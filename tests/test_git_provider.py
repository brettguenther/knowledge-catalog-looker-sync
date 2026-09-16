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



if __name__ == "__main__":
    unittest.main()
