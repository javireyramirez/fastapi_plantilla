from __future__ import annotations

import enum
import math
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from fastapi_plantilla.core.mixins import RecordStatus

__all__ = [
    "DEFAULT_MAX_BULK_LIMIT",
    "AuditEntry",
    "AuditFieldsSchema",
    "AuditLevel",
    "BulkIdsRequest",
    "BulkResponse",
    "ExportFormat",
    "ExportRequest",
    "ListItemResponse",
    "ListQueryParams",
    "MessageResponse",
    "PaginatedResponse",
    "PaginationMeta",
    "PaginationParams",
    "ScopeContext",
    "ScopeType",
    "SortOrder",
    "UserReference",
    "WriteOptions",
]

DEFAULT_MAX_BULK_LIMIT: int = 1000


class ScopeType(enum.StrEnum):
    """Enumeration of RBAC multi-tenancy access scopes."""

    GLOBAL = "GLOBAL"
    TEAM = "TEAM"
    OWN = "OWN"


class SortOrder(enum.StrEnum):
    """Enumeration of sort order directions."""

    ASC = "asc"
    DESC = "desc"


class ExportFormat(enum.StrEnum):
    """Enumeration of supported export file formats."""

    CSV = "csv"
    EXCEL = "excel"
    JSON = "json"


class PaginationParams(BaseModel):
    """Query parameters for paginated list requests."""

    page: int = Field(default=1, ge=1, le=1000)
    limit: int = Field(default=10, ge=1, le=100)
    sort_by: str = "created_at"
    sort_order: SortOrder = SortOrder.DESC
    is_trash: bool = False
    search: str | None = Field(default=None, max_length=100)
    created_at_from: datetime | None = None
    created_at_to: datetime | None = None


class PaginationMeta(BaseModel):
    """Metadata detailing pagination state and total counts."""

    page: int = Field(..., ge=1)
    limit: int = Field(..., ge=1)
    total: int = Field(..., ge=0)
    total_pages: int = Field(..., ge=0)
    has_next: bool
    has_prev: bool

    @classmethod
    def create(cls, page: int, limit: int, total: int) -> PaginationMeta:
        """Calculate and build pagination metadata from totals."""
        total_pages = math.ceil(total / limit) if limit > 0 else 0
        return cls(
            page=page,
            limit=limit,
            total=total,
            total_pages=total_pages,
            has_next=page < total_pages,
            has_prev=page > 1 and total_pages > 0,
        )


class PaginatedResponse[T](BaseModel):
    """Standard envelope for paginated list responses."""

    model_config = ConfigDict(arbitrary_types_allowed=True, from_attributes=True)

    data: list[T]
    meta: PaginationMeta


class ListQueryParams(BaseModel):
    """Query parameters for lightweight dropdown and combobox selector requests."""

    limit: int = Field(default=20, ge=1, le=100)
    search: str | None = Field(default=None, max_length=100)
    sort_by: str = "name"
    sort_order: SortOrder = SortOrder.ASC
    is_trash: bool = False


class ListItemResponse(BaseModel):
    """Minimal payload for select and combobox dropdown options."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    extra: dict[str, Any] | None = None


class BulkIdsRequest(BaseModel):
    """Payload containing a list of item IDs for bulk operations."""

    ids: list[uuid.UUID] = Field(..., min_length=1, max_length=DEFAULT_MAX_BULK_LIMIT)


class BulkResponse(BaseModel):
    """Result payload for bulk operations returning the count of affected records."""

    count: int = Field(..., ge=0)
    message: str | None = None


class ExportRequest(BaseModel):
    """Request payload configuring data export format, filters, and columns."""

    ids: list[uuid.UUID] | None = None
    filters: dict[str, Any] = Field(default_factory=dict)
    columns: list[str] | None = None
    format: ExportFormat = ExportFormat.CSV
    sort_by: str = "created_at"
    sort_order: SortOrder = SortOrder.DESC
    is_trash: bool = False


class AuditFieldsSchema(BaseModel):
    """Base schema providing audit timestamps, actor references, and record status."""

    model_config = ConfigDict(from_attributes=True)

    status: RecordStatus = RecordStatus.ACTIVE
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
    restored_at: datetime | None = None
    created_by: str | None = None
    updated_by: str | None = None
    deleted_by: str | None = None
    restored_by: str | None = None
    version: int = Field(default=1, ge=1)


class UserReference(BaseModel):
    """Safe public representation of an associated user or owner."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    email: str
    image: str | None = None


class MessageResponse(BaseModel):
    """Generic status and confirmation message response."""

    message: str
    detail: str | None = None


class ScopeContext(BaseModel):
    """Context object encapsulating RBAC scope, user ID, and team memberships."""

    scope: ScopeType = ScopeType.GLOBAL
    user_id: uuid.UUID | None = None
    team_ids: list[uuid.UUID] = Field(default_factory=list)
    teammate_ids: list[uuid.UUID] = Field(default_factory=list)
    is_super_admin: bool = False


class WriteOptions(BaseModel):
    """Execution options passed to repository and service write operations."""

    user_id: str | uuid.UUID | None = None
    scope: ScopeContext | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    description: str | None = None
    expected_version: int | None = Field(default=None, ge=1)
    include: dict[str, Any] | None = None


class AuditLevel(enum.StrEnum):
    """Granular audit tracking levels per module/service."""

    FULL = "full"  # Completa: metadata + diffs campo por campo
    PARTIAL = "partial"  # Parcial: metadata de acción, sin diffs de campos
    NONE = "none"  # Sin auditoría


class AuditEntry(BaseModel):
    """Decoupled audit log event payload."""

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
