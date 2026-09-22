"""Unit tests for SyncOrchestrator, deployment invariants, and error isolation."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

from looker_kc_sync.models.catalog import CatalogEntry, SchemaColumn
from looker_kc_sync.orchestrator import SyncOrchestrator


class TestSyncOrchestrator(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.out_dir = Path(self.temp_dir.name) / "output"
        self.config_file = Path(self.temp_dir.name) / "sync_config.yaml"

        cfg_content = {
            "project_id": "test-project",
            "location": "us-central1",
            "dataset_id": "test_dataset",
            "looker_project_id": "test_looker_project",
            "connection_name": "test_conn",
            "model_name": "test_model",
            "active_profile": "config/profiles/semantic_curation.yaml",
            "output_dir": str(self.out_dir),
        }
        self.config_file.write_text(yaml.dump(cfg_content))

    def tearDown(self):
        self.temp_dir.cleanup()

    @patch("looker_kc_sync.orchestrator.LookerClient")
    @patch("looker_kc_sync.orchestrator.DataplexCatalogClient")
    def test_deploy_only_base_layer_invariant(self, mock_dpx_cls, mock_looker_cls):
        """CRITICAL INVARIANT: Automated deployment must ONLY touch machine-managed base views and base explores."""
        mock_dpx = MagicMock()
        mock_dpx_cls.return_value = mock_dpx

        mock_looker = MagicMock()
        mock_looker.validate_project.return_value = (True, "Project is valid")
        mock_looker_cls.return_value = mock_looker

        entry = CatalogEntry(
            resource_name="//dataplex/test/orders",
            entry_id="orders",
            display_name="orders",
            bigquery_table="test-project.test_dataset.orders",
            columns=[SchemaColumn(name="order_id", data_type="STRING")],
        )
        mock_dpx.get_dataset_entries.return_value = [entry]

        orchestrator = SyncOrchestrator(str(self.config_file))
        results = orchestrator.run(deploy=True, scaffold=False)

        self.assertEqual(results["tables_processed"], 1)
        self.assertIn("orders", results["explores_generated"])
        self.assertTrue(results["looker_deployed"])

        # Check deployed files: only views/base/*.base.view.lkml and explores/base/*.base.explore.lkml
        created_files = [call.kwargs.get("file_path") or call.args[1] for call in mock_looker.create_or_update_file.call_args_list]
        self.assertIn("views/base/orders.base.view.lkml", created_files)
        self.assertIn("explores/base/orders.base.explore.lkml", created_files)
        for f in created_files:
            is_base_view = f.startswith("views/base/") and f.endswith(".base.view.lkml")
            is_base_explore = f.startswith("explores/base/") and f.endswith(".base.explore.lkml")
            self.assertTrue(is_base_view or is_base_explore, f"Automated deploy wrote forbidden non-base file: {f}")

        # Verify curated views and model files were NOT deployed
        self.assertNotIn("views/curated/orders.view.lkml", created_files)
        self.assertNotIn("models/test_model.model.lkml", created_files)

        # Verify curated views and model files were NOT written to disk when scaffold=False
        self.assertFalse((self.out_dir / "views" / "curated" / "orders.view.lkml").exists())
        self.assertFalse((self.out_dir / "models" / "test_model.model.lkml").exists())

    @patch("looker_kc_sync.orchestrator.LookerClient")
    @patch("looker_kc_sync.orchestrator.DataplexCatalogClient")
    def test_scaffold_flag_generates_local_files_without_remote_deploy(self, mock_dpx_cls, mock_looker_cls):
        mock_dpx = MagicMock()
        mock_dpx_cls.return_value = mock_dpx

        mock_looker = MagicMock()
        mock_looker.validate_project.return_value = (True, "Project is valid")
        mock_looker_cls.return_value = mock_looker

        entry = CatalogEntry(
            resource_name="//dataplex/test/orders",
            entry_id="orders",
            display_name="orders",
            bigquery_table="test-project.test_dataset.orders",
            columns=[SchemaColumn(name="order_id", data_type="STRING")],
        )
        mock_dpx.get_dataset_entries.return_value = [entry]

        orchestrator = SyncOrchestrator(str(self.config_file))
        results = orchestrator.run(deploy=False, scaffold=True)

        # Verify local scaffold files exist
        self.assertTrue((self.out_dir / "views" / "curated" / "orders.view.lkml").exists())
        self.assertTrue((self.out_dir / "explores" / "base" / "orders.base.explore.lkml").exists())
        self.assertTrue((self.out_dir / "models" / f"{orchestrator.config.model_name}.model.lkml").exists())

        # Verify remote deployment was NOT called
        mock_looker.create_or_update_file.assert_not_called()

    @patch("looker_kc_sync.orchestrator.DataplexCatalogClient")
    def test_error_isolation_partial_failure(self, mock_dpx_cls):
        """Verifies that a bad table entry does not crash the sync pipeline for valid entries."""
        mock_dpx = MagicMock()
        mock_dpx_cls.return_value = mock_dpx

        valid_entry = CatalogEntry(
            resource_name="//dataplex/test/valid",
            entry_id="valid_table",
            display_name="valid_table",
            bigquery_table="test-project.test_dataset.valid_table",
            columns=[SchemaColumn(name="id", data_type="STRING")],
        )
        broken_entry = CatalogEntry(
            resource_name="//dataplex/test/broken",
            entry_id="broken_table",
            display_name="broken_table",
            bigquery_table="test-project.test_dataset.broken_table",
            columns=[],
        )
        mock_dpx.get_dataset_entries.return_value = [broken_entry, valid_entry]

        orchestrator = SyncOrchestrator(str(self.config_file))
        orig_map = orchestrator.mapper.map_entry_to_view

        def side_effect(e):
            if e.entry_id == "broken_table":
                raise ValueError("Simulated malformed entry metadata")
            return orig_map(e)

        with patch.object(orchestrator.mapper, "map_entry_to_view", side_effect=side_effect):
            results = orchestrator.run(deploy=False, scaffold=False)

        # The valid table succeeded
        self.assertEqual(results["tables_processed"], 1)
        self.assertIn("valid_table", results["views_generated"])
        # The broken table was recorded in errors without crashing the run
        self.assertEqual(len(results["errors"]), 1)
        self.assertIn("broken_table", results["errors"][0])

    def test_config_missing_raises_file_not_found(self):
        """Verify that a missing config file fails loudly instead of falling back to .example."""
        from looker_kc_sync.orchestrator import load_sync_config
        with self.assertRaises(FileNotFoundError) as ctx:
            load_sync_config("nonexistent_config_12345.yaml")
        self.assertIn("nonexistent_config_12345.yaml", str(ctx.exception))

    def test_dependency_injection_catalog_and_deployer(self):
        """Verify protocol-driven dependency injection of custom CatalogSource and LookMLDeployer."""
        fake_catalog = MagicMock()
        entry = CatalogEntry(
            resource_name="//dataplex/fake/t1",
            entry_id="t1",
            display_name="t1",
            bigquery_table="proj.ds.t1",
            columns=[SchemaColumn(name="id", data_type="STRING")],
        )
        fake_catalog.get_dataset_entries.return_value = [entry]

        fake_deployer = MagicMock()
        fake_deployer.validate_project.return_value = (True, "Valid")

        orchestrator = SyncOrchestrator(
            str(self.config_file),
            catalog_client=fake_catalog,
            looker_client=fake_deployer,
        )

        results = orchestrator.run(deploy=True, scaffold=False)
        self.assertEqual(results["tables_processed"], 1)
        self.assertTrue(results["looker_deployed"])
        fake_catalog.get_dataset_entries.assert_called_once()
        fake_deployer.ensure_dev_mode.assert_called_once()
        self.assertEqual(fake_deployer.create_or_update_file.call_count, 2)  # 1 base view + 1 base explore


if __name__ == "__main__":
    unittest.main()
