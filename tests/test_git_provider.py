"""Unit tests for GitHubProvider and token resolution."""

import os
import unittest
from unittest.mock import MagicMock, patch
from looker_kc_sync.clients.git_provider import GitHubProvider
from looker_kc_sync.orchestrator import _expand_env, SyncConfig


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


if __name__ == "__main__":
    unittest.main()

