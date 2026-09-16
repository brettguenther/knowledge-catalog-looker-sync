"""Rule evaluation engine for certification and exclusions."""

from typing import Any, Dict
from looker_kc_sync.mapping.profile import CertificationRule, ExclusionRule


def resolve_path_value(data: Dict[str, Any], path: str) -> Any:
    """Extracts a value from nested dictionary using dot notation or aspect matching.
    
    Supports:
    - 'aspect_name.field_name'
    - 'field_name' (top-level)
    - Matching aspect types that contain hyphens or project prefixes
    """
    if not data or not path:
        return None

    # Check direct key first
    if path in data:
        return data[path]

    parts = path.split(".")
    
    # 1. Exact traversal
    curr: Any = data
    found = True
    for part in parts:
        if isinstance(curr, dict) and part in curr:
            curr = curr[part]
        else:
            found = False
            break
    if found:
        return curr

    # 2. Fuzzy aspect matching: e.g. path 'semantic_curation.status' matches
    # key '353822458593.us-central1.semantic-curation' -> 'status'
    target_aspect = parts[0].replace("_", "-")
    target_field = ".".join(parts[1:]) if len(parts) > 1 else None

    for aspect_key, aspect_val in data.items():
        if target_aspect in aspect_key.lower():
            if not target_field:
                return aspect_val
            if isinstance(aspect_val, dict):
                # Search recursively or directly
                sub_parts = target_field.split(".")
                sub_curr = aspect_val
                sub_found = True
                for sp in sub_parts:
                    if isinstance(sub_curr, dict) and sp in sub_curr:
                        sub_curr = sub_curr[sp]
                    else:
                        sub_found = False
                        break
                if sub_found:
                    return sub_curr

    return None


def evaluate_certification_rule(data: Dict[str, Any], rule: CertificationRule) -> bool:
    """Evaluates whether an entity's aspects satisfy the certification rule."""
    val = resolve_path_value(data, rule.path)
    if val is None:
        return False

    op = rule.operator.lower()
    if op == "equals":
        return any(str(val).upper() == str(v).upper() for v in rule.values)
    elif op == "in":
        return any(str(val).upper() == str(v).upper() for v in rule.values)
    elif op == "not_equals":
        return not any(str(val).upper() == str(v).upper() for v in rule.values)
    elif op == "exists":
        return val is not None
    elif op == "boolean_true":
        return bool(val) is True

    return False


def evaluate_exclusion_rule(data: Dict[str, Any], rule: ExclusionRule) -> bool:
    """Evaluates whether an entity should be excluded based on exclusion rules."""
    val = resolve_path_value(data, rule.path)
    if val is None:
        return False

    op = rule.operator.lower()
    if op == "contains":
        if isinstance(val, (list, tuple)):
            return any(item in rule.values for item in val)
        return str(val) in rule.values
    elif op == "equals":
        return any(str(val) == str(v) for v in rule.values)

    return False
