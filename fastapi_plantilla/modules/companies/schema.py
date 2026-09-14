import uuid

from pydantic import BaseModel, Field

from fastapi_plantilla.core.crud.schema import (
    AuditFieldsSchema,
    PaginationParams,
    UserReference,
)

__all__ = [
    "CompaniesPaginationParams",
    "CompanyCreate",
    "CompanyResponse",
    "CompanyUpdate",
]


class CompanyResponse(AuditFieldsSchema):
    """Detailed response schema for a company."""

    id: uuid.UUID
    name: str
    nif: str
    sector: str | None = None
    website: str | None = None
    description: str | None = None
    owner: UserReference | None = None


class CompanyCreate(BaseModel):
    """Payload for creating a new company."""

    name: str = Field(..., min_length=1, max_length=150)
    nif: str = Field(..., min_length=1, max_length=50)
    sector: str | None = Field(default=None, max_length=100)
    website: str | None = Field(default=None, max_length=255)
    description: str | None = None
    owner_id: uuid.UUID | None = None


class CompanyUpdate(BaseModel):
    """Payload for updating an existing company."""

    name: str | None = Field(default=None, min_length=1, max_length=150)
    nif: str | None = Field(default=None, min_length=1, max_length=50)
    sector: str | None = Field(default=None, max_length=100)
    website: str | None = Field(default=None, max_length=255)
    description: str | None = None
    owner_id: uuid.UUID | None = None
    version: int | None = None


class CompaniesPaginationParams(PaginationParams):
    """Extended query parameters for company list filtering."""

    name: str | None = None
    nif: str | None = None
    sector: str | None = None
