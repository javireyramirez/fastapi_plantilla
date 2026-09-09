import enum
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "AuditAction",
    "AuditFilterParams",
    "AuditLogCreate",
    "AuditLogResponse",
]


class AuditAction(enum.StrEnum):
    """Enumeration of standard audited operations."""

    CREATE = "CREATE"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    TRASH = "TRASH"
    RESTORE = "RESTORE"
    LOGIN = "LOGIN"
    LOGOUT = "LOGOUT"
    IMPERSONATE = "IMPERSONATE"
    PASSWORD_CHANGE = "PASSWORD_CHANGE"  # noqa: S105
    SETTINGS_CHANGE = "SETTINGS_CHANGE"


class AuditLogResponse(BaseModel):
    """Public representation of an immutable audit record."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    entity_type: str
    entity_id: uuid.UUID | None = None
    action: str
    actor_id: uuid.UUID | None = None
    actor_name: str | None = None
    actor_email: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    changes: dict[str, Any] | None = None
    details: str | None = None
    created_at: datetime


class AuditLogCreate(BaseModel):
    """Payload to create an explicit domain audit log record."""

    entity_type: str = Field(..., min_length=1, max_length=100)
    entity_id: uuid.UUID | None = None
    action: str = Field(..., min_length=1, max_length=50)
    actor_id: uuid.UUID | None = None
    actor_name: str | None = Field(default=None, max_length=255)
    actor_email: str | None = Field(default=None, max_length=255)
    ip_address: str | None = Field(default=None, max_length=45)
    user_agent: str | None = Field(default=None, max_length=500)
    changes: dict[str, Any] | None = None
    details: str | None = None


class AuditFilterParams(BaseModel):
    """Query filters for retrieving paginated audit logs."""

    entity_type: str | None = None
    entity_id: uuid.UUID | None = None
    action: str | None = None
    actor_id: uuid.UUID | None = None
    from_date: datetime | None = None
    to_date: datetime | None = None
    page: int = Field(default=1, ge=1, le=1000)
    limit: int = Field(default=20, ge=1, le=100)
