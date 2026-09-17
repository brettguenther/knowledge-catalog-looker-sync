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
    default_timeframes: List[str] = Field(
        default_factory=lambda: ["raw", "time", "date", "week", "month", "quarter", "year"]
    )
    date_timeframes: List[str] = Field(
        default_factory=lambda: ["raw", "date", "week", "month", "quarter", "year"]
    )
    certification_rule: CertificationRule
    exclusions: List[ExclusionRule] = Field(default_factory=list)
    field_mappings: FieldMappings = Field(default_factory=FieldMappings)
