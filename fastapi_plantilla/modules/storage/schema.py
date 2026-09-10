import uuid
from typing import Self

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from fastapi_plantilla.core.crud.schema import AuditFieldsSchema, PaginationParams

__all__ = [
    "ConfirmUploadRequest",
    "DocumentFilterParams",
    "DocumentResponse",
    "DocumentUpdateSchema",
    "PresignedDownloadResponse",
    "PresignedUploadRequest",
    "PresignedUploadResponse",
    "ZipDownloadRequest",
]


class PresignedUploadRequest(BaseModel):
    """Payload for requesting a presigned direct-upload URL."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    entity_type: str = Field(
        ...,
        min_length=1,
        max_length=50,
        validation_alias=AliasChoices("entity_type", "entityType"),
    )
    entity_id: uuid.UUID = Field(
        ...,
        validation_alias=AliasChoices("entity_id", "entityId"),
    )
    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        validation_alias=AliasChoices("name", "filename", "fileName"),
    )
    content_type: str | None = Field(
        default=None,
        max_length=100,
        validation_alias=AliasChoices("content_type", "contentType", "mimeType"),
    )
    size_bytes: int | None = Field(
        default=None,
        ge=0,
        validation_alias=AliasChoices(
            "size_bytes", "sizeBytes", "file_size", "fileSize", "size"
        ),
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
        validation_alias=AliasChoices(
            "size_bytes", "sizeBytes", "file_size", "fileSize", "size"
        ),
    )
    content_type: str | None = Field(
        default=None,
        max_length=100,
        validation_alias=AliasChoices("content_type", "contentType", "mimeType"),
    )


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
    owner_id: uuid.UUID | None = None


class DocumentFilterParams(PaginationParams):
    """Pagination and filter parameters for documents list."""

    entity_type: str | None = Field(default=None, max_length=50)
    entity_id: uuid.UUID | None = None
    is_uploaded: bool | None = None


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
