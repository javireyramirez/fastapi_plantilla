import uuid
from collections.abc import Callable, Coroutine, Sequence
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, status
from loguru import logger
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.crud.audit_diff import (
    compute_create_diff,
    compute_update_diff,
)
from fastapi_plantilla.core.crud.schema import (
    AuditEntry,
    AuditLevel,
    BulkIdsRequest,
    BulkResponse,
    ListItemResponse,
    ListQueryParams,
    PaginationParams,
    ScopeContext,
    WriteOptions,
)
from fastapi_plantilla.core.crud.service_base import BaseCRUDService
from fastapi_plantilla.core.database import Base
from fastapi_plantilla.core.mixins import RecordStatus

TrashSyncHook = Callable[
    [AsyncSession, Any, bool, str | uuid.UUID | None], Coroutine[Any, Any, None]
]
PurgeSyncHook = Callable[[AsyncSession, str, uuid.UUID], Coroutine[Any, Any, None]]
AuditSyncHook = Callable[[AsyncSession, AuditEntry], Coroutine[Any, Any, None]]

_TRASH_SYNC_HOOKS: list[TrashSyncHook] = []
_PURGE_SYNC_HOOKS: list[PurgeSyncHook] = []
_AUDIT_SYNC_HOOKS: list[AuditSyncHook] = []


def register_trash_sync_hook(hook: TrashSyncHook) -> None:
    """Register a hook to be called on soft-delete or restore."""
    _TRASH_SYNC_HOOKS.append(hook)


def register_purge_sync_hook(hook: PurgeSyncHook) -> None:
    """Register a hook to be called on permanent delete/purge."""
    _PURGE_SYNC_HOOKS.append(hook)


def register_audit_sync_hook(hook: AuditSyncHook) -> None:
    """Register a hook to be called on audit event emission."""
    _AUDIT_SYNC_HOOKS.append(hook)


async def dispatch_audit_event(session: AsyncSession, entry: AuditEntry) -> None:
    """Dispatch an audit entry to all registered audit sync hooks."""
    for hook in _AUDIT_SYNC_HOOKS:
        try:
            await hook(session, entry)
        except Exception as err:
            logger.warning(f"Audit hook execution failed: {err}")


async def dispatch_trash_hook(
    session: AsyncSession,
    item: Any,
    is_trash: bool,
    user_id: str | uuid.UUID | None = None,
) -> None:
    """Dispatch a trash sync event to all registered trash sync hooks."""
    for hook in _TRASH_SYNC_HOOKS:
        try:
            await hook(session, item, is_trash, user_id)
        except Exception as err:
            logger.warning(f"Trash sync hook failed: {err}")


async def dispatch_purge_hook(
    session: AsyncSession,
    entity_type: str,
    id: uuid.UUID,
) -> None:
    """Dispatch a purge sync event to all registered purge sync hooks."""
    for hook in _PURGE_SYNC_HOOKS:
        try:
            await hook(session, entity_type, id)
        except Exception as err:
            logger.warning(f"Purge sync hook failed: {err}")


__all__ = [
    "AuditSyncHook",
    "BaseAuditService",
    "PurgeSyncHook",
    "TrashSyncHook",
    "dispatch_audit_event",
    "dispatch_purge_hook",
    "dispatch_trash_hook",
    "register_audit_sync_hook",
    "register_purge_sync_hook",
    "register_trash_sync_hook",
]


class BaseAuditService[ModelT: Base](BaseCRUDService[ModelT]):
    """Base business service for CRUD Audit Service."""

    audit_level: AuditLevel = AuditLevel.FULL

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
        extra_fields: Sequence[str] | None = None,
    ) -> list[ListItemResponse]:
        """Fetch a lightweight list of items for select/combobox dropdowns."""
        status_clause = self.get_status_filter(params.is_trash)
        extra = (status_clause,) if status_clause is not None else ()
        return await super().find_list(
            params,
            *where,
            *extra,
            scope=scope,
            display_field=display_field,
            extra_fields=extra_fields,
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
        options: WriteOptions | None = None,
    ) -> ModelT:
        """Create a record with created_by and updated_by actor stamping."""
        effective_user_id = (options.user_id if options else None) or user_id
        payload = data.model_dump() if isinstance(data, BaseModel) else dict(data)
        if not allow_immutable and self._get_column("status") is not None:
            payload["status"] = RecordStatus.ACTIVE
        self._stamp_actors(payload, effective_user_id, is_create=True)
        created = await super().create(
            data=payload,
            user_id=effective_user_id,
            owner_id=owner_id,
            scope=scope,
            allow_immutable=allow_immutable,
        )
        await self._emit_audit(
            item=created,
            action="CREATE",
            options=options,
            user_id=effective_user_id,
            new_data=payload,
        )
        return created

    async def update(
        self,
        id: uuid.UUID,
        data: BaseModel | dict[str, Any],
        *where: Any,
        expected_version: int | None = None,
        user_id: str | uuid.UUID | None = None,
        scope: ScopeContext | None = None,
        allow_immutable: bool = False,
        options: WriteOptions | None = None,
    ) -> ModelT:
        """Update a record with updated_by actor stamping and trash protection."""
        effective_user_id = (options.user_id if options else None) or user_id
        payload = (
            data.model_dump(exclude_unset=True)
            if isinstance(data, BaseModel)
            else dict(data)
        )
        extra_where = list(where)
        status_col = self._get_column("status")

        if not allow_immutable and status_col is not None:
            extra_where.append(status_col != RecordStatus.TRASHED)

        self._stamp_actors(payload, effective_user_id, is_create=False)

        snapshot_before: dict[str, Any] | None = None
        if self.audit_level == AuditLevel.FULL:
            item_obj = await self.repository.get_by_id(id)
            if item_obj is not None:
                snapshot_before = {k: getattr(item_obj, k, None) for k in payload}

        try:
            updated = await super().update(
                id,
                payload,
                *extra_where,
                expected_version=expected_version,
                scope=scope,
                allow_immutable=allow_immutable,
            )
            await self._emit_audit(
                item=updated,
                action="UPDATE",
                options=options,
                user_id=effective_user_id,
                snapshot_before=snapshot_before,
                updated_payload=payload,
            )
            return updated
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
        options: WriteOptions | None = None,
    ) -> ModelT:
        """Safely soft-delete (trash) record instead of physical delete."""
        return await self.trash(
            id, *where, user_id=user_id, scope=scope, options=options
        )

    async def bulk_create(
        self,
        items: Sequence[BaseModel | dict[str, Any]],
        user_id: str | uuid.UUID | None = None,
        owner_id: uuid.UUID | None = None,
        scope: ScopeContext | None = None,
        allow_immutable: bool = False,
        options: WriteOptions | None = None,
    ) -> BulkResponse:
        """Bulk create multiple records with actor stamping."""
        effective_user_id = (options.user_id if options else None) or user_id
        payload: list[dict[str, Any]] = []
        has_status = self._get_column("status") is not None
        for data in items:
            d = data.model_dump() if isinstance(data, BaseModel) else dict(data)
            if not allow_immutable and has_status:
                d["status"] = RecordStatus.ACTIVE
            self._stamp_actors(d, effective_user_id, is_create=True)
            payload.append(d)
        res = await super().bulk_create(
            payload,
            user_id=effective_user_id,
            owner_id=owner_id,
            scope=scope,
            allow_immutable=allow_immutable,
            options=options,
        )
        if res.count > 0:
            await self._emit_audit(
                item=None,
                action="BULK_CREATE",
                options=options,
                user_id=effective_user_id,
                details=f"Bulk created {res.count} {self.resource_name} records",
            )
        return res

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
        except IntegrityError as exc:
            if target_status == RecordStatus.ACTIVE:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"Cannot restore {self.resource_name}: a conflicting "
                        "active record already exists."
                    ),
                ) from exc
            raise

    async def trash(
        self,
        id: uuid.UUID,
        *where: Any,
        user_id: str | uuid.UUID | None = None,
        scope: ScopeContext | None = None,
        options: WriteOptions | None = None,
    ) -> ModelT:
        """Move a single record to the trash bin (soft delete)."""
        effective_user_id = (options.user_id if options else None) or user_id
        item = await self._transition_status(
            id,
            RecordStatus.TRASHED,
            RecordStatus.ACTIVE,
            "deleted_by",
            "deleted_at",
            "Record is already in the trash bin",
            *where,
            user_id=effective_user_id,
            scope=scope,
        )
        await self.on_after_trash(item, user_id=effective_user_id)
        await self._emit_audit(
            item=item,
            action="TRASH",
            options=options,
            user_id=effective_user_id,
            status_transition=(RecordStatus.ACTIVE.value, RecordStatus.TRASHED.value),
        )
        return item

    async def restore(
        self,
        id: uuid.UUID,
        *where: Any,
        user_id: str | uuid.UUID | None = None,
        scope: ScopeContext | None = None,
        options: WriteOptions | None = None,
    ) -> ModelT:
        """Restore a single record from the trash bin."""
        effective_user_id = (options.user_id if options else None) or user_id
        item = await self._transition_status(
            id,
            RecordStatus.ACTIVE,
            RecordStatus.TRASHED,
            "restored_by",
            "restored_at",
            "Record is not in the trash bin",
            *where,
            user_id=effective_user_id,
            scope=scope,
        )
        await self.on_after_restore(item, user_id=effective_user_id)
        await self._emit_audit(
            item=item,
            action="RESTORE",
            options=options,
            user_id=effective_user_id,
            status_transition=(RecordStatus.TRASHED.value, RecordStatus.ACTIVE.value),
        )
        return item

    async def permanent_delete(
        self,
        id: uuid.UUID,
        *where: Any,
        scope: ScopeContext | None = None,
        options: WriteOptions | None = None,
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
                    detail=f"{self.resource_name} not found",
                )
            if (where or scope_clauses) and not await self.repository.exists(
                self.repository.pk == id, *where, *scope_clauses
            ):
                await self._raise_not_found_or_forbidden(id)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot permanently delete a record that is not in the trash",
            )
        await self.on_after_permanent_delete(id)
        await self._emit_audit(
            item=deleted,
            action="PERMANENT_DELETE",
            options=options,
            status_transition=(RecordStatus.TRASHED.value, "PURGED"),
            details=f"Permanently deleted {self.resource_name}",
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
        options: WriteOptions | None = None,
    ) -> BulkResponse:
        """Bulk transition status between ACTIVE and TRASHED."""
        effective_user_id = (options.user_id if options else None) or user_id
        where_clauses = list(where) + self.build_scope_filters(scope)
        status_filter = self.get_status_filter(is_trash=is_trash_filter)
        if status_filter is not None:
            where_clauses.append(status_filter)
        try:
            count = await self.repository.update_many(
                self.repository.pk.in_(req.ids),
                *where_clauses,
                data={
                    "status": target_status,
                    actor_field: str(effective_user_id) if effective_user_id else None,
                    timestamp_field: datetime.now(UTC),
                },
            )
        except IntegrityError as exc:
            if target_status == RecordStatus.ACTIVE:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"Cannot restore {self.resource_name}: one or more "
                        "records conflict with existing active records."
                    ),
                ) from exc
            raise
        if count > 0:
            if target_status == RecordStatus.TRASHED:
                await self.on_after_bulk_trash(req.ids, user_id=effective_user_id)
            elif target_status == RecordStatus.ACTIVE:
                await self.on_after_bulk_restore(req.ids, user_id=effective_user_id)
            action_name = (
                "BULK_TRASH"
                if target_status == RecordStatus.TRASHED
                else "BULK_RESTORE"
            )
            await self._emit_audit(
                item=None,
                action=action_name,
                options=options,
                user_id=effective_user_id,
                details=(
                    f"Successfully {action_verb} {count} {self.resource_name} records"
                ),
            )

        return BulkResponse(
            count=count, message=f"Successfully {action_verb} {count} records"
        )

    # ==========================================
    # 4. HOOKS DE SINCRONIZACIÓN CON PAPELERA
    # ==========================================

    async def on_after_trash(
        self,
        item: ModelT,
        user_id: str | uuid.UUID | None = None,
    ) -> None:
        """Hook executed after soft-deleting an item."""
        await dispatch_trash_hook(self.repository.session, item, True, user_id)

    async def on_after_restore(
        self,
        item: ModelT,
        user_id: str | uuid.UUID | None = None,
    ) -> None:
        """Hook executed after restoring an item."""
        await dispatch_trash_hook(self.repository.session, item, False, user_id)

    async def on_after_permanent_delete(self, id: uuid.UUID) -> None:
        """Hook executed after permanently deleting an item."""
        entity_type = getattr(self.model, "__name__", self.resource_name).lower()
        if entity_type.startswith("sys_"):
            entity_type = entity_type.removeprefix("sys_").rstrip("s")
        await dispatch_purge_hook(self.repository.session, entity_type, id)

    async def on_after_bulk_trash(
        self,
        ids: list[uuid.UUID],
        user_id: str | uuid.UUID | None = None,
    ) -> None:
        """Hook executed after bulk soft-deleting items."""
        items = await self.repository.find_many(self.repository.pk.in_(ids))
        for item in items:
            await self.on_after_trash(item, user_id=user_id)

    async def on_after_bulk_restore(
        self,
        ids: list[uuid.UUID],
        user_id: str | uuid.UUID | None = None,
    ) -> None:
        """Hook executed after bulk restoring items."""
        items = await self.repository.find_many(self.repository.pk.in_(ids))
        for item in items:
            await self.on_after_restore(item, user_id=user_id)

    async def bulk_trash(
        self,
        req: BulkIdsRequest,
        *where: Any,
        user_id: str | uuid.UUID | None = None,
        scope: ScopeContext | None = None,
        options: WriteOptions | None = None,
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
            options=options,
        )

    async def bulk_restore(
        self,
        req: BulkIdsRequest,
        *where: Any,
        user_id: str | uuid.UUID | None = None,
        scope: ScopeContext | None = None,
        options: WriteOptions | None = None,
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
            options=options,
        )

    async def bulk_permanent_delete(
        self,
        req: BulkIdsRequest,
        *where: Any,
        scope: ScopeContext | None = None,
        options: WriteOptions | None = None,
    ) -> BulkResponse:
        """Permanently delete records from the trash in a single atomic query."""
        status_filter = self.get_status_filter(is_trash=True)
        scope_clauses = self.build_scope_filters(scope)
        extra: list[Any] = [status_filter] if status_filter is not None else []
        extra.extend(where)
        extra.extend(scope_clauses)
        items_to_delete = await self.repository.find_many(
            self.repository.pk.in_(req.ids), *extra
        )
        item_map = {getattr(it, "id", None): it for it in items_to_delete}
        count = await self.repository.delete_many(req.ids, *extra)
        for item_id in req.ids:
            it = item_map.get(item_id)
            await self.on_after_permanent_delete(item_id)
            await self._emit_audit(
                item=it,
                action="PERMANENT_DELETE",
                options=options,
                entity_id=item_id,
                status_transition=(RecordStatus.TRASHED.value, "PURGED"),
                details=f"Permanently deleted {self.resource_name} via bulk action",
            )
        return BulkResponse(
            count=count,
            message=f"Successfully permanently deleted {count} records from trash",
        )

    # ==========================================
    # 5. EMISIÓN DE AUDITORÍA DESACOPLADA
    # ==========================================

    def _extract_entity_name(
        self,
        item: Any = None,
        data: dict[str, Any] | None = None,
    ) -> str | None:
        """Extract a readable entity name from an instance or dictionary."""
        if item is not None:
            for attr in ("name", "title", "filename", "username", "code", "email"):
                val = getattr(item, attr, None)
                if val is not None and str(val).strip():
                    return str(val).strip()
        if data is not None and isinstance(data, dict):
            for key in ("name", "title", "filename", "username", "code", "email"):
                val = data.get(key)
                if isinstance(val, dict) and ("new" in val or "old" in val):
                    val = val.get("new") or val.get("old")
                if val is not None and str(val).strip():
                    return str(val).strip()
        return None

    async def _emit_audit(
        self,
        item: Any,
        action: str,
        options: WriteOptions | None = None,
        user_id: str | uuid.UUID | None = None,
        entity_id: uuid.UUID | None = None,
        entity_name: str | None = None,
        changes: dict[str, Any] | None = None,
        new_data: dict[str, Any] | None = None,
        snapshot_before: dict[str, Any] | None = None,
        updated_payload: dict[str, Any] | None = None,
        status_transition: tuple[str, str] | None = None,
        details: str | None = None,
    ) -> None:
        """Construct and dispatch an AuditEntry if audit_level is active."""
        if self.audit_level == AuditLevel.NONE or not _AUDIT_SYNC_HOOKS:
            return

        entity_type = self.resource_name.lower()
        effective_entity_id = entity_id or (getattr(item, "id", None) if item else None)
        effective_entity_name = (
            entity_name
            or self._extract_entity_name(item)
            or self._extract_entity_name(data=new_data)
            or self._extract_entity_name(data=updated_payload)
            or self._extract_entity_name(data=snapshot_before)
            or self._extract_entity_name(data=changes)
        )
        actor_id_raw = (
            (options.user_id if options else None)
            or user_id
            or getattr(item, "updated_by", None)
            or getattr(item, "created_by", None)
        )

        computed_changes: dict[str, Any] | None = changes
        if computed_changes is None and self.audit_level == AuditLevel.FULL:
            if action == "CREATE" and new_data:
                computed_changes = compute_create_diff(
                    new_data,
                    self.IMMUTABLE_CREATE_FIELDS,
                    self.SENSITIVE_COLUMNS,
                )
            elif action == "UPDATE" and updated_payload and snapshot_before:
                computed_changes = compute_update_diff(
                    snapshot_before,
                    updated_payload,
                    self.IMMUTABLE_FIELDS,
                    self.SENSITIVE_COLUMNS,
                )
            elif status_transition:
                old_s, new_s = status_transition
                computed_changes = {"status": {"old": old_s, "new": new_s}}

        actor_name = options.actor_name if options else None
        actor_email = options.actor_email if options else None

        entry = AuditEntry(
            entity_type=entity_type,
            entity_id=effective_entity_id,
            entity_name=effective_entity_name,
            action=action,
            actor_id=self._to_uuid(actor_id_raw),
            actor_name=actor_name,
            actor_email=actor_email,
            ip_address=options.ip_address if options else None,
            user_agent=options.user_agent if options else None,
            changes=computed_changes if computed_changes else None,
            details=details,
        )

        await dispatch_audit_event(self.repository.session, entry)
