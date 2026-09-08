import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from fastapi_plantilla.core.crud.schema import (
    BulkIdsRequest,
    BulkResponse,
    PaginationParams,
)

DEFAULT_TRASH_PURGE_LIMIT: int = 500

__all__ = [
    "DEFAULT_TRASH_PURGE_LIMIT",
    "BulkTrashActionRequest",
    "BulkTrashResponse",
    "TrashFilterParams",
    "TrashItemResponse",
    "TrashPurgeResponse",
]


class TrashItemResponse(BaseModel):
    """Public representation of an item in the centralized trash bin."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    entity_type: str
    entity_id: uuid.UUID
    name: str
    owner_id: uuid.UUID | None = None
    deleted_by: str | None = None
    deleted_at: datetime
    expires_at: datetime
    data_backup: dict[str, Any] | None = None
    details: str | None = None
    created_at: datetime
    updated_at: datetime

    @property
    def is_expired(self) -> bool:
        """Check whether the item has passed its retention deadline."""
        return self.expires_at <= datetime.now(UTC)


class TrashFilterParams(PaginationParams):
    """Pagination and criteria filters for listing trash items."""

    entity_type: str | None = Field(default=None, max_length=50)
    is_expired: bool | None = None
    deleted_at_from: datetime | None = None
    deleted_at_to: datetime | None = None
    q: str | None = Field(default=None, max_length=100)


class BulkTrashActionRequest(BulkIdsRequest):
    """Payload containing trash item IDs for bulk restore or purge operations."""


class BulkTrashResponse(BulkResponse):
    """Result payload for bulk trash operations."""


class TrashPurgeResponse(BaseModel):
    """Summary of purged trash items count."""

    purged_count: int = Field(..., ge=0)
    message: str
