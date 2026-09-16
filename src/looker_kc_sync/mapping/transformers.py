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
