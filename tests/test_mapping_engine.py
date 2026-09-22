"""Unit tests for the Semantic Mapping Engine using standard unittest."""

import unittest
from pathlib import Path
import yaml

from looker_kc_sync.mapping.profile import MappingProfile
from looker_kc_sync.mapping.engine import SemanticMapper
from looker_kc_sync.mapping.transformers import title_case, parse_delimited_list, map_lookup_value
from looker_kc_sync.models.catalog import CatalogEntry, SchemaColumn


class TestSemanticMappingEngine(unittest.TestCase):

    def setUp(self):
        profile_path = Path("config/profiles/semantic_curation.yaml")
        with open(profile_path) as f:
            data = yaml.safe_load(f)
        self.profile = MappingProfile(**data)

    def test_transformers(self):
        self.assertEqual(title_case("order_amount_usd"), "Order Amount Usd")
        self.assertEqual(parse_delimited_list("a, b, c"), ["a", "b", "c"])
        self.assertEqual(parse_delimited_list(["x", "y"]), ["x", "y"])
        self.assertEqual(map_lookup_value("usd", {"usd": "usd", "currency": "usd"}), "usd")
        self.assertEqual(map_lookup_value("percent", {"percent": "percent_2"}), "percent_2")

    def test_semantic_mapper_certification(self):
        mapper = SemanticMapper(self.profile)
        entry = CatalogEntry(
            resource_name="//dataplex/test",
            entry_id="test_orders",
            display_name="orders",
            description="Test orders table",
            bigquery_table="project.dataset.test_orders",
            columns=[
                SchemaColumn(name="order_id", data_type="STRING"),
                SchemaColumn(name="raw_etl_col", data_type="STRING"),
                SchemaColumn(name="amount", data_type="FLOAT"),
            ],
            table_aspects={},
            column_aspects={
                "order_id": {
                    "semantic-curation": {
                        "status": "CERTIFIED",
                        "business_label": "Order Identifier",
                        "governance_tags": ["certified", "primary_key"],
                    }
                },
                "amount": {
                    "semantic-curation": {
                        "status": "CERTIFIED",
                        "business_label": "Total Amount",
                        "format_pattern": "usd",
                        "synonyms": ["revenue", "sales"],
                    }
                },
            },
        )

        view = mapper.map_entry_to_view(entry)
        self.assertEqual(view.base_view_name, "orders_base")
        self.assertTrue(view.fields_hidden_by_default)

        # Check order_id dimension
        dim_id = next(d for d in view.dimensions if d.name == "order_id")
        self.assertFalse(dim_id.hidden)
        self.assertTrue(dim_id.primary_key)
        self.assertEqual(dim_id.label, "Order Identifier")

        # Check raw_etl_col (uncertified -> hidden)
        dim_raw = next(d for d in view.dimensions if d.name == "raw_etl_col")
        self.assertTrue(dim_raw.hidden)
        self.assertIsNone(dim_raw.label)

        # Check amount dimension & measure
        dim_amt = next(d for d in view.dimensions if d.name == "amount")
        self.assertFalse(dim_amt.hidden)
        self.assertEqual(dim_amt.value_format_name, "usd")
        self.assertEqual(dim_amt.synonyms, ["revenue", "sales"])

        # When auto_generate_kpi_measures is False (default), no measures are generated
        self.assertEqual(len(view.measures), 0)

        # When auto_generate_kpi_measures is True, KPI measures are generated
        self.profile.auto_generate_kpi_measures = True
        view_with_kpis = mapper.map_entry_to_view(entry)
        measure_amt = next(m for m in view_with_kpis.measures if m.name == "total_amount")
        self.assertEqual(measure_amt.type, "sum")
        self.assertFalse(measure_amt.hidden)
        self.assertEqual(measure_amt.value_format_name, "usd")

    def test_dimension_group_mapping(self):
        mapper = SemanticMapper(self.profile)
        entry = CatalogEntry(
            resource_name="//dataplex/test_dg",
            entry_id="test_orders",
            display_name="orders",
            bigquery_table="project.dataset.test_orders",
            columns=[
                SchemaColumn(name="order_id", data_type="STRING"),
                SchemaColumn(name="order_date", data_type="TIMESTAMP"),
                SchemaColumn(name="shipment_date", data_type="DATE"),
            ],
            table_aspects={},
            column_aspects={
                "order_date": {
                    "semantic-curation": {
                        "status": "CERTIFIED",
                        "business_label": "Order Placed Date",
                        "business_description": "Timestamp when order was placed",
                        "synonyms": ["transaction time", "purchase date"],
                        "governance_tags": ["certified", "date"],
                    }
                },
                "shipment_date": {
                    "semantic-curation": {
                        "status": "CERTIFIED",
                        "business_label": "Shipment Date",
                    }
                },
            },
        )

        view = mapper.map_entry_to_view(entry)
        self.assertEqual(len(view.dimension_groups), 2)

        # 1. Check order_date mapped to dimension_group: order
        dg_order = next(dg for dg in view.dimension_groups if dg.name == "order")
        self.assertEqual(dg_order.type, "time")
        self.assertEqual(dg_order.datatype, "timestamp")
        self.assertEqual(dg_order.sql, "${TABLE}.order_date")
        self.assertFalse(dg_order.hidden)
        # Suffix " Date" stripped from label to prevent "Order Placed Date Date"
        self.assertEqual(dg_order.label, "Order Placed")
        self.assertEqual(dg_order.description, "Timestamp when order was placed")
        self.assertEqual(dg_order.synonyms, ["transaction time", "purchase date"])
        self.assertIn("date", dg_order.timeframes)
        self.assertIn("time", dg_order.timeframes)

        # 2. Check shipment_date mapped to dimension_group: shipment
        dg_ship = next(dg for dg in view.dimension_groups if dg.name == "shipment")
        self.assertEqual(dg_ship.type, "time")
        self.assertEqual(dg_ship.datatype, "date")
        self.assertEqual(dg_ship.sql, "${TABLE}.shipment_date")
        self.assertEqual(dg_ship.label, "Shipment")
        self.assertIn("date", dg_ship.timeframes)
        self.assertNotIn("time", dg_ship.timeframes)  # DATE timeframes omit time

        # Confirm temporal columns were NOT placed in scalar dimensions
        dim_names = [d.name for d in view.dimensions]
        self.assertNotIn("order_date", dim_names)
        self.assertNotIn("shipment_date", dim_names)

    def test_dimension_group_collision_and_fallback(self):
        # Test collision: table has both 'status' and 'status_date'
        mapper = SemanticMapper(self.profile)
        entry = CatalogEntry(
            resource_name="//dataplex/collision",
            entry_id="test_status",
            display_name="events",
            bigquery_table="project.dataset.events",
            columns=[
                SchemaColumn(name="status", data_type="STRING"),
                SchemaColumn(name="status_date", data_type="TIMESTAMP"),
            ],
            table_aspects={},
            column_aspects={},
        )
        view = mapper.map_entry_to_view(entry)
        # Stripping '_date' would collide with 'status', so it must preserve 'status_date'
        dg = next(dg for dg in view.dimension_groups if dg.sql == "${TABLE}.status_date")
        self.assertEqual(dg.name, "status_date")

        # Test use_dimension_groups = False fallback
        self.profile.use_dimension_groups = False
        view_scalar = mapper.map_entry_to_view(entry)
        self.assertEqual(len(view_scalar.dimension_groups), 0)
        dim_date = next(d for d in view_scalar.dimensions if d.name == "status_date")
        self.assertEqual(dim_date.type, "date_time")

    def test_custom_heuristics_in_profile(self):
        # Override heuristics on profile: custom KPI keyword and custom PK pattern
        self.profile.auto_generate_kpi_measures = True
        self.profile.kpi_measure_keywords = ["custom_metric"]
        self.profile.kpi_measure_prefix = "agg_"
        self.profile.primary_key_patterns = ["pk_{view_name}"]

        entry = CatalogEntry(
            resource_name="//dataplex/custom",
            entry_id="custom_table",
            display_name="custom_table",
            bigquery_table="project.dataset.custom_table",
            columns=[
                SchemaColumn(name="pk_custom_table", data_type="STRING"),
                SchemaColumn(name="custom_metric_val", data_type="FLOAT"),
            ],
            table_aspects={},
            column_aspects={
                "pk_custom_table": {"semantic-curation": {"status": "CERTIFIED"}},
                "custom_metric_val": {"semantic-curation": {"status": "CERTIFIED"}},
            },
        )
        mapper = SemanticMapper(self.profile)
        view = mapper.map_entry_to_view(entry)

        # Verify custom PK pattern was matched
        pk_dim = next(d for d in view.dimensions if d.name == "pk_custom_table")
        self.assertTrue(pk_dim.primary_key)

        # Verify custom KPI keyword and prefix were applied
        measure = next(m for m in view.measures if m.name == "agg_custom_metric_val")
        self.assertEqual(measure.name, "agg_custom_metric_val")
        self.assertEqual(measure.type, "sum")


if __name__ == "__main__":
    unittest.main()

