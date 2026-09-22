"""Unit tests for LookerClient with official SDK and CLI fallback."""

import unittest
from unittest.mock import MagicMock, patch

from looker_sdk import models40
from looker_kc_sync.clients.looker import LookerClient


class TestLookerClient(unittest.TestCase):

    def test_sdk_validate_project_valid(self):
        mock_sdk = MagicMock()
        mock_val = MagicMock()
        mock_val.errors = []
        mock_sdk.validate_project.return_value = mock_val

        client = LookerClient(sdk=mock_sdk)
        is_valid, msg = client.validate_project("test_proj")

        self.assertTrue(is_valid)
        self.assertIn("Project is valid", msg)
        mock_sdk.validate_project.assert_called_once_with("test_proj")

    def test_sdk_validate_project_with_errors(self):
        mock_sdk = MagicMock()
        mock_err = models40.ProjectError(
            severity="ERROR",
            file_path="views/orders.view.lkml",
            line_number=42,
            message="Invalid field reference ${orders.unknown_field}",
        )
        mock_val = MagicMock()
        mock_val.errors = [mock_err]
        mock_sdk.validate_project.return_value = mock_val

        client = LookerClient(sdk=mock_sdk)
        is_valid, msg = client.validate_project("test_proj")

        self.assertFalse(is_valid)
        self.assertIn("[ERROR]", msg)
        self.assertIn("views/orders.view.lkml:42", msg)
        self.assertIn("Invalid field reference", msg)

    def test_sdk_list_files(self):
        mock_sdk = MagicMock()
        f1 = models40.ProjectFile(id="views/base/orders.base.view.lkml")
        f2 = models40.ProjectFile(id="models/retail.model.lkml")
        mock_sdk.all_project_files.return_value = [f1, f2]

        client = LookerClient(sdk=mock_sdk)
        files = client.list_files("test_proj")

        self.assertEqual(files, ["views/base/orders.base.view.lkml", "models/retail.model.lkml"])
        mock_sdk.all_project_files.assert_called_once_with("test_proj")

    def test_sdk_ensure_dev_mode(self):
        mock_sdk = MagicMock()
        client = LookerClient(sdk=mock_sdk)
        client.ensure_dev_mode()
        mock_sdk.update_session.assert_called_once()
        call_args = mock_sdk.update_session.call_args[0][0]
        self.assertEqual(call_args.workspace_id, "dev")

    def test_sdk_checkout_branch(self):
        mock_sdk = MagicMock()
        client = LookerClient(sdk=mock_sdk)
        client.checkout_branch("test_proj", "feature/sync")
        mock_sdk.update_git_branch.assert_called_once()
        call_proj, call_branch = mock_sdk.update_git_branch.call_args[0]
        self.assertEqual(call_proj, "test_proj")
        self.assertEqual(call_branch.name, "feature/sync")


if __name__ == "__main__":
    unittest.main()
