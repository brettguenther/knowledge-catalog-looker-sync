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


if __name__ == "__main__":
    unittest.main()
