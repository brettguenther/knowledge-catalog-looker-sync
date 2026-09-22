"""Google Knowledge Catalog (Dataplex) Client."""

from typing import Any, Dict, List, Optional, Tuple
import yaml
from google.cloud import dataplex_v1
from looker_kc_sync.models.catalog import CatalogEntry, JoinRelationship, SchemaColumn


def _proto_to_python(obj: Any) -> Any:
    """Recursively converts protobuf MapComposite/RepeatedComposite to native Python dicts/lists."""
    if hasattr(obj, "items"):
        return {str(k): _proto_to_python(v) for k, v in obj.items()}
    elif hasattr(obj, "__iter__") and not isinstance(obj, (str, bytes, dict)):
        return [_proto_to_python(item) for item in obj]
    return obj


def _extract_table_name(resource_or_fqn: str) -> str:
    """Extracts the short table name from a Dataplex resource URI or FQN."""
    if "/tables/" in resource_or_fqn:
        return resource_or_fqn.split("/tables/")[-1].strip()
    return resource_or_fqn.split(".")[-1].strip()


class DataplexCatalogClient:
    """Client for discovering entries, aspects, join context, and AI documentation from Knowledge Catalog."""

    def __init__(
        self,
        project_id: str,
        location: str = "us-central1",
        scan_location: str = "us-central1",
        client: Optional[dataplex_v1.CatalogServiceClient] = None,
        scan_client: Optional[dataplex_v1.DataScanServiceClient] = None,
    ):
        self.project_id = project_id
        self.location = location
        self.scan_location = scan_location if scan_location.lower() not in ("us", "eu", "global") else "us-central1"
        self.client = client or dataplex_v1.CatalogServiceClient()
        self._scan_client = scan_client

    @property
    def scan_client(self) -> dataplex_v1.DataScanServiceClient:
        if self._scan_client is None:
            self._scan_client = dataplex_v1.DataScanServiceClient()
        return self._scan_client

    def get_dataset_entries(self, dataset_id: str, table_names: Optional[List[str]] = None) -> List[CatalogEntry]:
        """Fetches all catalog entries, system aspects, join context, and DataScan documentation for a dataset."""
        parent = f"projects/{self.project_id}/locations/{self.location}/entryGroups/@bigquery"
        entries: List[CatalogEntry] = []

        if table_names:
            target_tables = table_names
        else:
            dataset_parent_entry = f"{parent}/entries/bigquery.googleapis.com/projects/{self.project_id}/datasets/{dataset_id}"
            req = dataplex_v1.ListEntriesRequest(
                parent=parent,
                filter=f'parent_entry="{dataset_parent_entry}"',
            )
            target_tables = []
            for item in self.client.list_entries(request=req):
                if "/tables/" in item.name:
                    tbl = item.name.split("/tables/")[-1]
                    target_tables.append(tbl)

        for tbl in target_tables:
            entry_resource = f"{parent}/entries/bigquery.googleapis.com/projects/{self.project_id}/datasets/{dataset_id}/tables/{tbl}"
            try:
                req = dataplex_v1.GetEntryRequest(
                    name=entry_resource,
                    view=dataplex_v1.EntryView.ALL,
                )
                entry_pb = self.client.get_entry(request=req)
                parsed = self._parse_entry(entry_pb, dataset_id, tbl)
                entries.append(parsed)
            except Exception as e:
                print(f"Warning: Failed to fetch Dataplex entry for {tbl}: {e}")

        if entries:
            self._enrich_with_lookup_context(entries)
            self._enrich_with_data_scans(dataset_id, entries)

        return entries

    def _parse_entry(self, entry: dataplex_v1.Entry, dataset_id: str, table_name: str) -> CatalogEntry:
        """Parses a Dataplex Entry protobuf into a CatalogEntry model, unpacking system aspects."""
        columns: List[SchemaColumn] = []
        table_aspects: Dict[str, Dict[str, Any]] = {}
        column_aspects: Dict[str, Dict[str, Dict[str, Any]]] = {}
        partition_fields: List[str] = []
        cluster_fields: List[str] = []

        for aspect_key, aspect_obj in entry.aspects.items():
            aspect_data_raw = {}
            if getattr(aspect_obj, "data", None) is not None:
                try:
                    aspect_data_raw = dict(aspect_obj.data)
                except Exception:
                    aspect_data_raw = {}
            aspect_data = _proto_to_python(aspect_data_raw)

            # Check if this is the native schema aspect
            if "schema" in aspect_key.lower() and "fields" in aspect_data and "@Schema." not in aspect_key:
                for f in aspect_data.get("fields", []):
                    col_name = f.get("name")
                    if col_name:
                        columns.append(
                            SchemaColumn(
                                name=col_name,
                                data_type=f.get("dataType", "STRING"),
                                metadata_type=f.get("metadataType"),
                                mode=f.get("mode", "NULLABLE"),
                                description=f.get("description", ""),
                            )
                        )

            # Route to column aspect or table aspect
            if "@Schema." in aspect_key:
                prefix, col_name = aspect_key.split("@Schema.", 1)
                aspect_name = prefix.split(".")[-1]  # e.g. semantic-curation
                column_aspects.setdefault(col_name, {})[aspect_name] = aspect_data
                column_aspects[col_name][prefix] = aspect_data
            else:
                aspect_name = aspect_key.split(".")[-1]
                table_aspects[aspect_name] = aspect_data
                table_aspects[aspect_key] = aspect_data

                # Unpack table-level data-profile aspect into per-column aspects
                if aspect_name == "data-profile" and isinstance(aspect_data.get("fields"), dict):
                    for col_name, col_prof in aspect_data["fields"].items():
                        if isinstance(col_prof, dict):
                            column_aspects.setdefault(col_name, {})["data-profile"] = col_prof

                # Unpack table-level data-quality-scorecard aspect into per-column aspects
                if aspect_name == "data-quality-scorecard" and isinstance(aspect_data.get("columns"), list):
                    for col_dq in aspect_data["columns"]:
                        if isinstance(col_dq, dict) and col_dq.get("name"):
                            column_aspects.setdefault(col_dq["name"], {})["data-quality-scorecard"] = col_dq

                # Unpack catalog-published descriptions (DATA_DOCUMENTATION) into per-column aspects
                if aspect_name == "descriptions" and isinstance(aspect_data, dict):
                    if aspect_data.get("description"):
                        table_aspects.setdefault("data-documentation", {})["overview"] = str(aspect_data["description"]).strip()
                    if isinstance(aspect_data.get("fields"), list):
                        for f in aspect_data["fields"]:
                            if isinstance(f, dict) and f.get("name") and f.get("description"):
                                column_aspects.setdefault(f["name"], {}).setdefault(
                                    "data-documentation", {}
                                )["description"] = str(f["description"]).strip()


                # Extract partition and cluster keys from bigquery-table aspect
                if aspect_name == "bigquery-table" and isinstance(aspect_data, dict):
                    part_info = aspect_data.get("partitioning")
                    if isinstance(part_info, dict):
                        pf = part_info.get("field")
                        if pf and pf not in partition_fields:
                            partition_fields.append(str(pf))
                        for p_item in part_info.get("fields", []):
                            if p_item and p_item not in partition_fields:
                                partition_fields.append(str(p_item))
                    clust_info = aspect_data.get("clustering")
                    if isinstance(clust_info, dict):
                        for cf in clust_info.get("fields", []):
                            if cf and cf not in cluster_fields:
                                cluster_fields.append(str(cf))

        # Annotate column_aspects with partition/cluster flags
        for pf in partition_fields:
            column_aspects.setdefault(pf, {}).setdefault("bigquery-table", {})["is_partition_key"] = True
        for cf in cluster_fields:
            column_aspects.setdefault(cf, {}).setdefault("bigquery-table", {})["is_cluster_key"] = True

        return CatalogEntry(
            resource_name=entry.name,
            entry_id=table_name,
            display_name=entry.entry_source.display_name or table_name,
            description=entry.entry_source.description or "",
            bigquery_table=f"{self.project_id}.{dataset_id}.{table_name}",
            columns=columns,
            table_aspects=table_aspects,
            column_aspects=column_aspects,
            partition_fields=partition_fields,
            cluster_fields=cluster_fields,
        )

    def _add_join_if_new(self, entry: CatalogEntry, join_rel: JoinRelationship) -> None:
        """Appends a JoinRelationship to entry.joins if not already present."""
        for existing in entry.joins:
            if (
                existing.source_table == join_rel.source_table
                and existing.target_table == join_rel.target_table
                and existing.join_keys == join_rel.join_keys
            ):
                return
        entry.joins.append(join_rel)

    def _enrich_with_lookup_context(self, entries: List[CatalogEntry]) -> None:
        """Enriches entries with detected table joins and partition/cluster metadata from LookupContext."""
        if not hasattr(self.client, "lookup_context"):
            return

        entries_by_id = {e.entry_id.lower(): e for e in entries}
        # LookupContext accepts up to 10 resources per call
        for idx in range(0, len(entries), 10):
            batch = entries[idx : idx + 10]
            resources = [e.resource_name for e in batch if e.resource_name]
            if not resources:
                continue
            try:
                resp = self.client.lookup_context(
                    request={
                        "name": f"projects/{self.project_id}/locations/{self.location}",
                        "resources": resources,
                    }
                )
                ctx_str = getattr(resp, "context", "")
                if not ctx_str:
                    continue
                ctx_data = yaml.safe_load(ctx_str) or {}
                if not isinstance(ctx_data, dict):
                    continue

                # 1. Parse usage.partitionBy and usage.clusterBy
                for res_item in ctx_data.get("resources", []) or []:
                    if not isinstance(res_item, dict):
                        continue
                    tbl_name = _extract_table_name(str(res_item.get("resource", "") or res_item.get("simpleName", "")))
                    target_entry = entries_by_id.get(tbl_name.lower())
                    if not target_entry:
                        continue
                    usage = res_item.get("usage") or {}
                    if isinstance(usage, dict):
                        for pf in usage.get("partitionBy") or []:
                            if pf and pf not in target_entry.partition_fields:
                                target_entry.partition_fields.append(str(pf))
                                target_entry.column_aspects.setdefault(str(pf), {}).setdefault("bigquery-table", {})[
                                    "is_partition_key"
                                ] = True
                        for cf in usage.get("clusterBy") or []:
                            if cf and cf not in target_entry.cluster_fields:
                                target_entry.cluster_fields.append(str(cf))
                                target_entry.column_aspects.setdefault(str(cf), {}).setdefault("bigquery-table", {})[
                                    "is_cluster_key"
                                ] = True

                # 2. Parse joins
                joins_map = ctx_data.get("joins") or {}
                if isinstance(joins_map, dict):
                    for _, join_list in joins_map.items():
                        if not isinstance(join_list, list):
                            continue
                        for j in join_list:
                            if not isinstance(j, dict):
                                continue
                            src_tbl = _extract_table_name(str(j.get("source", "")))
                            tgt_tbl = _extract_table_name(str(j.get("target", "")))
                            raw_keys = j.get("joinKeys") or []
                            key_pairs: List[Tuple[str, str]] = []
                            for k in raw_keys:
                                if isinstance(k, dict) and k.get("sourceField") and k.get("targetField"):
                                    key_pairs.append((str(k["sourceField"]), str(k["targetField"])))
                            if not src_tbl or not tgt_tbl or not key_pairs:
                                continue

                            # Attach join relationship to the fact/target or source entry
                            # In LookupContext, 'source' is typically dimension (e.g. dim_store) and 'target' is fact (fct_sales_daily)
                            if tgt_tbl.lower() in entries_by_id:
                                flipped_keys = [(tk, sk) for sk, tk in key_pairs]
                                self._add_join_if_new(
                                    entries_by_id[tgt_tbl.lower()],
                                    JoinRelationship(
                                        source_table=tgt_tbl,
                                        target_table=src_tbl,
                                        join_keys=flipped_keys,
                                    ),
                                )
                            elif src_tbl.lower() in entries_by_id:
                                self._add_join_if_new(
                                    entries_by_id[src_tbl.lower()],
                                    JoinRelationship(
                                        source_table=src_tbl,
                                        target_table=tgt_tbl,
                                        join_keys=key_pairs,
                                    ),
                                )
            except Exception as e:
                print(f"Warning: LookupContext enrichment skipped: {e}")

    def _enrich_with_data_scans(self, dataset_id: str, entries: List[CatalogEntry]) -> None:
        """Enriches entries with AI Data Documentation (DATA_DOCUMENTATION) descriptions and schema joins."""
        entries_by_id = {e.entry_id.lower(): e for e in entries}
        parent = f"projects/{self.project_id}/locations/{self.scan_location}"
        dataset_prefix = f"//bigquery.googleapis.com/projects/{self.project_id}/datasets/{dataset_id}"

        try:
            scans = list(self.scan_client.list_data_scans(parent=parent))
        except Exception:
            return

        for scan in scans:
            resource = getattr(scan.data, "resource", "") or ""
            if not resource.startswith(dataset_prefix):
                continue

            scan_type_name = dataplex_v1.DataScanType(scan.type_).name
            if scan_type_name != "DATA_DOCUMENTATION":
                continue

            try:
                full_scan = self.scan_client.get_data_scan(
                    request=dataplex_v1.GetDataScanRequest(
                        name=scan.name,
                        view=dataplex_v1.GetDataScanRequest.DataScanView.FULL,
                    )
                )
                doc_res = getattr(full_scan, "data_documentation_result", None)
                if not doc_res:
                    continue

                which = doc_res._pb.WhichOneof("result")
                if which == "table_result":
                    tr = doc_res.table_result
                    tbl_name = _extract_table_name(resource)
                    entry = entries_by_id.get(tbl_name.lower())
                    if not entry:
                        continue
                    if getattr(tr, "overview", ""):
                        entry.table_aspects.setdefault("data-documentation", {})["overview"] = tr.overview.strip()
                    if hasattr(tr, "schema") and hasattr(tr.schema, "fields"):
                        for field_doc in tr.schema.fields:
                            if field_doc.name and field_doc.description:
                                entry.column_aspects.setdefault(field_doc.name, {}).setdefault(
                                    "data-documentation", {}
                                )["description"] = field_doc.description.strip()

                elif which == "dataset_result":
                    dr = doc_res.dataset_result
                    for rel in getattr(dr, "schema_relationships", []) or []:
                        left_fqn = getattr(rel.left_schema_paths, "table_fqn", "")
                        right_fqn = getattr(rel.right_schema_paths, "table_fqn", "")
                        left_paths = list(getattr(rel.left_schema_paths, "paths", []) or [])
                        right_paths = list(getattr(rel.right_schema_paths, "paths", []) or [])
                        if not left_fqn or not right_fqn or not left_paths or len(left_paths) != len(right_paths):
                            continue
                        left_tbl = _extract_table_name(left_fqn)
                        right_tbl = _extract_table_name(right_fqn)
                        if left_tbl.lower() in entries_by_id and right_tbl.lower() in entries_by_id:
                            key_pairs = list(zip(left_paths, right_paths))
                            self._add_join_if_new(
                                entries_by_id[left_tbl.lower()],
                                JoinRelationship(
                                    source_table=left_tbl,
                                    target_table=right_tbl,
                                    join_keys=key_pairs,
                                ),
                            )
            except Exception as e:
                print(f"Warning: Failed to enrich from DATA_DOCUMENTATION scan '{scan.name}': {e}")
