import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from fastapi_plantilla.core.crud.schema import (
    BulkIdsRequest,
    BulkResponse,
    PaginationParams,
    PrincipalEntityModule,
    UserReference,
)

DEFAULT_TRASH_PURGE_LIMIT: int = 500

__all__ = [
    "DEFAULT_TRASH_PURGE_LIMIT",
    "BulkTrashActionRequest",
    "BulkTrashResponse",
    "PrincipalEntityModule",
    "TrashFilterParams",
    "TrashItemResponse",
    "TrashModuleResponse",
    "TrashPurgeResponse",
]


class TrashModuleResponse(BaseModel):
    """Metadata of the system module associated with a trash item."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID | None = None
    code: str
    slug: str | None = None
    name: str
    description: str | None = None
    icon: str | None = None
    category: str | None = None


class TrashItemResponse(BaseModel):
    """Public representation of an item in the centralized trash bin."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)
    id: uuid.UUID
    entity_type: str
    entity_id: uuid.UUID
    name: str
    module_principal_entity: PrincipalEntityModule | dict[str, Any] | None = None
    owner_id: uuid.UUID | None = None
    deletor: UserReference | None = None
    deleted_at: datetime
    expires_at: datetime
    details: str | None = None
    created_at: datetime
    updated_at: datetime

    @property
    def is_expired(self) -> bool:
        """Check whether the item has passed its retention deadline."""
        return self.expires_at <= datetime.now(UTC)


class TrashFilterParams(PaginationParams):
    """Pagination and criteria filters for listing trash items."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    entity_type: str | None = Field(default=None, max_length=50)
    category: str | None = Field(default=None, max_length=50)
    is_expired: bool | None = None
    deleted_at_from: datetime | None = None
    deleted_at_to: datetime | None = None
    expires_at_from: datetime | None = None
    expires_at_to: datetime | None = None
    q: str | None = Field(default=None, max_length=100)


class BulkTrashActionRequest(BulkIdsRequest):
    """Payload containing trash item IDs for bulk restore or purge operations."""


class BulkTrashResponse(BulkResponse):
    """Result payload for bulk trash operations."""


class TrashPurgeResponse(BaseModel):
    """Summary of purged trash items count."""

    purged_count: int = Field(..., ge=0)
    message: str
