"""Google Knowledge Catalog (Dataplex) Client."""

from typing import Any, Dict, List, Optional
from google.cloud import dataplex_v1
from looker_kc_sync.models.catalog import CatalogEntry, SchemaColumn


def _proto_to_python(obj: Any) -> Any:
    """Recursively converts protobuf MapComposite/RepeatedComposite to native Python dicts/lists."""
    if hasattr(obj, "items"):
        return {str(k): _proto_to_python(v) for k, v in obj.items()}
    elif hasattr(obj, "__iter__") and not isinstance(obj, (str, bytes, dict)):
        return [_proto_to_python(item) for item in obj]
    return obj


class DataplexCatalogClient:
    """Client for discovering entries and extracting aspects from Knowledge Catalog."""

    def __init__(self, project_id: str, location: str = "us-central1"):
        self.project_id = project_id
        self.location = location
        self.client = dataplex_v1.CatalogServiceClient()

    def get_dataset_entries(self, dataset_id: str, table_names: Optional[List[str]] = None) -> List[CatalogEntry]:
        """Fetches all catalog entries and aspects for tables in a BigQuery dataset."""
        parent = f"projects/{self.project_id}/locations/{self.location}/entryGroups/@bigquery"
        entries: List[CatalogEntry] = []

        if table_names:
            target_tables = table_names
        else:
            # Query table entries under the dataset's parent entry
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

        return entries

    def _parse_entry(self, entry: dataplex_v1.Entry, dataset_id: str, table_name: str) -> CatalogEntry:
        """Parses a Dataplex Entry protobuf into a CatalogEntry model."""
        columns: List[SchemaColumn] = []
        table_aspects: Dict[str, Dict[str, Any]] = {}
        column_aspects: Dict[str, Dict[str, Dict[str, Any]]] = {}

        # 1. Parse aspects
        for aspect_key, aspect_obj in entry.aspects.items():
            aspect_data_raw = dict(aspect_obj.data) if hasattr(aspect_obj, "data") else {}
            aspect_data = _proto_to_python(aspect_data_raw)

            # Check if this is the native schema aspect
            if "schema" in aspect_key.lower() and "fields" in aspect_data:
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
                if col_name not in column_aspects:
                    column_aspects[col_name] = {}
                column_aspects[col_name][aspect_name] = aspect_data
                column_aspects[col_name][prefix] = aspect_data
            else:
                aspect_name = aspect_key.split(".")[-1]
                table_aspects[aspect_name] = aspect_data
                table_aspects[aspect_key] = aspect_data

        return CatalogEntry(
            resource_name=entry.name,
            entry_id=table_name,
            display_name=entry.entry_source.display_name or table_name,
            description=entry.entry_source.description or "",
            bigquery_table=f"{self.project_id}.{dataset_id}.{table_name}",
            columns=columns,
            table_aspects=table_aspects,
            column_aspects=column_aspects,
        )
