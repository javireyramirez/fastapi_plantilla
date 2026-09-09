import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from fastapi_plantilla.core.crud.schema import PaginationParams
from fastapi_plantilla.core.mixins import RecordStatus

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


class CompanyResponse(BaseModel):
    """Detailed response schema for a company."""

    id: uuid.UUID
    name: str
    nif: str
    sector: str | None = None
    website: str | None = None
    description: str | None = None
    owner_id: uuid.UUID | None = None
    owner: CompanyOwnerResponse | None = None
    status: RecordStatus = RecordStatus.ACTIVE
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
    restored_at: datetime | None = None
    created_by: str | None = None
    updated_by: str | None = None
    deleted_by: str | None = None
    restored_by: str | None = None
    version: int = 1

    model_config = ConfigDict(from_attributes=True)


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
