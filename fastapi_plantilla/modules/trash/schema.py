import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

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
    "PrincipalEntityModule",
    "TrashDeletorResponse",
    "TrashFilterParams",
    "TrashItemResponse",
    "TrashModuleResponse",
    "TrashPurgeResponse",
]


class PrincipalEntityModule(BaseModel):
    """Metadata of the parent entity to which a document belongs."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    code: str
    name: str
    entity_name: str | None = None
    entity_id: uuid.UUID | None = None


class TrashDeletorResponse(BaseModel):
    """Information of the actor who moved the item to trash."""

    model_config = ConfigDict(from_attributes=True)

    name: str | None = None
    email: str | None = None


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
    target_entity_type: str | None = None
    target_entity_id: uuid.UUID | None = None
    target_entity_name: str | None = None
    module: TrashModuleResponse | dict[str, Any] | None = None
    module_principal_entity: PrincipalEntityModule | dict[str, Any] | None = None
    owner_id: uuid.UUID | None = None
    deleted_by: str | None = None
    deleted_by_name: str | None = None
    deleted_by_email: str | None = None
    deletor: TrashDeletorResponse | None = None
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

    entity_type: str | None = Field(
        default=None,
        max_length=50,
        validation_alias=AliasChoices(
            "entity_type", "entityType", "module_slug", "moduleSlug"
        ),
    )
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
