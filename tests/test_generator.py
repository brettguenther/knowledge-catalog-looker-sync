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
            fields_hidden_by_default=True,
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

        # Render base view
        rendered = self.generator.render_base_view(view)
        self.assertIn("fields_hidden_by_default: yes", rendered)
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


if __name__ == "__main__":
    unittest.main()
