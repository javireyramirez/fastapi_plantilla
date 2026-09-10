import enum
import uuid
from datetime import datetime
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from fastapi_plantilla.core.crud.schema import UserReference

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
    PERMANENT_DELETE = "PERMANENT_DELETE"
    PURGE = "PURGE"
    TRASH = "TRASH"
    RESTORE = "RESTORE"
    SUSPEND = "SUSPEND"
    REACTIVATE = "REACTIVATE"
    ACTIVATE = "ACTIVATE"
    LOGIN = "LOGIN"
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
    entity_name: str | None = Field(
        default=None,
        validation_alias=AliasChoices("entity_name", "entityName"),
    )
    entityName: str | None = Field(default=None)  # noqa: N815
    action: str
    actor_id: uuid.UUID | None = None
    actor_name: str | None = None
    actor_email: str | None = None
    user: UserReference | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    changes: dict[str, Any] | None = None
    details: str | None = None
    created_at: datetime

    @model_validator(mode="after")
    def sync_entity_names(self) -> "AuditLogResponse":
        """Synchronize snake_case and camelCase entity name fields."""
        val = self.entity_name or self.entityName
        self.entity_name = val
        self.entityName = val
        return self


class AuditLogCreate(BaseModel):
    """Payload to create an explicit domain audit log record."""

    entity_type: str = Field(..., min_length=1, max_length=100)
    entity_id: uuid.UUID | None = None
    entity_name: str | None = Field(
        default=None,
        max_length=255,
        validation_alias=AliasChoices("entity_name", "entityName"),
    )
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

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    entity_type: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "entity_type", "entityType", "module_slug", "moduleSlug"
        ),
    )
    entity_id: uuid.UUID | None = Field(
        default=None,
        validation_alias=AliasChoices("entity_id", "entityId"),
    )
    entity_name: str | None = Field(
        default=None,
        validation_alias=AliasChoices("entity_name", "entityName"),
    )
    action: str | None = None
    actor_id: uuid.UUID | None = Field(
        default=None,
        validation_alias=AliasChoices("actor_id", "actorId", "user_id", "userId"),
    )
    from_date: datetime | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "from_date", "fromDate", "createdAtFrom", "created_at_from"
        ),
    )
    to_date: datetime | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "to_date", "toDate", "createdAtTo", "created_at_to"
        ),
    )
    page: int = Field(default=1, ge=1, le=1000)
    limit: int = Field(default=20, ge=1, le=100)
