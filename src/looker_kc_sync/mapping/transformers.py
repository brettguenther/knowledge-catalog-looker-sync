"""Value transformation utilities for LookML parameter mapping."""

from collections.abc import Sequence
import re
from typing import Any, List, Optional


def title_case(value: str) -> str:
    """Converts a snake_case or technical identifier into a friendly Title Case label."""
    if not value:
        return ""
    words = re.split(r"[_\-\s]+", value)
    return " ".join(w.capitalize() for w in words if w)


def parse_delimited_list(value: Any, delimiter: str = ",") -> List[str]:
    """Parses a string or iterable into a clean list of trimmed strings."""
    if not value:
        return []
    if isinstance(value, (list, tuple, Sequence)) and not isinstance(value, (str, bytes)):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(delimiter) if item.strip()]
    return [str(value).strip()]


def map_lookup_value(value: Any, lookup_map: Optional[dict]) -> Optional[str]:
    """Maps a source format value through a lookup dictionary."""
    if not value or not lookup_map:
        return str(value) if value else None
    str_val = str(value).strip().lower()
    for k, v in lookup_map.items():
        if k.lower() == str_val:
            return v
    return str_val


def derive_dimension_group_name(column_name: str, reserved_names: set[str]) -> str:
    """Strips temporal suffixes (_date, _time, _at, _timestamp, _datetime) to form the LookML dimension_group name.
    
    If stripping the suffix results in an empty string or a name already present in reserved_names
    (e.g. other table columns or already generated fields), the original column name is retained.
    """
    col = column_name.lower()
    for suffix in ("_timestamp", "_datetime", "_date", "_time", "_at"):
        if col.endswith(suffix):
            base = col[:-len(suffix)]
            if base and base not in reserved_names:
                return base
            break
    return col


def clean_dimension_group_label(label: Optional[str]) -> Optional[str]:
    """Strips temporal suffix words (Date, Time, Timestamp, Datetime) from the label.
    
    Looker automatically combines the dimension_group label with each timeframe name
    (e.g., 'Order Placed' + 'Date' -> 'Order Placed Date'). Stripping redundant suffixes
    prevents duplicate labels like 'Order Placed Date Date' in the field picker.
    """
    if not label:
        return None
    for suffix in (" Date", " Time", " Timestamp", " Datetime", " date", " time", " timestamp", " datetime"):
        if label.endswith(suffix):
            cleaned = label[:-len(suffix)].strip()
            if cleaned:
                return cleaned
    return label
