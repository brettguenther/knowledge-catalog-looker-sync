"""Pydantic data models representing normalized LookML elements."""

from typing import List, Optional
from pydantic import BaseModel, Field


class LookMLDimension(BaseModel):
    """Represents a LookML dimension."""
    name: str
    type: str = "string"
    sql: str
    primary_key: bool = False
    hidden: bool = False
    label: Optional[str] = None
    description: Optional[str] = None
    synonyms: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    suggestions: List[str] = Field(default_factory=list)
    value_format_name: Optional[str] = None
    group_label: Optional[str] = None


class LookMLDimensionGroup(BaseModel):
    """Represents a LookML dimension_group (time or duration)."""
    name: str
    type: str = "time"
    timeframes: List[str] = Field(
        default_factory=lambda: ["raw", "time", "date", "week", "month", "quarter", "year"]
    )
    sql: str
    datatype: Optional[str] = None  # timestamp, datetime, date, epoch, yyyymmdd
    hidden: bool = False
    label: Optional[str] = None
    description: Optional[str] = None
    synonyms: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    group_label: Optional[str] = None
    convert_tz: Optional[bool] = None
    intervals: List[str] = Field(default_factory=list)


class LookMLMeasure(BaseModel):
    """Represents a LookML measure."""
    name: str
    type: str = "sum"
    sql: str
    hidden: bool = False
    label: Optional[str] = None
    description: Optional[str] = None
    synonyms: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    value_format_name: Optional[str] = None
    group_label: Optional[str] = None


class LookMLView(BaseModel):
    """Represents a LookML view definition."""
    view_name: str
    base_view_name: str
    sql_table_name: str
    fields_hidden_by_default: bool = True
    extension_required: bool = True
    description: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    dimensions: List[LookMLDimension] = Field(default_factory=list)
    dimension_groups: List[LookMLDimensionGroup] = Field(default_factory=list)
    measures: List[LookMLMeasure] = Field(default_factory=list)
    source_entry: str = ""
