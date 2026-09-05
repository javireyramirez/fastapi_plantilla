import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, status
from pydantic import BaseModel

from fastapi_plantilla.core.crud.schema import (
    BulkIdsRequest,
    BulkResponse,
    ListItemResponse,
    ListQueryParams,
    PaginationParams,
    ScopeContext,
)
from fastapi_plantilla.core.crud.service_base import BaseCRUDService
from fastapi_plantilla.core.database import Base
from fastapi_plantilla.core.mixins import RecordStatus

__all__ = ["BaseAuditService"]


class BaseAuditService[ModelT: Base](BaseCRUDService[ModelT]):
    """Base business service for CRUD Audit Service."""

    # ==========================================
    # 1. FILTROS Y LECTURAS AUDITADAS
    # ==========================================

    def get_status_filter(self, is_trash: bool) -> Any | None:
        """Return filter clause for active or trashed records."""
        status_col = self._get_column("status")
        if status_col is None:
            return None
        return (
            status_col == RecordStatus.TRASHED
            if is_trash
            else status_col != RecordStatus.TRASHED
        )

    def build_where_filters(self, params: PaginationParams) -> list[Any]:
        """Build query filter clauses including trash status filtering."""
        clauses = super().build_where_filters(params)
        status_clause = self.get_status_filter(params.is_trash)
        if status_clause is not None:
            clauses.append(status_clause)
        return clauses

    async def find_list(
        self,
        params: ListQueryParams,
        *where: Any,
        scope: ScopeContext | None = None,
        display_field: str | None = None,
    ) -> list[ListItemResponse]:
        """Fetch a lightweight list of items for select/combobox dropdowns."""
        status_clause = self.get_status_filter(params.is_trash)
        extra = (status_clause,) if status_clause is not None else ()
        return await super().find_list(
            params, *where, *extra, scope=scope, display_field=display_field
        )

    # ==========================================
    # 2. ESCRITURAS CON AUDITORÍA
    # ==========================================

    def _stamp_actors(
        self,
        payload: dict[str, Any],
        user_id: str | uuid.UUID | None,
        is_create: bool = False,
    ) -> None:
        """Stamp created_by and updated_by fields on payload if columns exist."""
        uid_str = str(user_id) if user_id else None
        if is_create and self._get_column("created_by") is not None:
            if uid_str:
                payload["created_by"] = uid_str
            else:
                payload.pop("created_by", None)
        if self._get_column("updated_by") is not None:
            if uid_str:
                payload["updated_by"] = uid_str
            else:
                payload.pop("updated_by", None)

    async def create(
        self,
        data: BaseModel | dict[str, Any],
        user_id: str | uuid.UUID | None = None,
        owner_id: uuid.UUID | None = None,
        scope: ScopeContext | None = None,
        allow_immutable: bool = False,
    ) -> ModelT:
        """Create a record with created_by and updated_by actor stamping."""
        payload = data.model_dump() if isinstance(data, BaseModel) else dict(data)
        if not allow_immutable and self._get_column("status") is not None:
            payload["status"] = RecordStatus.ACTIVE
        self._stamp_actors(payload, user_id, is_create=True)
        return await super().create(
            data=payload,
            user_id=user_id,
            owner_id=owner_id,
            scope=scope,
            allow_immutable=allow_immutable,
        )

    async def update(
        self,
        id: uuid.UUID,
        data: BaseModel | dict[str, Any],
        *where: Any,
        expected_version: int | None = None,
        user_id: str | uuid.UUID | None = None,
        scope: ScopeContext | None = None,
        allow_immutable: bool = False,
    ) -> ModelT:
        """Update a record with updated_by actor stamping and trash protection."""
        payload = (
            data.model_dump(exclude_unset=True)
            if isinstance(data, BaseModel)
            else dict(data)
        )
        extra_where = list(where)
        status_col = self._get_column("status")

        if not allow_immutable and status_col is not None:
            extra_where.append(status_col != RecordStatus.TRASHED)

        self._stamp_actors(payload, user_id, is_create=False)

        try:
            return await super().update(
                id,
                payload,
                *extra_where,
                expected_version=expected_version,
                scope=scope,
                allow_immutable=allow_immutable,
            )
        except HTTPException as exc:
            if (
                not allow_immutable
                and status_col is not None
                and exc.status_code
                in (status.HTTP_404_NOT_FOUND, status.HTTP_403_FORBIDDEN)
                and await self.repository.exists(
                    self.repository.pk == id, status_col == RecordStatus.TRASHED
                )
            ):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot update a record that is in the trash bin",
                ) from exc
            raise

    async def delete(
        self,
        id: uuid.UUID,
        *where: Any,
        user_id: str | uuid.UUID | None = None,
        scope: ScopeContext | None = None,
    ) -> ModelT:
        """Safely soft-delete (trash) record instead of physical delete."""
        return await self.trash(id, *where, user_id=user_id, scope=scope)

    async def bulk_create(
        self,
        items: Sequence[BaseModel | dict[str, Any]],
        user_id: str | uuid.UUID | None = None,
        owner_id: uuid.UUID | None = None,
        scope: ScopeContext | None = None,
        allow_immutable: bool = False,
    ) -> BulkResponse:
        """Bulk create multiple records with actor stamping."""
        payload: list[dict[str, Any]] = []
        uid_str = str(user_id) if user_id else None
        has_created = self._get_column("created_by") is not None
        has_updated = self._get_column("updated_by") is not None
        has_status = self._get_column("status") is not None
        for data in items:
            d = data.model_dump() if isinstance(data, BaseModel) else dict(data)
            if not allow_immutable and has_status:
                d["status"] = RecordStatus.ACTIVE
            if has_created:
                if uid_str:
                    d["created_by"] = uid_str
                elif not allow_immutable:
                    d.pop("created_by", None)
            if has_updated:
                if uid_str:
                    d["updated_by"] = uid_str
                elif not allow_immutable:
                    d.pop("updated_by", None)
            payload.append(d)
        return await super().bulk_create(
            payload,
            user_id=user_id,
            owner_id=owner_id,
            scope=scope,
            allow_immutable=allow_immutable,
        )

    async def bulk_delete(
        self,
        req: BulkIdsRequest,
        *where: Any,
        user_id: str | uuid.UUID | None = None,
        scope: ScopeContext | None = None,
    ) -> BulkResponse:
        """Safely bulk soft-delete (trash) records instead of physical delete."""
        return await self.bulk_trash(req, *where, user_id=user_id, scope=scope)

    # ==========================================
    # 3. PAPELERA Y RECUPERACIÓN
    # ==========================================

    async def _transition_status(
        self,
        id: uuid.UUID,
        target_status: RecordStatus,
        expected_status: RecordStatus,
        actor_field: str,
        timestamp_field: str,
        err_msg: str,
        *where: Any,
        user_id: str | uuid.UUID | None = None,
        scope: ScopeContext | None = None,
    ) -> ModelT:
        """Transition status between ACTIVE and TRASHED with state guard."""
        payload = {
            "status": target_status,
            actor_field: str(user_id) if user_id else None,
            timestamp_field: datetime.now(UTC),
        }
        status_filter = self.get_status_filter(
            is_trash=(expected_status == RecordStatus.TRASHED)
        )
        extra = [status_filter] if status_filter is not None else []
        extra.extend(where)
        try:
            return await self.update(
                id, payload, *extra, user_id=user_id, scope=scope, allow_immutable=True
            )
        except HTTPException as exc:
            status_col = self._get_column("status")
            if (
                status_col is not None
                and exc.status_code
                in (status.HTTP_404_NOT_FOUND, status.HTTP_403_FORBIDDEN)
                and await self.repository.exists(
                    self.repository.pk == id, status_col != expected_status
                )
            ):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail=err_msg
                ) from exc
            raise

    async def trash(
        self,
        id: uuid.UUID,
        *where: Any,
        user_id: str | uuid.UUID | None = None,
        scope: ScopeContext | None = None,
    ) -> ModelT:
        """Move a single record to the trash bin (soft delete)."""
        return await self._transition_status(
            id,
            RecordStatus.TRASHED,
            RecordStatus.ACTIVE,
            "deleted_by",
            "deleted_at",
            "Record is already in the trash bin",
            *where,
            user_id=user_id,
            scope=scope,
        )

    async def restore(
        self,
        id: uuid.UUID,
        *where: Any,
        user_id: str | uuid.UUID | None = None,
        scope: ScopeContext | None = None,
    ) -> ModelT:
        """Restore a single record from the trash bin."""
        return await self._transition_status(
            id,
            RecordStatus.ACTIVE,
            RecordStatus.TRASHED,
            "restored_by",
            "restored_at",
            "Record is not in the trash bin",
            *where,
            user_id=user_id,
            scope=scope,
        )

    async def permanent_delete(
        self,
        id: uuid.UUID,
        *where: Any,
        scope: ScopeContext | None = None,
    ) -> ModelT:
        """Permanently delete a record from the trash in a single atomic query."""
        status_filter = self.get_status_filter(is_trash=True)
        scope_clauses = self.build_scope_filters(scope)
        extra: list[Any] = [status_filter] if status_filter is not None else []
        extra.extend(where)
        extra.extend(scope_clauses)
        deleted = await self.repository.delete(id, *extra)
        if deleted is None:
            if not await self.repository.exists(self.repository.pk == id):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"{self.model.__name__} not found",
                )
            if (where or scope_clauses) and not await self.repository.exists(
                self.repository.pk == id, *where, *scope_clauses
            ):
                await self._raise_not_found_or_forbidden(id)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot permanently delete a record that is not in the trash",
            )
        return deleted

    async def _bulk_transition_status(
        self,
        req: BulkIdsRequest,
        target_status: RecordStatus,
        is_trash_filter: bool,
        actor_field: str,
        timestamp_field: str,
        action_verb: str,
        *where: Any,
        user_id: str | uuid.UUID | None = None,
        scope: ScopeContext | None = None,
    ) -> BulkResponse:
        """Bulk transition status between ACTIVE and TRASHED."""
        where_clauses = list(where) + self.build_scope_filters(scope)
        status_filter = self.get_status_filter(is_trash=is_trash_filter)
        if status_filter is not None:
            where_clauses.append(status_filter)
        count = await self.repository.update_many(
            self.repository.pk.in_(req.ids),
            *where_clauses,
            data={
                "status": target_status,
                actor_field: str(user_id) if user_id else None,
                timestamp_field: datetime.now(UTC),
            },
        )
        return BulkResponse(
            count=count, message=f"Successfully {action_verb} {count} records"
        )

    async def bulk_trash(
        self,
        req: BulkIdsRequest,
        *where: Any,
        user_id: str | uuid.UUID | None = None,
        scope: ScopeContext | None = None,
    ) -> BulkResponse:
        """Move multiple records to the trash bin."""
        return await self._bulk_transition_status(
            req,
            RecordStatus.TRASHED,
            False,
            "deleted_by",
            "deleted_at",
            "trashed",
            *where,
            user_id=user_id,
            scope=scope,
        )

    async def bulk_restore(
        self,
        req: BulkIdsRequest,
        *where: Any,
        user_id: str | uuid.UUID | None = None,
        scope: ScopeContext | None = None,
    ) -> BulkResponse:
        """Restore multiple records from the trash bin."""
        return await self._bulk_transition_status(
            req,
            RecordStatus.ACTIVE,
            True,
            "restored_by",
            "restored_at",
            "restored",
            *where,
            user_id=user_id,
            scope=scope,
        )

    async def bulk_permanent_delete(
        self,
        req: BulkIdsRequest,
        *where: Any,
        scope: ScopeContext | None = None,
    ) -> BulkResponse:
        """Permanently delete records from the trash in a single atomic query."""
        status_filter = self.get_status_filter(is_trash=True)
        scope_clauses = self.build_scope_filters(scope)
        extra: list[Any] = [status_filter] if status_filter is not None else []
        extra.extend(where)
        extra.extend(scope_clauses)
        count = await self.repository.delete_many(req.ids, *extra)
        return BulkResponse(
            count=count,
            message=f"Successfully permanently deleted {count} records from trash",
        )
