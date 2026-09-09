import uuid
from typing import Any, ClassVar

from fastapi import Depends, HTTPException, status
from pydantic import BaseModel

from fastapi_plantilla.core.crud.schema import (
    PaginationParams,
    ScopeContext,
    WriteOptions,
)
from fastapi_plantilla.core.crud.service_owned import BaseOwnedService
from fastapi_plantilla.modules.companies.models import Company
from fastapi_plantilla.modules.companies.repository import CompanyRepository

__all__ = ["CompanyService"]


class CompanyService(BaseOwnedService[Company]):
    """Business service governing company lifecycle, RBAC scopes, and validation."""

    resource_name: str = "Company"
    display_field: str = "name"
    search_fields: ClassVar[list[str]] = ["name", "nif"]
    owner_field: str = "owner_id"

    def __init__(self, repository: CompanyRepository = Depends()) -> None:
        super().__init__(repository)
        self.repository: CompanyRepository = repository

    def build_where_filters(self, params: PaginationParams) -> list[Any]:
        """Build query clauses including sector and exact NIF matching."""
        clauses = super().build_where_filters(params)
        sector_val = getattr(params, "sector", None)
        if sector_val:
            clauses.append(Company.sector == sector_val)
        nif_val = getattr(params, "nif", None)
        if nif_val:
            clauses.append(Company.nif == nif_val)
        return clauses

    async def create(
        self,
        data: BaseModel | dict[str, Any],
        user_id: str | uuid.UUID | None = None,
        owner_id: uuid.UUID | None = None,
        scope: ScopeContext | None = None,
        allow_immutable: bool = False,
        options: WriteOptions | None = None,
    ) -> Company:
        """Create company enforcing NIF uniqueness and default ownership."""
        payload = (
            data.model_dump(exclude_unset=True)
            if isinstance(data, BaseModel)
            else dict(data)
        )
        nif = payload.get("nif")
        if nif and await self.repository.get_by_nif(nif):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Company with NIF '{nif}' already exists",
            )

        effective_owner = payload.get("owner_id") or owner_id
        if effective_owner is None and user_id:
            effective_owner = (
                uuid.UUID(str(user_id)) if isinstance(user_id, str) else user_id
            )

        return await super().create(
            payload,
            user_id=user_id,
            owner_id=effective_owner,
            scope=scope,
            allow_immutable=allow_immutable,
            options=options,
        )

    async def update(
        self,
        id: uuid.UUID,
        data: BaseModel | dict[str, Any],
        *where: Any,
        expected_version: int | None = None,
        user_id: str | uuid.UUID | None = None,
        scope: ScopeContext | None = None,
        options: WriteOptions | None = None,
        allow_immutable: bool = False,
    ) -> Company:
        """Update company verifying NIF conflict and optimistic locking."""
        payload = (
            data.model_dump(exclude_unset=True)
            if isinstance(data, BaseModel)
            else dict(data)
        )
        if "nif" in payload:
            existing = await self.repository.get_by_nif(payload["nif"])
            if existing and existing.id != id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Company with NIF '{payload['nif']}' already exists",
                )

        return await super().update(
            id,
            data,
            *where,
            expected_version=expected_version,
            user_id=user_id,
            scope=scope,
            options=options,
            allow_immutable=allow_immutable,
        )
