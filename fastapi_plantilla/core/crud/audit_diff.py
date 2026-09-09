"""Audit diff computation and sensitive field redaction utilities.

Provides pure functions for safely serializing values, detecting sensitive
fields, and computing before/after changesets for audit log entries.
"""

import uuid
from collections.abc import Collection
from datetime import datetime
from typing import Any

__all__ = [
    "compute_create_diff",
    "compute_update_diff",
    "is_sensitive_audit_field",
    "serialize_audit_val",
]

_SENSITIVE_KEYWORDS: frozenset[str] = frozenset({"password", "secret", "token"})


def serialize_audit_val(val: Any) -> Any:
    """Serialize field value safely for JSON audit storage."""
    if val is None or isinstance(val, (int, float, bool, str)):
        return val
    if isinstance(val, (uuid.UUID, datetime)):
        return str(val)
    if hasattr(val, "value"):
        return val.value
    return str(val)


def is_sensitive_audit_field(
    field_name: str,
    sensitive_columns: Collection[str] = frozenset(),
) -> bool:
    """Check if a field name represents sensitive data or credentials."""
    name = field_name.lower()
    return field_name in sensitive_columns or any(
        kw in name for kw in _SENSITIVE_KEYWORDS
    )


def compute_create_diff(
    new_data: dict[str, Any],
    immutable_fields: Collection[str] = frozenset(),
    sensitive_columns: Collection[str] = frozenset(),
) -> dict[str, Any]:
    """Compute field changes dictionary for creation action."""
    changes: dict[str, Any] = {}
    for k, v in new_data.items():
        if k in immutable_fields:
            continue
        val = (
            "[REDACTED]"
            if is_sensitive_audit_field(k, sensitive_columns)
            else serialize_audit_val(v)
        )
        changes[k] = {"old": None, "new": val}
    return changes


def compute_update_diff(
    snapshot_before: dict[str, Any],
    updated_payload: dict[str, Any],
    immutable_fields: Collection[str] = frozenset(),
    sensitive_columns: Collection[str] = frozenset(),
) -> dict[str, Any]:
    """Compute field changes dictionary for update action."""
    changes: dict[str, Any] = {}
    for k, new_v in updated_payload.items():
        if k in immutable_fields:
            continue
        old_v = snapshot_before.get(k)
        if old_v != new_v:
            if is_sensitive_audit_field(k, sensitive_columns):
                changes[k] = {"old": "[REDACTED]", "new": "[REDACTED]"}
            else:
                changes[k] = {
                    "old": serialize_audit_val(old_v),
                    "new": serialize_audit_val(new_v),
                }
    return changes
