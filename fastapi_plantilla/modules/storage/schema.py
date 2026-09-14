import uuid
from typing import Any, Self

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    field_validator,
    model_validator,
)

from fastapi_plantilla.core.crud.schema import (
    AuditFieldsSchema,
    PaginationParams,
    PrincipalEntityModule,
)
from fastapi_plantilla.modules.trash.service import resolve_module

__all__ = [
    "ConfirmUploadRequest",
    "CreateExternalUrlRequest",
    "DocumentFilterParams",
    "DocumentResponse",
    "DocumentUpdateSchema",
    "PresignedDownloadResponse",
    "PresignedUploadRequest",
    "PresignedUploadResponse",
    "ZipDownloadRequest",
]


class CreateExternalUrlRequest(BaseModel):
    """Payload for registering an external URL (Drive, OneDrive, Dropbox, etc.)."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    entity_type: str = Field(..., min_length=1, max_length=50)
    entity_id: uuid.UUID
    url: HttpUrl
    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        validation_alias=AliasChoices("name", "title", "filename"),
    )
    description: str | None = Field(default=None, max_length=1000)


class PresignedUploadRequest(BaseModel):
    """Payload for requesting a presigned direct-upload URL."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    entity_type: str = Field(..., min_length=1, max_length=50)
    entity_id: uuid.UUID
    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        validation_alias=AliasChoices("name", "filename"),
    )
    content_type: str | None = Field(default=None, max_length=100)
    size_bytes: int | None = Field(
        default=None,
        ge=0,
        validation_alias=AliasChoices("size_bytes", "file_size", "size"),
    )
    description: str | None = Field(default=None, max_length=1000)


class PresignedUploadResponse(BaseModel):
    """Result containing presigned upload URL and temporary document ID."""

    document_id: uuid.UUID
    upload_url: str
    file_key: str
    expires_in: int
    method: str = "PUT"


class ConfirmUploadRequest(BaseModel):
    """Optional payload when confirming a completed direct upload."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    size_bytes: int | None = Field(
        default=None,
        ge=0,
        validation_alias=AliasChoices("size_bytes", "file_size", "size"),
    )
    content_type: str | None = Field(default=None, max_length=100)


class PresignedDownloadResponse(BaseModel):
    """Result containing presigned download URL and basic document headers."""

    document_id: uuid.UUID
    download_url: str
    expires_in: int
    name: str
    content_type: str


class DocumentUpdateSchema(BaseModel):
    """Payload for updating document metadata."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1000)


class DocumentResponse(AuditFieldsSchema):
    """Complete document representation."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    entity_type: str
    entity_id: uuid.UUID
    name: str
    file_key: str
    content_type: str
    size_bytes: int
    extension: str | None = None
    is_uploaded: bool
    description: str | None = None
    external_url: str | None = None
    owner_id: uuid.UUID | None = None
    module_principal_entity: PrincipalEntityModule | dict[str, Any] | None = None

    @model_validator(mode="after")
    def populate_module_principal_entity(self) -> Self:
        """Resolve and attach module principal metadata if not explicitly provided."""
        if self.module_principal_entity is None and self.entity_type:
            target_mod = resolve_module(self.entity_type)
            code = target_mod.code if target_mod else self.entity_type
            name = target_mod.name if target_mod else self.entity_type.capitalize()
            self.module_principal_entity = PrincipalEntityModule(
                code=code,
                name=name,
                entity_name=None,
                entity_id=self.entity_id,
            )
        return self


class DocumentFilterParams(PaginationParams):
    """Pagination and filter parameters for documents list."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    entity_type: str | None = Field(default=None, max_length=50)
    entity_id: uuid.UUID | None = None
    is_uploaded: bool | None = None
    content_type: str | None = None
    content_types: list[str] | None = None
    size_min: int | None = None
    size_max: int | None = None

    @field_validator("content_types", mode="before")
    @classmethod
    def parse_content_types(cls, v: Any) -> list[str] | None:
        """Normalize string separated by commas or sequence of types to list."""
        if v is None:
            return None
        if isinstance(v, str):
            items = [t.strip() for t in v.split(",") if t.strip()]
            return items if items else None
        if isinstance(v, (list, tuple, set)):
            items = [str(t).strip() for t in v if str(t).strip()]
            return items if items else None
        return v

    @model_validator(mode="after")
    def merge_content_type(self) -> Self:
        """Merge singular content_type into content_types list if not provided."""
        if not self.content_types and self.content_type:
            items = [t.strip() for t in self.content_type.split(",") if t.strip()]
            self.content_types = items if items else None
        return self


class ZipDownloadRequest(BaseModel):
    """Payload specifying documents to bundle in a ZIP archive."""

    document_ids: list[uuid.UUID] | None = Field(default=None, max_length=100)
    entity_type: str | None = Field(default=None, max_length=50)
    entity_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def validate_targets(self) -> Self:
        """Ensure either document IDs or entity association is provided."""
        has_ids = bool(self.document_ids)
        has_entity = bool(self.entity_type and self.entity_id)
        if not has_ids and not has_entity:
            raise ValueError(
                "Either document_ids or both entity_type and entity_id "
                "must be provided."
            )
        return self
