"""Unit tests for GitHubProvider, PR deduplication, and token resolution."""

import json
import os
import unittest
from unittest.mock import MagicMock, patch
from looker_kc_sync.clients.git_provider import GitHubProvider
from looker_kc_sync.orchestrator import _expand_env


class TestGitProviderAndEnv(unittest.TestCase):

    def test_expand_env(self):
        data = {
            "project": "${TEST_PROJECT_ENV:-default-project}",
            "nested": {
                "branch": "${TEST_BRANCH_ENV:-main}",
            },
            "list_items": ["${TEST_ITEM:-item1}", "static"],
        }
        res = _expand_env(data)
        self.assertEqual(res["project"], "default-project")
        self.assertEqual(res["nested"]["branch"], "main")
        self.assertEqual(res["list_items"], ["item1", "static"])

    def test_github_token_env_resolution(self):
        with patch.dict(os.environ, {"GITHUB_TOKEN": "test_token_123"}, clear=True):
            provider = GitHubProvider(repo_slug="org/repo")
            self.assertEqual(provider.token, "test_token_123")

    def test_github_token_secret_manager_resolution(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("pathlib.Path.exists", return_value=False):
                with patch("google.cloud.secretmanager.SecretManagerServiceClient") as mock_sm:
                    mock_client = MagicMock()
                    mock_resp = MagicMock()
                    mock_resp.payload.data.decode.return_value = "secret_manager_token_456"
                    mock_client.access_secret_version.return_value = mock_resp
                    mock_sm.return_value = mock_client

                    provider = GitHubProvider(repo_slug="org/repo", project_id="test-proj")
                    self.assertEqual(provider.token, "secret_manager_token_456")
                    mock_client.access_secret_version.assert_called_once_with(
                        request={"name": "projects/test-proj/secrets/GITHUB_TOKEN/versions/latest"}
                    )

    def test_token_redaction(self):
        provider = GitHubProvider(repo_slug="org/repo", token="super_secret_pat_789")
        raw_msg = "Error cloning https://x-access-token:super_secret_pat_789@github.com/org/repo"
        redacted = provider._redact(raw_msg)
        self.assertNotIn("super_secret_pat_789", redacted)
        self.assertIn("[REDACTED_TOKEN]", redacted)

    def test_secure_git_run_uses_env_auth(self):
        provider = GitHubProvider(repo_slug="org/repo", token="super_secret_pat_789")
        with patch("subprocess.run") as mock_run:
            mock_res = MagicMock()
            mock_res.returncode = 0
            mock_run.return_value = mock_res

            provider._run_git(["git", "status"])
            mock_run.assert_called_once()
            called_args, called_kwargs = mock_run.call_args
            self.assertEqual(called_args[0], ["git", "status"])
            # Verify credentials are in environment, not args
            self.assertNotIn("super_secret_pat_789", str(called_args[0]))
            called_env = called_kwargs["env"]
            self.assertEqual(called_env["GIT_CONFIG_KEY_0"], "http.extraHeader")
            self.assertIn("basic ", called_env["GIT_CONFIG_VALUE_0"])

    def test_find_and_reuse_open_sync_pr(self):
        """Verifies GitOps PR deduplication reuses an existing open kc-sync/* PR branch and PATCHes it."""
        provider = GitHubProvider(repo_slug="org/repo", token="test_pat_token")

        open_prs_payload = [
            {
                "number": 42,
                "html_url": "https://github.com/org/repo/pull/42",
                "head": {"ref": "kc-sync/update-20260920-000000"},
            }
        ]

        mock_get_resp = MagicMock()
        mock_get_resp.read.return_value = json.dumps(open_prs_payload).encode("utf-8")
        mock_get_resp.__enter__.return_value = mock_get_resp

        mock_patch_resp = MagicMock()
        mock_patch_resp.read.return_value = json.dumps({"html_url": "https://github.com/org/repo/pull/42"}).encode("utf-8")
        mock_patch_resp.__enter__.return_value = mock_patch_resp

        def urlopen_side_effect(req, *args, **kwargs):
            if req.get_method() == "GET":
                return mock_get_resp
            elif req.get_method() == "PATCH":
                return mock_patch_resp
            raise AssertionError(f"Unexpected HTTP method: {req.get_method()}")

        def git_side_effect(args, **kwargs):
            res = MagicMock()
            res.returncode = 0
            res.stdout = " views/base/orders.base.view.lkml | 2 +-\n" if "--stat" in args else "+hidden: no"
            return res

        with patch("urllib.request.urlopen", side_effect=urlopen_side_effect) as mock_urlopen:
            with patch.object(provider, "_run_git", side_effect=git_side_effect) as mock_git:
                res = provider.create_pull_request(
                    lookml_files={"views/base/orders.base.view.lkml": "view: orders {}"},
                    base_branch="master",
                )

                self.assertFalse(res["pr_created"])
                self.assertTrue(res["pr_updated"])
                self.assertEqual(res["branch"], "kc-sync/update-20260920-000000")
                self.assertEqual(res["pr_url"], "https://github.com/org/repo/pull/42")

                # Verify force push to the reused branch was executed
                git_calls = [c.args[0] for c in mock_git.call_args_list]
                self.assertIn(
                    ["git", "push", "--force", "-u", "origin", "kc-sync/update-20260920-000000"],
                    git_calls,
                )


if __name__ == "__main__":
    unittest.main()
