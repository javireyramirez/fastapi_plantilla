import uuid
from datetime import UTC, datetime
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

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
    "TrashDeletorResponse",
    "TrashFilterParams",
    "TrashItemResponse",
    "TrashModuleResponse",
    "TrashPurgeResponse",
]


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
    target_entity_type: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "target_entity_type", "targetEntityType", "entity_module", "entityModule"
        ),
    )
    targetEntityType: str | None = Field(default=None)  # noqa: N815
    target_entity_id: uuid.UUID | None = Field(
        default=None,
        validation_alias=AliasChoices("target_entity_id", "targetEntityId"),
    )
    targetEntityId: uuid.UUID | None = Field(default=None)  # noqa: N815
    target_entity_name: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "target_entity_name", "targetEntityName", "entity_name", "entityName"
        ),
    )
    targetEntityName: str | None = Field(default=None)  # noqa: N815
    entity_name: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "entity_name", "entityName", "target_entity_name", "targetEntityName"
        ),
    )
    entityName: str | None = Field(default=None)  # noqa: N815
    module: TrashModuleResponse | dict[str, Any] | None = None
    module_code: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "module_code", "moduleCode", "module_slug", "moduleSlug"
        ),
    )
    moduleCode: str | None = Field(default=None)  # noqa: N815
    module_name: str | None = Field(
        default=None,
        validation_alias=AliasChoices("module_name", "moduleName"),
    )
    moduleName: str | None = Field(default=None)  # noqa: N815
    module_icon: str | None = Field(
        default=None,
        validation_alias=AliasChoices("module_icon", "moduleIcon"),
    )
    moduleIcon: str | None = Field(default=None)  # noqa: N815
    module_category: str | None = Field(
        default=None,
        validation_alias=AliasChoices("module_category", "moduleCategory"),
    )
    moduleCategory: str | None = Field(default=None)  # noqa: N815
    owner_id: uuid.UUID | None = None
    deleted_by: str | None = None
    deleted_by_name: str | None = None
    deleted_by_email: str | None = None
    deletor: TrashDeletorResponse | None = None
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

    @model_validator(mode="after")
    def sync_entity_and_module_fields(self) -> "TrashItemResponse":
        """Synchronize dual-cased and alias fields for entity and module."""
        name_val = (
            self.target_entity_name
            or self.targetEntityName
            or self.entity_name
            or self.entityName
        )
        if name_val:
            self.target_entity_name = name_val
            self.targetEntityName = name_val
            self.entity_name = name_val
            self.entityName = name_val

        type_val = self.target_entity_type or self.targetEntityType
        if type_val:
            self.target_entity_type = type_val
            self.targetEntityType = type_val

        id_val = self.target_entity_id or self.targetEntityId
        if id_val:
            self.target_entity_id = id_val
            self.targetEntityId = id_val

        if isinstance(self.module, TrashModuleResponse):
            self.module_code = self.module_code or self.module.code
            self.module_name = self.module_name or self.module.name
            self.module_icon = self.module_icon or self.module.icon
            self.module_category = self.module_category or self.module.category
        elif isinstance(self.module, dict):
            self.module_code = self.module_code or self.module.get("code")
            self.module_name = self.module_name or self.module.get("name")
            self.module_icon = self.module_icon or self.module.get("icon")
            self.module_category = self.module_category or self.module.get("category")

        self.moduleCode = self.module_code or self.moduleCode
        self.module_code = self.moduleCode
        self.moduleName = self.module_name or self.moduleName
        self.module_name = self.moduleName
        self.moduleIcon = self.module_icon or self.moduleIcon
        self.module_icon = self.moduleIcon
        self.moduleCategory = self.module_category or self.moduleCategory
        self.module_category = self.moduleCategory

        return self


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
