"""Semantic Mapping Engine: Maps Catalog Entries to Normalized LookML Views and Base Explores."""

import fnmatch
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
    LookMLExplore,
    LookMLExploreJoin,
    LookMLFilter,
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
        """Extracts table description and governance tags from catalog entry, aspects, and AI Data Documentation."""
        table_desc = entry.description or ""
        table_tags = ["certified"] if evaluate_certification_rule(entry.table_aspects, self.profile.certification_rule) else []

        for k, v in entry.table_aspects.items():
            if isinstance(v, dict):
                if "business_description" in v and v["business_description"]:
                    table_desc = v["business_description"]
                if "governance_tags" in v and isinstance(v["governance_tags"], list):
                    table_tags.extend(v["governance_tags"])

        # Fallback to DATA_DOCUMENTATION overview when enabled and no explicit description is set
        if not table_desc and self.profile.use_ai_data_documentation:
            doc_aspect = entry.table_aspects.get("data-documentation")
            if isinstance(doc_aspect, dict) and doc_aspect.get("overview"):
                table_desc = str(doc_aspect["overview"]).strip()

        return table_desc, sorted(list(set(table_tags)))

    def _resolve_table_tags(self, entry: CatalogEntry) -> List[str]:
        """Resolves all governance tags and labels attached to a catalog entry."""
        _, tags = self._resolve_table_metadata(entry)
        for k, v in entry.table_aspects.items():
            if isinstance(v, dict):
                for tag_field in ("tags", "labels"):
                    val = v.get(tag_field)
                    if isinstance(val, list):
                        tags.extend(val)
        return sorted(list(set(tags)))

    def _resolve_column_metadata(
        self,
        col: SchemaColumn,
        col_aspects: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Resolves raw attributes from catalog aspects according to profile cascades and AI documentation fallback."""
        label = self._resolve_attribute(self.profile.field_mappings.label, col_aspects, col)
        description = self._resolve_attribute(self.profile.field_mappings.description, col_aspects, col) or col.description
        if not description and self.profile.use_ai_data_documentation:
            ai_desc = resolve_path_value(col_aspects, "data-documentation.description")
            if ai_desc:
                description = str(ai_desc).strip()

        synonyms = self._resolve_attribute(self.profile.field_mappings.synonyms, col_aspects, col) or []
        tags = self._resolve_tags(self.profile.field_mappings.tags, col_aspects)
        raw_suggestions = self._resolve_attribute(self.profile.field_mappings.suggestions, col_aspects, col) or []
        suggestions = self._filter_suggestions(raw_suggestions, tags)
        format_name = self._resolve_attribute(self.profile.field_mappings.value_format_name, col_aspects, col)

        return {
            "label": label,
            "description": description,
            "synonyms": synonyms,
            "tags": tags,
            "suggestions": suggestions,
            "format_name": format_name,
        }

    def _filter_suggestions(self, raw_suggestions: Any, tags: List[str]) -> List[str]:
        """Filters static suggestions to comprehensive lists within the configured limit."""
        if not raw_suggestions:
            return []
        if not isinstance(raw_suggestions, list):
            if isinstance(raw_suggestions, (tuple, set)):
                raw_suggestions = list(raw_suggestions)
            else:
                raw_suggestions = [raw_suggestions]

        deduped = list(dict.fromkeys(str(s).strip() for s in raw_suggestions if s is not None and str(s).strip()))
        if not deduped:
            return []

        if self.profile.suggestions_require_comprehensive_tag:
            if not any(t in tags for t in self.profile.comprehensive_tags):
                return []

        if len(deduped) < self.profile.max_suggestions_limit:
            return deduped

        return []

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

    def _is_partition_or_cluster_key(
        self,
        col_name: str,
        entry: CatalogEntry,
        col_aspects: Dict[str, Any],
    ) -> Tuple[bool, bool]:
        """Returns (is_partition_key, is_cluster_key) for the column."""
        col_lower = col_name.lower()
        is_part = any(pf.lower() == col_lower for pf in entry.partition_fields) or bool(
            resolve_path_value(col_aspects, "bigquery-table.is_partition_key")
        )
        is_clust = any(cf.lower() == col_lower for cf in entry.cluster_fields) or bool(
            resolve_path_value(col_aspects, "bigquery-table.is_cluster_key")
        )
        return is_part, is_clust

    def _synthesize_key_filter(
        self,
        col: SchemaColumn,
        meta: Dict[str, Any],
        raw_type: str,
        target_dim_name: str,
        is_partition: bool,
        is_cluster: bool,
    ) -> LookMLFilter:
        """Synthesizes a dedicated LookML filter field for a partition or cluster key."""
        if raw_type in ("TIMESTAMP", "DATETIME", "DATE"):
            filter_type = "date"
        elif raw_type in ("INTEGER", "INT64", "FLOAT", "FLOAT64", "NUMERIC", "BIGNUMERIC"):
            filter_type = "number"
        elif raw_type in ("BOOLEAN", "BOOL"):
            filter_type = "yesno"
        else:
            filter_type = "string"

        base_label = meta.get("label") or title_case(col.name)
        key_kind = "Partition & Cluster" if (is_partition and is_cluster) else ("Partition" if is_partition else "Cluster")
        filter_name = f"{col.name}_filter"
        sql_condition = f"{{% condition {filter_name} %}} ${{{target_dim_name}}} {{% endcondition %}}"
        suggest_dimension = target_dim_name if filter_type == "string" else None

        return LookMLFilter(
            name=filter_name,
            type=filter_type,
            sql=sql_condition,
            label=f"{base_label} Filter",
            description=f"Dedicated {key_kind.lower()} key filter for {base_label}.",
            suggest_dimension=suggest_dimension,
        )

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
            meta["label"] or title_case(col.name),
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
            label=(meta["label"] or title_case(col.name)) if is_certified else None,
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
        filters: List[LookMLFilter] = []
        measures: List[LookMLMeasure] = []
        assigned_names: Set[str] = set()

        # 2. Iterate and map columns
        for col in entry.columns:
            col_aspects = entry.column_aspects.get(col.name, {})

            # Check exclusions (e.g. policy tags)
            if any(evaluate_exclusion_rule(col_aspects, excl) for excl in self.profile.exclusions):
                continue

            is_certified = evaluate_certification_rule(col_aspects, self.profile.certification_rule)
            is_part, is_clust = self._is_partition_or_cluster_key(col.name, entry, col_aspects)

            # Promote visibility if partition/cluster filter generation and unhiding are enabled
            if (is_part or is_clust) and self.profile.auto_generate_partition_cluster_filters and self.profile.unhide_partition_cluster_dimensions:
                is_certified = True

            meta = self._resolve_column_metadata(col, col_aspects)
            if is_part and "partition_key" not in meta["tags"]:
                meta["tags"] = sorted(list(set(meta["tags"] + ["partition_key"])))
            if is_clust and "cluster_key" not in meta["tags"]:
                meta["tags"] = sorted(list(set(meta["tags"] + ["cluster_key"])))

            raw_type = col.data_type.upper().split("(")[0].strip()

            # Handle temporal types as dimension groups
            if self.profile.use_dimension_groups and raw_type in ("TIMESTAMP", "DATETIME", "DATE"):
                other_names = {c.name.lower() for c in entry.columns if c.name.lower() != col.name.lower()} | assigned_names
                dg = self._map_dimension_group(col, meta, is_certified, raw_type, other_names)
                assigned_names.add(dg.name)
                dimension_groups.append(dg)

                if (is_part or is_clust) and self.profile.auto_generate_partition_cluster_filters:
                    # Prefer the un-truncated raw timeframe (${dg.name}_raw) when available to prevent
                    # Looker string-casting and ensure direct database partition pruning on BigQuery.
                    # If raw is not present, use explicit LookML field type reference (${dg.name}_date::date).
                    if "raw" in dg.timeframes:
                        target_dim = f"{dg.name}_raw"
                    elif "date" in dg.timeframes:
                        target_dim = f"{dg.name}_date::date"
                    else:
                        target_dim = f"{dg.name}::date"

                    filters.append(
                        self._synthesize_key_filter(
                            col=col,
                            meta=meta,
                            raw_type=raw_type,
                            target_dim_name=target_dim,
                            is_partition=is_part,
                            is_cluster=is_clust,
                        )
                    )
                continue

            # Handle scalar dimension
            dim = self._map_scalar_dimension(col, meta, is_certified, raw_type, view_name)
            assigned_names.add(col.name.lower())
            dimensions.append(dim)

            if (is_part or is_clust) and self.profile.auto_generate_partition_cluster_filters:
                # If scalar temporal column, explicitly state type (::date) to prevent Looker string-casting
                target_dim = f"{dim.name}::date" if raw_type in ("TIMESTAMP", "DATETIME", "DATE") else dim.name
                filters.append(
                    self._synthesize_key_filter(
                        col=col,
                        meta=meta,
                        raw_type=raw_type,
                        target_dim_name=target_dim,
                        is_partition=is_part,
                        is_cluster=is_clust,
                    )
                )

            # Measure generation for certified numeric KPI fields
            measures.extend(self._synthesize_kpi_measures(col, dim, meta))

        return LookMLView(
            view_name=view_name,
            base_view_name=base_view_name,
            sql_table_name=f"`{entry.bigquery_table}`",
            fields_hidden_by_default=self.profile.fields_hidden_by_default,
            extension_required=True,
            description=table_desc,
            tags=table_tags,
            dimensions=dimensions,
            dimension_groups=dimension_groups,
            filters=filters,
            measures=measures,
            source_entry=entry.resource_name,
        )

    def _resolve_field_ref_for_column(self, view: LookMLView, col_name: str) -> str:
        """Resolves the LookML field name (e.g. 'order_date' for dimension_group 'order' or scalar 'store_id') for a physical column."""
        expected_sql = f"${{TABLE}}.{col_name}"
        for dg in view.dimension_groups:
            if dg.sql == expected_sql:
                return f"{dg.name}_date" if "date" in dg.timeframes else dg.name
        for dim in view.dimensions:
            if dim.sql == expected_sql or dim.name == col_name:
                return dim.name
        return col_name

    def map_entry_to_explore(
        self,
        entry: CatalogEntry,
        view: LookMLView,
        views_by_name: Optional[Dict[str, LookMLView]] = None,
    ) -> LookMLExplore:
        """Transforms a CatalogEntry and its LookMLView into a LookMLExplore with joins and partition filters."""
        explore_label = title_case(view.view_name)
        explore_desc = view.description or f"Explore for {explore_label}"

        joins: List[LookMLExploreJoin] = []
        seen_targets: Set[str] = set()

        for rel in entry.joins:
            target_view_name = rel.target_table.lower().replace("-", "_")
            if target_view_name == view.view_name or target_view_name in seen_targets:
                continue
            if views_by_name is not None and target_view_name not in views_by_name:
                continue

            target_view = views_by_name.get(target_view_name) if views_by_name else None
            clauses = []
            for src_col, tgt_col in rel.join_keys:
                src_ref = self._resolve_field_ref_for_column(view, src_col)
                tgt_ref = self._resolve_field_ref_for_column(target_view, tgt_col) if target_view else tgt_col
                clauses.append(f"${{{view.view_name}.{src_ref}}} = ${{{target_view_name}.{tgt_ref}}}")

            if clauses:
                seen_targets.add(target_view_name)
                joins.append(
                    LookMLExploreJoin(
                        name=target_view_name,
                        type=rel.join_type,
                        relationship=rel.relationship_type,
                        sql_on=" AND ".join(clauses),
                    )
                )

        always_filter: Dict[str, str] = {}
        if self.profile.always_filter_on_partition_key and entry.partition_fields:
            for pf in entry.partition_fields:
                field_ref = self._resolve_field_ref_for_column(view, pf)
                always_filter[f"{view.view_name}.{field_ref}"] = self.profile.default_partition_filter_value

        return LookMLExplore(
            name=view.view_name,
            view_name=view.view_name,
            label=explore_label,
            description=explore_desc,
            joins=joins,
            always_filter=always_filter,
            source_entry=entry.resource_name,
        )

    def should_generate_explore(
        self,
        entry: CatalogEntry,
        all_entries: Optional[List[CatalogEntry]] = None,
    ) -> bool:
        """Evaluates ExplorePolicy to determine whether to generate a base explore for entry."""
        policy = self.profile.explore_policy
        if not policy.enabled:
            return False

        table_name = entry.entry_id.lower()
        display_name = (entry.display_name or "").lower()
        view_name = table_name.replace("-", "_")
        entry_names = {table_name, display_name, view_name}

        # 1. Allowlist override: explicit tables always qualify
        allowlist_set = {t.lower() for t in policy.table_allowlist}
        if allowlist_set and (entry_names & allowlist_set):
            return True

        # 2. Exclude dimension tables heuristic
        if policy.exclude_dimension_tables:
            if (
                table_name.startswith("dim_")
                or display_name.startswith("dim_")
                or table_name.startswith("dimension_")
                or table_name.endswith("_dim")
            ):
                return False

            table_tags = {t.lower() for t in self._resolve_table_tags(entry)}
            if any(dt in table_tags for dt in ["dimension", "dim", "lookup"]):
                return False

            if all_entries and len(entry.joins) == 0:
                all_target_tables = {
                    rel.target_table.lower().replace("-", "_")
                    for e in all_entries
                    for rel in e.joins
                }
                if view_name in all_target_tables or table_name in all_target_tables:
                    return False

        # 3. Strategy evaluation
        strategy = policy.strategy.lower()

        if strategy == "all":
            return True

        if strategy == "allowlist":
            # Handled in step 1; if not matched, then False
            return False

        if strategy == "tagged":
            table_tags = {t.lower() for t in self._resolve_table_tags(entry)}
            req_tags = {t.lower() for t in policy.required_tags}
            return bool(table_tags & req_tags)

        if strategy == "patterns":
            for pat in policy.table_patterns:
                pat_l = pat.lower()
                if any(fnmatch.fnmatch(name, pat_l) for name in entry_names):
                    return True
            return False

        if strategy == "root_only":
            if len(entry.joins) > 0:
                return True
            if all_entries:
                all_target_tables = {
                    rel.target_table.lower().replace("-", "_")
                    for e in all_entries
                    for rel in e.joins
                }
                if view_name in all_target_tables or table_name in all_target_tables:
                    return False
            return True

        return True

