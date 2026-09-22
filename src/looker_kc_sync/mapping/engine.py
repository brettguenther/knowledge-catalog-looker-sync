"""Semantic Mapping Engine: Maps Catalog Entries to Normalized LookML Views."""

from typing import Any, Dict, List, Optional, Set, Tuple
from looker_kc_sync.mapping.profile import AttributeMapping, MappingProfile, TagMapping
from looker_kc_sync.mapping.rules import (
    evaluate_certification_rule,
    evaluate_exclusion_rule,
    resolve_path_value,
)
from looker_kc_sync.mapping.transformers import (
    clean_dimension_group_label,
    derive_dimension_group_name,
    map_lookup_value,
    parse_delimited_list,
    title_case,
)
from looker_kc_sync.models.catalog import CatalogEntry, SchemaColumn
from looker_kc_sync.models.lookml import (
    LookMLDimension,
    LookMLDimensionGroup,
    LookMLMeasure,
    LookMLView,
)


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

    def _resolve_table_metadata(self, entry: CatalogEntry) -> Tuple[str, List[str]]:
        """Extracts table description and governance tags from catalog entry and aspects."""
        table_desc = entry.description or ""
        table_tags = ["certified"] if evaluate_certification_rule(entry.table_aspects, self.profile.certification_rule) else []

        for k, v in entry.table_aspects.items():
            if isinstance(v, dict):
                if "business_description" in v and v["business_description"]:
                    table_desc = v["business_description"]
                if "governance_tags" in v and isinstance(v["governance_tags"], list):
                    table_tags.extend(v["governance_tags"])

        return table_desc, sorted(list(set(table_tags)))

    def _resolve_column_metadata(
        self,
        col: SchemaColumn,
        col_aspects: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Resolves raw attributes from catalog aspects according to profile cascades."""
        label = self._resolve_attribute(self.profile.field_mappings.label, col_aspects, col)
        description = self._resolve_attribute(self.profile.field_mappings.description, col_aspects, col) or col.description
        synonyms = self._resolve_attribute(self.profile.field_mappings.synonyms, col_aspects, col) or []
        tags = self._resolve_tags(self.profile.field_mappings.tags, col_aspects)
        suggestions = self._resolve_attribute(self.profile.field_mappings.suggestions, col_aspects, col) or []
        format_name = self._resolve_attribute(self.profile.field_mappings.value_format_name, col_aspects, col)

        return {
            "label": label,
            "description": description,
            "synonyms": synonyms,
            "tags": tags,
            "suggestions": suggestions,
            "format_name": format_name,
        }

    def _is_primary_key(self, col_name: str, view_name: str, tags: List[str]) -> bool:
        """Determines if a column is a primary key using profile rules and tags."""
        col_lower = col_name.lower()
        if any(tag in tags for tag in self.profile.primary_key_tags):
            return True
        for pat in self.profile.primary_key_patterns:
            expanded_pat = pat.format(view_name=view_name).lower()
            if col_lower == expanded_pat:
                return True
        return False

    def _map_dimension_group(
        self,
        col: SchemaColumn,
        meta: Dict[str, Any],
        is_certified: bool,
        raw_type: str,
        other_col_names: Set[str],
    ) -> LookMLDimensionGroup:
        """Constructs a LookMLDimensionGroup from temporal column metadata."""
        dg_name = derive_dimension_group_name(
            col.name,
            other_col_names,
            suffixes=self.profile.temporal_suffixes,
        )
        datatype = "timestamp" if raw_type == "TIMESTAMP" else ("date" if raw_type == "DATE" else "datetime")
        timeframes = self.profile.date_timeframes if raw_type == "DATE" else self.profile.default_timeframes
        dg_label = clean_dimension_group_label(
            meta["label"],
            suffixes=self.profile.temporal_label_suffixes,
        )

        return LookMLDimensionGroup(
            name=dg_name,
            type="time",
            timeframes=timeframes,
            sql=f"${{TABLE}}.{col.name}",
            datatype=datatype,
            hidden=not is_certified,
            label=dg_label if is_certified else None,
            description=meta["description"] if is_certified else None,
            synonyms=meta["synonyms"] if is_certified else [],
            tags=meta["tags"] if is_certified else [],
        )

    def _map_scalar_dimension(
        self,
        col: SchemaColumn,
        meta: Dict[str, Any],
        is_certified: bool,
        raw_type: str,
        view_name: str,
    ) -> LookMLDimension:
        """Constructs a scalar LookMLDimension using profile data type maps and primary key rules."""
        dim_type = self.profile.data_type_map.get(raw_type, "string")
        is_pk = self._is_primary_key(col.name, view_name, meta["tags"])

        return LookMLDimension(
            name=col.name,
            type=dim_type,
            sql=f"${{TABLE}}.{col.name}",
            primary_key=is_pk,
            hidden=not is_certified,
            label=meta["label"] if is_certified else None,
            description=meta["description"] if is_certified else None,
            synonyms=meta["synonyms"] if is_certified else [],
            tags=meta["tags"] if is_certified else [],
            suggestions=meta["suggestions"] if is_certified else [],
            value_format_name=meta["format_name"] if is_certified else None,
        )

    def _synthesize_kpi_measures(
        self,
        col: SchemaColumn,
        dim: LookMLDimension,
        meta: Dict[str, Any],
    ) -> List[LookMLMeasure]:
        """Synthesizes KPI aggregation measures for certified numeric dimensions."""
        if not (self.profile.auto_generate_kpi_measures and not dim.hidden and dim.type == "number" and not dim.primary_key):
            return []

        col_lower = col.name.lower()
        if not any(kpi_word in col_lower for kpi_word in self.profile.kpi_measure_keywords):
            return []

        prefix = self.profile.kpi_measure_prefix
        measure_name = f"{prefix}{col.name}"
        m_label = f"Total {dim.label}" if dim.label else title_case(measure_name)
        synonyms = [f"total {s}" for s in meta["synonyms"]] if meta["synonyms"] else [measure_name.replace("_", " ")]

        return [
            LookMLMeasure(
                name=measure_name,
                type=self.profile.kpi_measure_type,
                sql=f"${{TABLE}}.{col.name}",
                hidden=False,
                label=m_label,
                description=f"Sum of {dim.label or col.name}.",
                synonyms=synonyms,
                tags=["certified", "kpi"],
                value_format_name=meta["format_name"],
            )
        ]

    def map_entry_to_view(self, entry: CatalogEntry) -> LookMLView:
        """Transforms a CatalogEntry into a governed LookMLView."""
        view_name = entry.display_name.lower().replace("-", "_")
        base_view_name = f"{view_name}_base"

        # 1. Resolve table-level aspects
        table_desc, table_tags = self._resolve_table_metadata(entry)

        dimensions: List[LookMLDimension] = []
        dimension_groups: List[LookMLDimensionGroup] = []
        measures: List[LookMLMeasure] = []
        assigned_names: Set[str] = set()

        # 2. Iterate and map columns
        for col in entry.columns:
            col_aspects = entry.column_aspects.get(col.name, {})

            # Check exclusions (e.g. policy tags)
            if any(evaluate_exclusion_rule(col_aspects, excl) for excl in self.profile.exclusions):
                continue

            is_certified = evaluate_certification_rule(col_aspects, self.profile.certification_rule)
            meta = self._resolve_column_metadata(col, col_aspects)
            raw_type = col.data_type.upper().split("(")[0].strip()

            # Handle temporal types as dimension groups
            if self.profile.use_dimension_groups and raw_type in ("TIMESTAMP", "DATETIME", "DATE"):
                other_names = {c.name.lower() for c in entry.columns if c.name.lower() != col.name.lower()} | assigned_names
                dg = self._map_dimension_group(col, meta, is_certified, raw_type, other_names)
                assigned_names.add(dg.name)
                dimension_groups.append(dg)
                continue

            # Handle scalar dimension
            dim = self._map_scalar_dimension(col, meta, is_certified, raw_type, view_name)
            assigned_names.add(col.name.lower())
            dimensions.append(dim)

            # Measure generation for certified numeric KPI fields
            measures.extend(self._synthesize_kpi_measures(col, dim, meta))

        return LookMLView(
            view_name=view_name,
            base_view_name=base_view_name,
            sql_table_name=f"`{entry.bigquery_table}`",
            fields_hidden_by_default=True,
            extension_required=True,
            description=table_desc,
            tags=table_tags,
            dimensions=dimensions,
            dimension_groups=dimension_groups,
            measures=measures,
            source_entry=entry.resource_name,
        )
