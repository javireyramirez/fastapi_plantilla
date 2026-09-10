import uuid

from pydantic import BaseModel, ConfigDict, Field

from fastapi_plantilla.core.crud.schema import AuditFieldsSchema, PaginationParams

__all__ = [
    "CompaniesPaginationParams",
    "CompanyCreate",
    "CompanyOwnerResponse",
    "CompanyResponse",
    "CompanyUpdate",
]


class CompanyOwnerResponse(BaseModel):
    """Minimal owner representation for company relations."""

    id: uuid.UUID
    name: str | None = None
    email: str | None = None

    model_config = ConfigDict(from_attributes=True)


class CompanyResponse(AuditFieldsSchema):
    """Detailed response schema for a company."""

    id: uuid.UUID
    name: str
    nif: str
    sector: str | None = None
    website: str | None = None
    description: str | None = None
    owner_id: uuid.UUID | None = None
    owner: CompanyOwnerResponse | None = None


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

    nif: str | None = None
    sector: str | None = None
