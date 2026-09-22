"""Unit tests for the LookML Generator and lkml validation using standard unittest."""

import unittest
from pathlib import Path
import lkml

from looker_kc_sync.generator.engine import LookMLGenerator
from looker_kc_sync.models.lookml import (
    LookMLDimension,
    LookMLDimensionGroup,
    LookMLMeasure,
    LookMLView,
)


class TestLookMLGenerator(unittest.TestCase):

    def setUp(self):
        self.generator = LookMLGenerator(Path("src/looker_kc_sync/generator/templates"))

    def test_generator_renders_valid_lookml(self):
        view = LookMLView(
            view_name="customers",
            base_view_name="customers_base",
            sql_table_name="`my_proj.my_ds.customers`",
            fields_hidden_by_default=False,
            dimensions=[
                LookMLDimension(
                    name="customer_id",
                    type="string",
                    sql="${TABLE}.customer_id",
                    primary_key=True,
                    hidden=False,
                    label="Customer Identifier",
                    synonyms=["client id", "user id"],
                    tags=["certified"],
                ),
                LookMLDimension(
                    name="internal_code",
                    type="string",
                    sql="${TABLE}.internal_code",
                    hidden=True,
                ),
            ],
            dimension_groups=[
                LookMLDimensionGroup(
                    name="created",
                    type="time",
                    timeframes=["raw", "time", "date", "week", "month", "quarter", "year"],
                    sql="${TABLE}.created_at",
                    datatype="timestamp",
                    hidden=False,
                    label="Customer Created",
                    description="When customer registered",
                    synonyms=["signup date", "registration time"],
                    tags=["certified", "date"],
                )
            ],
            measures=[
                LookMLMeasure(
                    name="count",
                    type="count",
                    sql="${customer_id}",
                    hidden=False,
                    label="Customer Count",
                )
            ],
        )

        # Render base view (standard idiomatic pattern: fields_hidden_by_default=False)
        rendered = self.generator.render_base_view(view)
        self.assertNotIn("fields_hidden_by_default: yes", rendered)
        self.assertNotIn("hidden: no", rendered)
        self.assertIn("hidden: yes", rendered)  # internal_code explicitly hidden
        self.assertIn('synonyms: ["client id", "user id"]', rendered)

        # Verify AST parser parses without exception
        parsed = lkml.load(rendered)
        self.assertEqual(len(parsed["views"]), 1)
        self.assertEqual(parsed["views"][0]["name"], "customers")

        # Render refinement
        refinement = self.generator.render_curated_refinement(view)
        parsed_refinement = lkml.load(refinement)
        self.assertEqual(parsed_refinement["views"][0]["name"], "+customers")

        # Render model
        model = self.generator.render_model("test_model", "test_conn", [view])
        parsed_model = lkml.load(model)
        self.assertEqual(parsed_model["connection"], "test_conn")

    def test_generator_renders_fields_hidden_by_default_override(self):
        view = LookMLView(
            view_name="customers",
            base_view_name="customers_base",
            sql_table_name="`my_proj.my_ds.customers`",
            fields_hidden_by_default=True,
            dimensions=[
                LookMLDimension(
                    name="customer_id",
                    type="string",
                    sql="${TABLE}.customer_id",
                    hidden=False,
                    label="Customer Identifier",
                ),
                LookMLDimension(
                    name="internal_code",
                    type="string",
                    sql="${TABLE}.internal_code",
                    hidden=True,
                ),
            ],
        )
        rendered = self.generator.render_base_view(view)
        self.assertIn("fields_hidden_by_default: yes", rendered)
        self.assertIn("hidden: no", rendered)  # customer_id explicitly exposed
        self.assertNotIn("hidden: yes", rendered)  # internal_code relies on view-level default

    def test_generator_handles_quotes_and_newlines(self):
        view = LookMLView(
            view_name="special_chars",
            base_view_name="special_chars_base",
            sql_table_name="`my_proj.my_ds.special_chars`",
            description='Table description with "double quotes" and\nmultiple lines',
            dimensions=[
                LookMLDimension(
                    name="complex_col",
                    type="string",
                    sql="${TABLE}.complex_col",
                    hidden=False,
                    label='Field "VIP" Label',
                    description='Steward note: "Special" character & quote handling\nSecond line',
                    synonyms=['alias "one"', "regular alias"],
                    suggestions=['Option "A"', 'Option "B"'],
                )
            ],
        )

        rendered = self.generator.render_base_view(view)
        # Verify it successfully parses in lkml with no syntax error
        self.assertIn('label: "Field \\"VIP\\" Label"', rendered)
        self.assertIn('"alias \\"one\\""', rendered)
        parsed = lkml.load(rendered)
        self.assertEqual(parsed["views"][0]["name"], "special_chars")
        dim = parsed["views"][0]["dimensions"][0]
        self.assertIn("VIP", dim["label"])
        self.assertIn("Special", dim["description"])

    def test_generator_idempotency_no_timestamp(self):
        view = LookMLView(
            view_name="orders",
            base_view_name="orders_base",
            sql_table_name="`my_proj.my_ds.orders`",
            source_entry="//dataplex/entries/orders",
            dimensions=[
                LookMLDimension(
                    name="order_id",
                    type="string",
                    sql="${TABLE}.order_id",
                    hidden=False,
                    label="Order ID",
                )
            ],
        )

        rendered_1 = self.generator.render_base_view(view)
        rendered_2 = self.generator.render_base_view(view)

        # Assert zero volatile timestamps in output
        self.assertNotIn("Last Synced", rendered_1)
        self.assertNotIn("timestamp", rendered_1.lower())
        # Assert byte-for-byte idempotency across repeated runs
        self.assertEqual(rendered_1, rendered_2)


if __name__ == "__main__":
    unittest.main()

