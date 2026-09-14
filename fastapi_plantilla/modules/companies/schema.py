import uuid
from typing import Any

from pydantic import BaseModel, Field, field_validator

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
    sector: list[str] | None = None

    @field_validator("sector", mode="before")
    @classmethod
    def parse_sector(cls, v: Any) -> list[str] | None:
        """Normalize comma-separated strings or sequences into a list of sectors."""
        if v is None:
            return None
        if isinstance(v, str):
            items = [s.strip() for s in v.split(",") if s.strip()]
            return items if items else None
        if isinstance(v, (list, tuple, set)):
            result: list[str] = []
            for item in v:
                if isinstance(item, str):
                    result.extend([s.strip() for s in item.split(",") if s.strip()])
                elif item is not None:
                    result.append(str(item).strip())
            return result if result else None
        return v
