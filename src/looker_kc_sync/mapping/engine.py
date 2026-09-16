"""Semantic Mapping Engine: Maps Catalog Entries to Normalized LookML Views."""

from typing import Any, Dict, List, Optional
from looker_kc_sync.mapping.profile import AttributeMapping, MappingProfile, TagMapping
from looker_kc_sync.mapping.rules import (
    evaluate_certification_rule,
    evaluate_exclusion_rule,
    resolve_path_value,
)
from looker_kc_sync.mapping.transformers import (
    map_lookup_value,
    parse_delimited_list,
    title_case,
)
from looker_kc_sync.models.catalog import CatalogEntry, SchemaColumn
from looker_kc_sync.models.lookml import LookMLDimension, LookMLMeasure, LookMLView


DATA_TYPE_MAP = {
    "STRING": "string",
    "INTEGER": "number",
    "INT64": "number",
    "FLOAT": "number",
    "FLOAT64": "number",
    "NUMERIC": "number",
    "BIGNUMERIC": "number",
    "BOOLEAN": "yesno",
    "BOOL": "yesno",
    "TIMESTAMP": "date_time",
    "DATETIME": "date_time",
    "DATE": "date_date",
}


class SemanticMapper:
    """Transforms raw Dataplex catalog entries and aspects into normalized LookML models."""

    def __init__(self, profile: MappingProfile):
        self.profile = profile

    def _resolve_attribute(
        self,
        mapping: Optional[AttributeMapping],
        aspect_data: Dict[str, Any],
        column: SchemaColumn,
    ) -> Optional[Any]:
        """Resolves a field parameter through the mapping's source cascade."""
        if not mapping or not mapping.sources:
            return None

        for src in mapping.sources:
            if src.path:
                val = resolve_path_value(aspect_data, src.path)
                if val is not None and val != "":
                    if mapping.lookup_map:
                        return map_lookup_value(val, mapping.lookup_map)
                    if src.type in ("list", "delimited_string"):
                        return parse_delimited_list(val, src.delimiter)
                    return val

            if src.transform:
                if src.transform == "title_case(column_name)":
                    return title_case(column.name)

        return None

    def _resolve_tags(
        self,
        tag_mapping: Optional[TagMapping],
        aspect_data: Dict[str, Any],
    ) -> List[str]:
        """Resolves static and dynamic tags for a field."""
        tags = set()
        if not tag_mapping:
            return []

        for st in tag_mapping.static_tags:
            tags.add(st)

        for dt in tag_mapping.dynamic_tags:
            val = resolve_path_value(aspect_data, dt.path)
            if val:
                if isinstance(val, (list, tuple)):
                    for item in val:
                        tags.add(f"{dt.prefix}{item}")
                else:
                    tags.add(f"{dt.prefix}{val}")

        return sorted(list(tags))

    def map_entry_to_view(self, entry: CatalogEntry) -> LookMLView:
        """Transforms a CatalogEntry into a governed LookMLView."""
        view_name = entry.display_name.lower().replace("-", "_")
        base_view_name = f"{view_name}_base"

        # Check table-level aspects
        table_desc = entry.description or ""
        table_tags = ["certified"] if evaluate_certification_rule(entry.table_aspects, self.profile.certification_rule) else []

        for k, v in entry.table_aspects.items():
            if isinstance(v, dict):
                if "business_description" in v and v["business_description"]:
                    table_desc = v["business_description"]
                if "governance_tags" in v and isinstance(v["governance_tags"], list):
                    table_tags.extend(v["governance_tags"])

        dimensions: List[LookMLDimension] = []
        measures: List[LookMLMeasure] = []

        for col in entry.columns:
            col_aspects = entry.column_aspects.get(col.name, {})

            # Check exclusions (e.g. policy tags)
            excluded = False
            for excl in self.profile.exclusions:
                if evaluate_exclusion_rule(col_aspects, excl):
                    excluded = True
                    break
            if excluded:
                continue

            # Check certification status (hidden: no vs hidden by default)
            is_certified = evaluate_certification_rule(col_aspects, self.profile.certification_rule)

            # Resolve parameters via profile cascades
            label = self._resolve_attribute(self.profile.field_mappings.label, col_aspects, col)
            description = self._resolve_attribute(self.profile.field_mappings.description, col_aspects, col) or col.description
            synonyms = self._resolve_attribute(self.profile.field_mappings.synonyms, col_aspects, col) or []
            tags = self._resolve_tags(self.profile.field_mappings.tags, col_aspects)
            suggestions = self._resolve_attribute(self.profile.field_mappings.suggestions, col_aspects, col) or []
            format_name = self._resolve_attribute(self.profile.field_mappings.value_format_name, col_aspects, col)

            dim_type = DATA_TYPE_MAP.get(col.data_type.upper(), "string")
            is_pk = (col.name.lower() == f"{view_name}_id") or (col.name.lower() == "id") or ("primary_key" in tags)

            dim = LookMLDimension(
                name=col.name,
                type=dim_type,
                sql=f"${{TABLE}}.{col.name}",
                primary_key=is_pk,
                hidden=not is_certified,
                label=label if is_certified else None,
                description=description if is_certified else None,
                synonyms=synonyms if is_certified else [],
                tags=tags if is_certified else [],
                suggestions=suggestions if is_certified else [],
                value_format_name=format_name if is_certified else None,
            )
            dimensions.append(dim)

            # Measure generation for certified numeric KPI fields (opt-in via profile)
            if self.profile.auto_generate_kpi_measures and is_certified and dim_type == "number" and not is_pk:
                if any(kpi_word in col.name.lower() for kpi_word in ["amount", "price", "revenue", "cost", "total", "sales"]):
                    measure_name = f"total_{col.name}"
                    m_label = f"Total {label}" if label else title_case(measure_name)
                    measures.append(
                        LookMLMeasure(
                            name=measure_name,
                            type="sum",
                            sql=f"${{TABLE}}.{col.name}",
                            hidden=False,
                            label=m_label,
                            description=f"Sum of {label or col.name}.",
                            synonyms=[f"total {s}" for s in synonyms] if synonyms else [measure_name.replace('_', ' ')],
                            tags=["certified", "kpi"],
                            value_format_name=format_name,
                        )
                    )

        return LookMLView(
            view_name=view_name,
            base_view_name=base_view_name,
            sql_table_name=f"`{entry.bigquery_table}`",
            fields_hidden_by_default=True,
            extension_required=True,
            description=table_desc,
            tags=sorted(list(set(table_tags))),
            dimensions=dimensions,
            measures=measures,
            source_entry=entry.resource_name,
        )
