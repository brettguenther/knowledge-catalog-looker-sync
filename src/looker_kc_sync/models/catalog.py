"""Pydantic data models representing Dataplex / Knowledge Catalog assets."""

from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field


class SchemaColumn(BaseModel):
    """Represents a column defined in the Dataplex / BigQuery schema aspect."""
    name: str
    data_type: str
    metadata_type: Optional[str] = None
    mode: Optional[str] = "NULLABLE"
    description: Optional[str] = None


class JoinRelationship(BaseModel):
    """Represents a foreign-key / join relationship discovered between two tables."""
    source_table: str
    target_table: str
    join_keys: List[Tuple[str, str]] = Field(default_factory=list)
    relationship_type: str = "many_to_one"
    join_type: str = "left_outer"


class CatalogEntry(BaseModel):
    """Represents an ingested Catalog Entry with table and column-level aspects."""
    resource_name: str
    entry_id: str
    display_name: str
    description: Optional[str] = ""
    bigquery_table: str
    columns: List[SchemaColumn] = Field(default_factory=list)
    table_aspects: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    column_aspects: Dict[str, Dict[str, Dict[str, Any]]] = Field(default_factory=dict)
    partition_fields: List[str] = Field(default_factory=list)
    cluster_fields: List[str] = Field(default_factory=list)
    joins: List[JoinRelationship] = Field(default_factory=list)
