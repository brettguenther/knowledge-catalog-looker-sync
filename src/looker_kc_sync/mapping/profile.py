"""Schema for Declarative Mapping Profiles."""

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class CertificationRule(BaseModel):
    """Rule determining whether an entity is certified (hidden: no)."""
    path: str
    operator: str = "equals"  # equals, in, not_equals, exists, boolean_true
    values: List[Any] = Field(default_factory=list)


class ExclusionRule(BaseModel):
    """Rule determining whether an entity should be excluded entirely."""
    path: str
    operator: str = "contains"
    values: List[Any] = Field(default_factory=list)


class MappingSource(BaseModel):
    """Source definition within a resolution cascade."""
    path: Optional[str] = None
    transform: Optional[str] = None
    type: str = "string"  # string, list, delimited_string
    delimiter: str = ","


class DynamicTag(BaseModel):
    """Rule for generating dynamic tags from aspect values."""
    path: str
    prefix: str = ""


class TagMapping(BaseModel):
    """Specification for LookML field tags."""
    static_tags: List[str] = Field(default_factory=list)
    dynamic_tags: List[DynamicTag] = Field(default_factory=list)


class AttributeMapping(BaseModel):
    """Generic mapping for a LookML parameter."""
    sources: List[MappingSource] = Field(default_factory=list)
    lookup_map: Optional[Dict[str, str]] = None


class FieldMappings(BaseModel):
    """Mapping specifications for all LookML field parameters."""
    label: Optional[AttributeMapping] = None
    description: Optional[AttributeMapping] = None
    synonyms: Optional[AttributeMapping] = None
    tags: Optional[TagMapping] = None
    suggestions: Optional[AttributeMapping] = None
    value_format_name: Optional[AttributeMapping] = None


class MappingProfile(BaseModel):
    """Complete customer mapping profile."""
    profile_name: str
    description: Optional[str] = ""
    auto_generate_kpi_measures: bool = False
    use_dimension_groups: bool = True
    fields_hidden_by_default: bool = False
    max_suggestions_limit: int = 10
    suggestions_require_comprehensive_tag: bool = False
    comprehensive_tags: List[str] = Field(
        default_factory=lambda: ["comprehensive", "closed_list", "exhaustive"]
    )
    use_ai_data_documentation: bool = True
    auto_generate_partition_cluster_filters: bool = False
    unhide_partition_cluster_dimensions: bool = True
    always_filter_on_partition_key: bool = False
    default_partition_filter_value: str = "30 days"
    default_timeframes: List[str] = Field(
        default_factory=lambda: ["raw", "time", "date", "week", "month", "quarter", "year"]
    )
    date_timeframes: List[str] = Field(
        default_factory=lambda: ["raw", "date", "week", "month", "quarter", "year"]
    )
    certification_rule: CertificationRule
    exclusions: List[ExclusionRule] = Field(default_factory=list)
    field_mappings: FieldMappings = Field(default_factory=FieldMappings)

    # Configurable Business Heuristics
    kpi_measure_keywords: List[str] = Field(
        default_factory=lambda: ["amount", "price", "revenue", "cost", "total", "sales"]
    )
    kpi_measure_type: str = "sum"
    kpi_measure_prefix: str = "total_"
    primary_key_patterns: List[str] = Field(
        default_factory=lambda: ["{view_name}_id", "id"]
    )
    primary_key_tags: List[str] = Field(
        default_factory=lambda: ["primary_key"]
    )
    temporal_suffixes: List[str] = Field(
        default_factory=lambda: ["_timestamp", "_datetime", "_date", "_time", "_at"]
    )
    temporal_label_suffixes: List[str] = Field(
        default_factory=lambda: [
            " Date", " Time", " Timestamp", " Datetime",
            " date", " time", " timestamp", " datetime",
        ]
    )
    data_type_map: Dict[str, str] = Field(
        default_factory=lambda: {
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
    )
