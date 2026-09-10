import uuid
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import Depends, HTTPException, status
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.config import settings
from fastapi_plantilla.core.crud.actors import enrich_actors
from fastapi_plantilla.core.crud.schema import (
    AuditEntry,
    BulkResponse,
    PaginatedResponse,
    PaginationMeta,
    ScopeContext,
    ScopeType,
)
from fastapi_plantilla.core.crud.service_audit import _AUDIT_SYNC_HOOKS
from fastapi_plantilla.core.database import get_db_session
from fastapi_plantilla.core.mixins import RecordStatus
from fastapi_plantilla.modules.rbac.catalog import CORE_SYSTEM_MODULES
from fastapi_plantilla.modules.trash.models import TrashItem
from fastapi_plantilla.modules.trash.repository import (
    TrashRepository,
    register_trash_model,
    resolve_entity_type_candidates,
)
from fastapi_plantilla.modules.trash.schema import (
    DEFAULT_TRASH_PURGE_LIMIT,
    BulkTrashActionRequest,
    PrincipalEntityModule,
    TrashFilterParams,
    TrashItemResponse,
    TrashModuleResponse,
)

__all__ = [
    "TrashService",
    "get_trash_repository",
    "get_trash_service",
    "purge_expired_trash",
    "register_trash_entity",
    "resolve_entity_name",
    "resolve_module",
]

# Type alias for custom entity purge callbacks
PurgeCallback = Callable[[Any, uuid.UUID], Coroutine[Any, Any, None]]

_PURGE_HOOKS: dict[str, PurgeCallback] = {}


def register_trash_entity(
    entity_type: str,
    model: Any,
    purge_hook: PurgeCallback | None = None,
) -> None:
    """Register domain model and cleanup callback for trash operations."""
    normalized_type = entity_type.strip().lower()
    register_trash_model(normalized_type, model)
    if purge_hook is not None:
        _PURGE_HOOKS[normalized_type] = purge_hook


# ---------------------------------------------------------------------------
# In-memory Module Catalog (SSOT derived from CORE_SYSTEM_MODULES)
# ---------------------------------------------------------------------------

MODULE_CATALOG: dict[str, TrashModuleResponse] = {}
for _mod in CORE_SYSTEM_MODULES:
    _resp = TrashModuleResponse(
        code=_mod["code"],
        name=_mod["name"],
        description=_mod.get("description"),
        icon=_mod.get("icon"),
        category=_mod.get("category"),
    )
    MODULE_CATALOG[_mod["code"].lower()] = _resp
    for _candidate in resolve_entity_type_candidates(_mod["code"]):
        MODULE_CATALOG.setdefault(_candidate, _resp)


def resolve_module(entity_type: str | None) -> TrashModuleResponse | None:
    """Resolve module metadata in O(1) from central system catalog."""
    if not entity_type:
        return None
    return MODULE_CATALOG.get(entity_type.strip().lower())


async def resolve_entity_name(
    session: Any,
    entity_type: str,
    entity_id: uuid.UUID,
) -> str | None:
    """Delegate entity name lookup to repository for backward compatibility."""
    repo = TrashRepository(session)
    return await repo.resolve_entity_name(entity_type, entity_id)


def _to_response(item: TrashItem) -> TrashItemResponse:
    """Convert TrashItem database model to clean TrashItemResponse DTO."""
    target_type = item.target_entity_type
    target_id = item.target_entity_id
    target_name = item.target_entity_name

    principal: PrincipalEntityModule | None = None
    is_doc = item.entity_type.lower() in ("document", "documents")
    if is_doc and (target_type or target_id):
        target_mod = resolve_module(target_type)
        code = target_mod.code if target_mod else (target_type or "unknown")
        name = (
            target_mod.name
            if target_mod
            else (target_type.capitalize() if target_type else "Unknown")
        )
        principal = PrincipalEntityModule(
            code=code,
            name=name,
            entity_name=target_name,
            entity_id=target_id,
        )

    return TrashItemResponse(
        id=item.id,
        entity_type=item.entity_type,
        entity_id=item.entity_id,
        name=item.name,
        module_principal_entity=principal,
        owner_id=item.owner_id,
        deleted_by=item.deleted_by,
        deleted_by_name=getattr(item, "deleted_by_name", None),
        deleted_by_email=getattr(item, "deleted_by_email", None),
        deletor=getattr(item, "deletor", None),
        deleted_at=item.deleted_at,
        expires_at=item.expires_at,
        details=item.details,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


# ---------------------------------------------------------------------------
# Trash Service
# ---------------------------------------------------------------------------


class TrashService:
    """Application service orchestrating trash bin business logic without DB queries."""

    def __init__(self, repository: TrashRepository) -> None:
        self.repository = repository

    def _apply_scope(self, scope: ScopeContext | None, item: TrashItem) -> None:
        """Enforce owner-level RBAC access permissions on single item."""
        if not scope or scope.is_super_admin:
            return
        if str(scope.scope).upper() != ScopeType.OWN:
            return
        if item.owner_id is not None and item.owner_id != scope.user_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Trash item not found",
            )

    async def list_trash(
        self,
        params: TrashFilterParams,
        scope: ScopeContext | None = None,
    ) -> PaginatedResponse[TrashItemResponse]:
        """Fetch paginated trash items matching criteria and RBAC scope."""
        items, total = await self.repository.find_trash_items(params, scope)
        if items:
            await enrich_actors(self.repository.session, items)

        response_items = [_to_response(item) for item in items]
        meta = PaginationMeta.create(page=params.page, limit=params.limit, total=total)
        return PaginatedResponse(data=response_items, meta=meta)

    async def get_by_id(
        self,
        id: uuid.UUID,
        scope: ScopeContext | None = None,
    ) -> TrashItemResponse:
        """Fetch single trash record verifying RBAC scope."""
        item = await self.repository.get_by_id(id)
        if item is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Trash item not found",
            )
        self._apply_scope(scope, item)
        await enrich_actors(self.repository.session, [item])
        return _to_response(item)

    async def record_trash(
        self,
        entity_type: str,
        entity_id: uuid.UUID,
        name: str,
        owner_id: uuid.UUID | None = None,
        deleted_by: str | None = None,
        details: str | None = None,
        retention_days: int | None = None,
        target_entity_type: str | None = None,
        target_entity_id: uuid.UUID | None = None,
        target_entity_name: str | None = None,
    ) -> TrashItem:
        """Record an entity in the centralized trash bin."""
        days = retention_days or settings.trash_retention_days
        expires_at = datetime.now(UTC) + timedelta(days=days)
        normalized_type = entity_type.strip().lower()

        return await self.repository.save_trash_item(
            entity_type=normalized_type,
            entity_id=entity_id,
            name=name,
            owner_id=owner_id,
            deleted_by=deleted_by,
            details=details,
            expires_at=expires_at,
            target_entity_type=target_entity_type,
            target_entity_id=target_entity_id,
            target_entity_name=target_entity_name,
        )

    async def _emit_audit(
        self,
        entity_type: str,
        entity_id: uuid.UUID,
        action: str,
        entity_name: str | None = None,
        actor_id: uuid.UUID | None = None,
        changes: dict[str, Any] | None = None,
        details: str | None = None,
    ) -> None:
        """Dispatch audit event to registered audit sync listeners."""
        if not _AUDIT_SYNC_HOOKS:
            return
        entry = AuditEntry(
            entity_type=entity_type,
            entity_id=entity_id,
            entity_name=entity_name,
            action=action,
            actor_id=actor_id,
            changes=changes,
            details=details,
        )
        for hook in _AUDIT_SYNC_HOOKS:
            try:
                await hook(self.repository.session, entry)
            except Exception as err:
                logger.warning(f"Trash audit hook execution failed: {err}")

    async def restore_item(
        self,
        id: uuid.UUID,
        scope: ScopeContext | None = None,
        user_id: str | uuid.UUID | None = None,
    ) -> TrashItemResponse:
        """Restore an item from trash back to active state in its source module."""
        item = await self.repository.get_by_id(id)
        if item is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Trash item not found",
            )
        self._apply_scope(scope, item)

        await self.repository.restore_target_model(
            item.entity_type, item.entity_id, user_id=user_id
        )
        await self.repository.delete(item.id)

        actor_uuid = None
        if user_id:
            try:
                actor_uuid = uuid.UUID(str(user_id))
            except (ValueError, TypeError):
                actor_uuid = None

        await self._emit_audit(
            entity_type=item.entity_type,
            entity_id=item.entity_id,
            action="RESTORE",
            entity_name=item.name,
            actor_id=actor_uuid,
            changes={
                "status": {
                    "old": RecordStatus.TRASHED.value,
                    "new": RecordStatus.ACTIVE.value,
                }
            },
            details=f"Restored {item.name or item.entity_type} from trash",
        )
        return _to_response(item)

    async def purge_item(
        self,
        id: uuid.UUID,
        scope: ScopeContext | None = None,
        user_id: str | uuid.UUID | None = None,
    ) -> TrashItemResponse:
        """Permanently delete an item from the source table, storage, and trash bin."""
        item = await self.repository.get_by_id(id)
        if item is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Trash item not found",
            )
        self._apply_scope(scope, item)

        normalized_type = item.entity_type.strip().lower()
        if normalized_type in _PURGE_HOOKS:
            try:
                await _PURGE_HOOKS[normalized_type](
                    self.repository.session, item.entity_id
                )
            except Exception as err:
                logger.warning(
                    f"Purge hook failed for {item.entity_type}:{item.entity_id}: {err}"
                )

        await self.repository.purge_target_model(item.entity_type, item.entity_id)
        await self.repository.delete(item.id)

        actor_uuid = None
        if user_id:
            try:
                actor_uuid = uuid.UUID(str(user_id))
            except (ValueError, TypeError):
                actor_uuid = None

        await self._emit_audit(
            entity_type=item.entity_type,
            entity_id=item.entity_id,
            action="PERMANENT_DELETE",
            entity_name=item.name,
            actor_id=actor_uuid,
            changes={"status": {"old": RecordStatus.TRASHED.value, "new": "PURGED"}},
            details=f"Permanently purged {item.name or item.entity_type} from trash",
        )
        return _to_response(item)

    async def _execute_bulk_action(
        self,
        ids: list[uuid.UUID],
        action: Callable[[uuid.UUID], Coroutine[Any, Any, Any]],
        action_verb: str,
    ) -> BulkResponse:
        """Execute a batch operation over trash IDs, logging individual failures."""
        count = 0
        for item_id in ids:
            try:
                await action(item_id)
                count += 1
            except Exception as err:
                logger.warning(f"Failed to {action_verb} trash item {item_id}: {err}")

        return BulkResponse(
            count=count,
            message=f"Successfully {action_verb}d {count} items from trash.",
        )

    async def bulk_restore(
        self,
        request: BulkTrashActionRequest,
        scope: ScopeContext | None = None,
        user_id: str | uuid.UUID | None = None,
    ) -> BulkResponse:
        """Restore multiple trash items in batch."""
        return await self._execute_bulk_action(
            request.ids,
            lambda item_id: self.restore_item(item_id, scope=scope, user_id=user_id),
            "restore",
        )

    async def bulk_purge(
        self,
        request: BulkTrashActionRequest,
        scope: ScopeContext | None = None,
        user_id: str | uuid.UUID | None = None,
    ) -> BulkResponse:
        """Purge multiple trash items in batch."""
        return await self._execute_bulk_action(
            request.ids,
            lambda item_id: self.purge_item(item_id, scope=scope, user_id=user_id),
            "purge",
        )

    async def purge_expired(self, limit: int = DEFAULT_TRASH_PURGE_LIMIT) -> int:
        """Purge all trash items whose retention period has expired."""
        now = datetime.now(UTC)
        expired_items = await self.repository.find_expired(cutoff=now, limit=limit)
        purged_count = 0

        for item in expired_items:
            try:
                await self.purge_item(item.id, scope=None)
                purged_count += 1
            except Exception as err:
                logger.error(f"Error purging expired trash item {item.id}: {err}")

        return purged_count


async def purge_expired_trash(
    session: AsyncSession,
    limit: int = DEFAULT_TRASH_PURGE_LIMIT,
) -> int:
    """Purge expired trash records in a database session."""
    repo = TrashRepository(session)
    service = TrashService(repo)
    return await service.purge_expired(limit=limit)


def get_trash_repository(
    session: AsyncSession = Depends(get_db_session),
) -> TrashRepository:
    """Provide TrashRepository bound to request DB session."""
    return TrashRepository(session=session)


def get_trash_service(
    repository: TrashRepository = Depends(get_trash_repository),
) -> TrashService:
    """Provide TrashService instance."""
    return TrashService(repository=repository)
