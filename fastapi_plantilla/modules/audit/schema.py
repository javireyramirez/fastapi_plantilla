import enum
import uuid
from datetime import datetime
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from fastapi_plantilla.core.crud.schema import SortOrder, UserReference

DEFAULT_AUDIT_EXPORT_LIMIT: int = 1000
MAX_AUDIT_EXPORT_LIMIT: int = 5000
DEFAULT_AUDIT_RETENTION_DAYS: int = 365
DEFAULT_AUDIT_PURGE_LIMIT: int = 1000

__all__ = [
    "DEFAULT_AUDIT_EXPORT_LIMIT",
    "DEFAULT_AUDIT_PURGE_LIMIT",
    "DEFAULT_AUDIT_RETENTION_DAYS",
    "MAX_AUDIT_EXPORT_LIMIT",
    "AuditAction",
    "AuditFilterParams",
    "AuditLogExportResponse",
    "AuditLogResponse",
]


class AuditAction(enum.StrEnum):
    """Enumeration of standard audited operations."""

    CREATE = "CREATE"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    PERMANENT_DELETE = "PERMANENT_DELETE"
    PURGE = "PURGE"
    TRASH = "TRASH"
    RESTORE = "RESTORE"
    SUSPEND = "SUSPEND"
    REACTIVATE = "REACTIVATE"
    ACTIVATE = "ACTIVATE"
    LOGIN = "LOGIN"
    LOGIN_FAILED = "LOGIN_FAILED"
    LOGOUT = "LOGOUT"
    IMPERSONATE = "IMPERSONATE"
    PASSWORD_CHANGE = "PASSWORD_CHANGE"  # noqa: S105
    SETTINGS_CHANGE = "SETTINGS_CHANGE"


class AuditLogResponse(BaseModel):
    """Public representation of an immutable audit record."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID
    entity_type: str
    entity_id: uuid.UUID | None = None
    entity_name: str | None = None
    module_slug: str | None = None
    module_name: str | None = None
    action: str
    user: UserReference | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    changes: dict[str, Any] | None = None
    details: str | None = None
    created_at: datetime


class AuditLogExportResponse(BaseModel):
    """Clean representation of audit log records tailored for export."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID
    created_at: datetime
    action: str
    entity_type: str
    entity_id: uuid.UUID | None = None
    entity_name: str | None = None
    module_slug: str | None = None
    module_name: str | None = None
    actor_id: uuid.UUID | None = None
    actor_name: str | None = None
    actor_email: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    details: str | None = None
    changes: dict[str, Any] | None = None


class AuditFilterParams(BaseModel):
    """Query filters for retrieving paginated audit logs."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    entity_type: str | None = None
    entity_id: uuid.UUID | None = None
    entity_name: str | None = None
    action: str | None = None
    actor_id: uuid.UUID | None = Field(
        default=None,
        validation_alias=AliasChoices("actor_id", "user_id"),
    )
    created_at_from: datetime | None = Field(
        default=None,
        validation_alias=AliasChoices("created_at_from", "from_date"),
    )
    created_at_to: datetime | None = Field(
        default=None,
        validation_alias=AliasChoices("created_at_to", "to_date"),
    )
    page: int = Field(default=1, ge=1, le=1000)
    limit: int = Field(default=20, ge=1, le=100)
    sort_by: str = "created_at"
    sort_order: SortOrder = SortOrder.DESC
