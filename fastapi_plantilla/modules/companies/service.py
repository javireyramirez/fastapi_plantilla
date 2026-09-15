import uuid
from collections.abc import Sequence
from typing import Any, ClassVar

from fastapi import Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError

from fastapi_plantilla.core.crud.schema import (
    BulkResponse,
    PaginationParams,
    ScopeContext,
    WriteOptions,
)
from fastapi_plantilla.core.crud.service_owned import BaseOwnedService
from fastapi_plantilla.core.mixins import RecordStatus
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
        """Build query clauses including name, nif, and sector."""
        clauses = super().build_where_filters(params)
        if name_val := getattr(params, "name", None):
            clauses.append(self.build_string_filter("name", name_val))
        if nif_val := getattr(params, "nif", None):
            clauses.append(self.build_string_filter("nif", nif_val))
        if sector_val := getattr(params, "sector", None):
            limited = sector_val[:1000] if isinstance(sector_val, list) else sector_val
            clause = (
                Company.sector.in_(limited)
                if isinstance(limited, list)
                else Company.sector == limited
            )
            clauses.append(clause)
        return clauses

    async def _validate_nif_uniqueness(
        self,
        nif: str | None,
        exclude_id: uuid.UUID | None = None,
        company_name: str | None = None,
    ) -> None:
        """Raise 409 Conflict if an active company already uses the given NIF."""
        if not nif:
            return
        existing = await self.repository.get_by_nif(nif)
        if existing and (exclude_id is None or existing.id != exclude_id):
            detail = (
                f"Cannot restore company '{company_name}': NIF '{nif}' "
                "is already in use by an active company."
                if company_name
                else f"Company with NIF '{nif}' already exists"
            )
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)

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
        nif = (
            data.nif
            if isinstance(data, BaseModel) and hasattr(data, "nif")
            else (data.get("nif") if isinstance(data, dict) else None)
        )
        await self._validate_nif_uniqueness(nif)
        try:
            return await super().create(
                data,
                user_id=user_id,
                owner_id=owner_id,
                scope=scope,
                allow_immutable=allow_immutable,
                options=options,
            )
        except IntegrityError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Company with NIF '{nif}' already exists",
            ) from exc

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
        nif = (
            getattr(data, "nif", None)
            if isinstance(data, BaseModel)
            and "nif" in getattr(data, "model_fields_set", set())
            else (data.get("nif") if isinstance(data, dict) else None)
        )
        if nif:
            await self._validate_nif_uniqueness(nif, exclude_id=id)

        try:
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
        except IntegrityError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Company with NIF '{nif}' already exists"
                    if nif
                    else "Company with this NIF already exists"
                ),
            ) from exc

    async def restore(
        self,
        id: uuid.UUID,
        *where: Any,
        user_id: str | uuid.UUID | None = None,
        scope: ScopeContext | None = None,
        options: WriteOptions | None = None,
    ) -> Company:
        """Restore company verifying NIF does not collide with an active company."""
        opts = self._resolve_write_options(options, user_id, scope)
        scope_filters = self.build_scope_filters(opts.scope)
        company = await self.repository.find_first(
            Company.id == id, *scope_filters, *where
        )
        if company and company.status == RecordStatus.TRASHED:
            await self._validate_nif_uniqueness(
                company.nif, exclude_id=company.id, company_name=company.name
            )
        try:
            return await super().restore(
                id, *where, user_id=user_id, scope=scope, options=options
            )
        except IntegrityError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Cannot restore company: NIF is already in use "
                    "by an active company."
                ),
            ) from exc

    async def bulk_create(
        self,
        items: Sequence[BaseModel | dict[str, Any]],
        user_id: str | uuid.UUID | None = None,
        owner_id: uuid.UUID | None = None,
        scope: ScopeContext | None = None,
        allow_immutable: bool = False,
        options: WriteOptions | None = None,
    ) -> BulkResponse:
        """Bulk create companies validating unique NIFs in batch and database."""
        nifs: list[str] = []
        for it in items:
            raw_nif = (
                getattr(it, "nif", None) if isinstance(it, BaseModel) else it.get("nif")
            )
            if raw_nif:
                nifs.append(str(raw_nif).strip())

        if len(nifs) != len(set(nifs)):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Duplicate NIF found within bulk create payload",
            )

        try:
            return await super().bulk_create(
                items,
                user_id=user_id,
                owner_id=owner_id,
                scope=scope,
                allow_immutable=allow_immutable,
                options=options,
            )
        except IntegrityError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="One or more companies violate unique NIF constraint",
            ) from exc
