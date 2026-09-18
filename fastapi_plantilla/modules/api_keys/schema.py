import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from fastapi_plantilla.core.crud.schema import PaginationParams

__all__ = [
    "ApiKeyCreate",
    "ApiKeyCreatedResponse",
    "ApiKeyPaginationParams",
    "ApiKeyResponse",
    "ApiKeyUpdate",
]


class ApiKeyCreate(BaseModel):
    """Payload to create a new external programmatic API Key."""

    name: str = Field(
        ...,
        min_length=1,
        max_length=150,
        description="Friendly label for this API key",
    )
    scopes: list[str] = Field(
        default_factory=list,
        description="List of granted scope actions (e.g. ['*'], ['companies:read'])",
    )
    expires_at: datetime | None = Field(
        default=None,
        description="Optional expiration timestamp",
    )


class ApiKeyResponse(BaseModel):
    """Public representation of an API Key."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    prefix: str
    masked_key: str
    scopes: list[str]
    is_active: bool
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    owner_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class ApiKeyCreatedResponse(ApiKeyResponse):
    """Response returned upon API key generation containing the raw plain secret."""

    raw_key: str = Field(
        ...,
        description="Plain secret API key. Store safely; will never be shown again.",
    )


class ApiKeyUpdate(BaseModel):
    """Payload to update an existing API Key."""

    name: str | None = Field(default=None, min_length=1, max_length=150)
    scopes: list[str] | None = None
    is_active: bool | None = None
    expires_at: datetime | None = None


class ApiKeyPaginationParams(PaginationParams):
    """Query filters for paginating API keys."""

    is_active: bool | None = Field(
        default=None,
        description="Filter by active/revoked status",
    )
