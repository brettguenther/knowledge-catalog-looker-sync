"""Unit tests for the Semantic Mapping Engine using standard unittest."""

import unittest
from pathlib import Path
import yaml

from looker_kc_sync.mapping.profile import MappingProfile
from looker_kc_sync.mapping.engine import SemanticMapper
from looker_kc_sync.mapping.transformers import title_case, parse_delimited_list, map_lookup_value
from looker_kc_sync.models.catalog import CatalogEntry, JoinRelationship, SchemaColumn


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
        self.assertFalse(view.fields_hidden_by_default)

        # Check fields_hidden_by_default override
        self.profile.fields_hidden_by_default = True
        view_hidden_default = mapper.map_entry_to_view(entry)
        self.assertTrue(view_hidden_default.fields_hidden_by_default)
        self.profile.fields_hidden_by_default = False

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

    def test_ai_data_documentation_description_fallback(self):
        """Verifies DATA_DOCUMENTATION overview and field descriptions populate missing descriptions."""
        mapper = SemanticMapper(self.profile)
        entry = CatalogEntry(
            resource_name="//dataplex/ai_doc",
            entry_id="fct_orders",
            display_name="fct_orders",
            description="",  # Empty native description
            bigquery_table="project.edg.fct_orders",
            columns=[
                SchemaColumn(name="sales_channel", data_type="STRING", description=""),
            ],
            table_aspects={
                "data-documentation": {
                    "overview": "This table stores comprehensive information about customer transactions."
                }
            },
            column_aspects={
                "sales_channel": {
                    "semantic-curation": {"status": "CERTIFIED"},
                    "data-documentation": {
                        "description": 'This column contains the method through which a sale was made, such as "InStore" or "Pick Up".'
                    },
                }
            },
        )
        view = mapper.map_entry_to_view(entry)
        self.assertEqual(
            view.description,
            "This table stores comprehensive information about customer transactions.",
        )
        dim_channel = next(d for d in view.dimensions if d.name == "sales_channel")
        self.assertIn("InStore", dim_channel.description)

    def test_partition_and_cluster_filter_generation_and_base_explore(self):
        """Verifies optional YAML config for partition/cluster filter fields and base explore generation with joins."""
        self.profile.auto_generate_partition_cluster_filters = True
        self.profile.always_filter_on_partition_key = True
        mapper = SemanticMapper(self.profile)

        fact_entry = CatalogEntry(
            resource_name="//dataplex/fct_sales_daily",
            entry_id="fct_sales_daily",
            display_name="fct_sales_daily",
            description="Daily sales fact",
            bigquery_table="project.retail.fct_sales_daily",
            columns=[
                SchemaColumn(name="sale_date", data_type="DATE"),
                SchemaColumn(name="store_id", data_type="STRING"),
                SchemaColumn(name="net_sales_amount", data_type="FLOAT"),
            ],
            partition_fields=["sale_date"],
            cluster_fields=["store_id"],
            joins=[
                JoinRelationship(
                    source_table="fct_sales_daily",
                    target_table="dim_store",
                    join_keys=[("store_id", "store_id")],
                )
            ],
            column_aspects={
                "net_sales_amount": {"semantic-curation": {"status": "CERTIFIED"}},
            },
        )
        dim_entry = CatalogEntry(
            resource_name="//dataplex/dim_store",
            entry_id="dim_store",
            display_name="dim_store",
            bigquery_table="project.retail.dim_store",
            columns=[SchemaColumn(name="store_id", data_type="STRING")],
        )

        fact_view = mapper.map_entry_to_view(fact_entry)
        dim_view = mapper.map_entry_to_view(dim_entry)

        # Verify dedicated filter fields were created for sale_date (partition) and store_id (cluster)
        filter_names = [f.name for f in fact_view.filters]
        self.assertIn("sale_date_filter", filter_names)
        self.assertIn("store_id_filter", filter_names)

        sale_filter = next(f for f in fact_view.filters if f.name == "sale_date_filter")
        self.assertEqual(sale_filter.type, "date")
        self.assertIsNone(sale_filter.suggest_dimension)
        self.assertEqual(sale_filter.sql, "{% condition sale_date_filter %} ${sale_date} {% endcondition %}")

        store_filter = next(f for f in fact_view.filters if f.name == "store_id_filter")
        self.assertEqual(store_filter.type, "string")
        self.assertEqual(store_filter.suggest_dimension, "store_id")
        self.assertEqual(store_filter.sql, "{% condition store_id_filter %} ${store_id} {% endcondition %}")

        # Verify partition & cluster dimensions were unhidden and tagged
        dg_sale = next(dg for dg in fact_view.dimension_groups if dg.name == "sale")
        self.assertFalse(dg_sale.hidden)
        self.assertIn("partition_key", dg_sale.tags)

        dim_store_id = next(d for d in fact_view.dimensions if d.name == "store_id")
        self.assertFalse(dim_store_id.hidden)
        self.assertIn("cluster_key", dim_store_id.tags)

        # Verify base explore generation with join and partition always_filter
        explore = mapper.map_entry_to_explore(
            fact_entry,
            fact_view,
            views_by_name={"fct_sales_daily": fact_view, "dim_store": dim_view},
        )
        self.assertEqual(explore.name, "fct_sales_daily")
        self.assertEqual(len(explore.joins), 1)
        self.assertEqual(explore.joins[0].name, "dim_store")
        self.assertEqual(explore.joins[0].sql_on, "${fct_sales_daily.store_id} = ${dim_store.store_id}")
        self.assertEqual(explore.always_filter.get("fct_sales_daily.sale_date"), "30 days")

    def test_custom_heuristics_in_profile(self):
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

        pk_dim = next(d for d in view.dimensions if d.name == "pk_custom_table")
        self.assertTrue(pk_dim.primary_key)

        measure = next(m for m in view.measures if m.name == "agg_custom_metric_val")
        self.assertEqual(measure.name, "agg_custom_metric_val")
        self.assertEqual(measure.type, "sum")

    def test_suggestions_filtering_and_comprehensiveness(self):
        mapper = SemanticMapper(self.profile)
        entry = CatalogEntry(
            resource_name="//dataplex/suggestions_test",
            entry_id="test_products",
            display_name="products",
            bigquery_table="project.dataset.products",
            columns=[
                SchemaColumn(name="category_small", data_type="STRING"),
                SchemaColumn(name="category_large", data_type="STRING"),
                SchemaColumn(name="status_tagged", data_type="STRING"),
            ],
            table_aspects={},
            column_aspects={
                "category_small": {
                    "semantic-curation": {
                        "status": "CERTIFIED",
                        "allowed_values": ["Apparel", "Footwear", "Electronics", "Home & Kitchen", "Accessories"],
                    }
                },
                "category_large": {
                    "semantic-curation": {
                        "status": "CERTIFIED",
                        "allowed_values": [
                            "Val_1", "Val_2", "Val_3", "Val_4", "Val_5",
                            "Val_6", "Val_7", "Val_8", "Val_9", "Val_10",
                            "Val_11", "Val_12",
                        ],
                    }
                },
                "status_tagged": {
                    "semantic-curation": {
                        "status": "CERTIFIED",
                        "allowed_values": ["OPEN", "CLOSED"],
                        "governance_tags": ["certified", "exhaustive"],
                    }
                },
            },
        )

        # 1. Default threshold (max_suggestions_limit = 10)
        view = mapper.map_entry_to_view(entry)
        dim_small = next(d for d in view.dimensions if d.name == "category_small")
        self.assertEqual(len(dim_small.suggestions), 5)
        self.assertEqual(dim_small.suggestions[0], "Apparel")

        dim_large = next(d for d in view.dimensions if d.name == "category_large")
        self.assertEqual(dim_large.suggestions, [])  # 12 items >= 10 -> omitted

        # 2. Custom threshold (max_suggestions_limit = 5)
        self.profile.max_suggestions_limit = 5
        view_custom = mapper.map_entry_to_view(entry)
        dim_small_custom = next(d for d in view_custom.dimensions if d.name == "category_small")
        self.assertEqual(dim_small_custom.suggestions, [])  # 5 items >= 5 -> omitted

        # 3. Tag-based comprehensiveness requirement
        self.profile.max_suggestions_limit = 10
        self.profile.suggestions_require_comprehensive_tag = True
        view_tagged = mapper.map_entry_to_view(entry)
        dim_small_untagged = next(d for d in view_tagged.dimensions if d.name == "category_small")
        self.assertEqual(dim_small_untagged.suggestions, [])  # No comprehensive tag -> omitted

        dim_status_tagged = next(d for d in view_tagged.dimensions if d.name == "status_tagged")
        self.assertEqual(dim_status_tagged.suggestions, ["OPEN", "CLOSED"])  # Has 'exhaustive' tag -> included

    def test_explore_policy_strategies_and_heuristics(self):
        """Tests declarative ExplorePolicy strategies, tag matching, patterns, allowlists, and dimension table exclusion."""
        orders_entry = CatalogEntry(
            resource_name="//dataplex/orders",
            entry_id="orders",
            display_name="orders",
            bigquery_table="proj.ds.orders",
            table_aspects={
                "semantic-curation": {
                    "status": "CERTIFIED",
                    "governance_tags": ["certified", "core_bi"],
                }
            },
            joins=[
                JoinRelationship(
                    source_table="orders",
                    target_table="order_items",
                    join_keys=[("order_id", "order_id")],
                )
            ],
        )
        items_entry = CatalogEntry(
            resource_name="//dataplex/order_items",
            entry_id="order_items",
            display_name="order_items",
            bigquery_table="proj.ds.order_items",
            table_aspects={
                "semantic-curation": {
                    "status": "CERTIFIED",
                    "governance_tags": ["certified", "sales"],
                }
            },
        )
        dim_store_entry = CatalogEntry(
            resource_name="//dataplex/dim_store",
            entry_id="dim_store",
            display_name="dim_store",
            bigquery_table="proj.ds.dim_store",
            table_aspects={
                "semantic-curation": {
                    "status": "CERTIFIED",
                    "governance_tags": ["certified", "core_bi"],
                }
            },
        )
        fct_sales_entry = CatalogEntry(
            resource_name="//dataplex/fct_sales",
            entry_id="fct_sales",
            display_name="fct_sales",
            bigquery_table="proj.ds.fct_sales",
            table_aspects={},
        )
        all_entries = [orders_entry, items_entry, dim_store_entry, fct_sales_entry]

        # 1. Strategy: "tagged" (Default: required_tags=["core_bi", "explore", "fact"])
        self.profile.explore_policy.enabled = True
        self.profile.explore_policy.strategy = "tagged"
        self.profile.explore_policy.required_tags = ["core_bi", "explore", "fact"]
        self.profile.explore_policy.table_allowlist = []
        self.profile.explore_policy.exclude_dimension_tables = True
        mapper = SemanticMapper(self.profile)

        self.assertTrue(mapper.should_generate_explore(orders_entry, all_entries))
        self.assertFalse(mapper.should_generate_explore(items_entry, all_entries))  # 'sales' != 'core_bi'
        # dim_store has 'core_bi' but is excluded because name starts with dim_
        self.assertFalse(mapper.should_generate_explore(dim_store_entry, all_entries))

        # 2. Strategy: "allowlist"
        self.profile.explore_policy.strategy = "allowlist"
        self.profile.explore_policy.table_allowlist = ["order_items"]
        self.assertFalse(mapper.should_generate_explore(orders_entry, all_entries))
        self.assertTrue(mapper.should_generate_explore(items_entry, all_entries))

        # 3. Strategy: "patterns"
        self.profile.explore_policy.strategy = "patterns"
        self.profile.explore_policy.table_allowlist = []
        self.profile.explore_policy.table_patterns = ["fct_*", "orders"]
        self.assertTrue(mapper.should_generate_explore(orders_entry, all_entries))
        self.assertTrue(mapper.should_generate_explore(fct_sales_entry, all_entries))
        self.assertFalse(mapper.should_generate_explore(items_entry, all_entries))

        # 4. Strategy: "root_only"
        self.profile.explore_policy.strategy = "root_only"
        self.profile.explore_policy.table_allowlist = []
        # orders has outgoing joins -> root -> True
        self.assertTrue(mapper.should_generate_explore(orders_entry, all_entries))
        # items_entry has no outgoing joins and is a target table of orders -> not a root -> False
        self.assertFalse(mapper.should_generate_explore(items_entry, all_entries))

        # 5. Dimension table exclusion heuristics (name prefix, dimension tag, join target)
        self.profile.explore_policy.strategy = "all"
        self.profile.explore_policy.exclude_dimension_tables = True
        # dim_store is excluded due to dim_ prefix
        self.assertFalse(mapper.should_generate_explore(dim_store_entry, all_entries))
        # items_entry is excluded because it is purely a join target with no outgoing joins
        self.assertFalse(mapper.should_generate_explore(items_entry, all_entries))

        # Explicit allowlist overrides dimension exclusion
        self.profile.explore_policy.table_allowlist = ["dim_store"]
        self.assertTrue(mapper.should_generate_explore(dim_store_entry, all_entries))

        # 6. Policy disabled -> all False
        self.profile.explore_policy.enabled = False
        self.assertFalse(mapper.should_generate_explore(orders_entry, all_entries))
        self.assertFalse(mapper.should_generate_explore(dim_store_entry, all_entries))


if __name__ == "__main__":
    unittest.main()
