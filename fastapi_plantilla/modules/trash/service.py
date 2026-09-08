import uuid
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException, status
from loguru import logger
from sqlalchemy import delete, inspect, update
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.config import settings
from fastapi_plantilla.core.crud.schema import (
    BulkResponse,
    PaginatedResponse,
    PaginationMeta,
    ScopeContext,
    ScopeType,
    SortOrder,
)
from fastapi_plantilla.core.database import Base
from fastapi_plantilla.core.mixins import RecordStatus
from fastapi_plantilla.modules.trash.models import TrashItem
from fastapi_plantilla.modules.trash.repository import TrashRepository
from fastapi_plantilla.modules.trash.schema import (
    DEFAULT_TRASH_PURGE_LIMIT,
    BulkTrashActionRequest,
    TrashFilterParams,
    TrashItemResponse,
)

__all__ = [
    "TrashService",
    "register_trash_entity",
]

# Type alias for purge callbacks
PurgeCallback = Callable[[AsyncSession, uuid.UUID], Coroutine[Any, Any, None]]

_ENTITY_REGISTRY: dict[str, type[Base]] = {}
_PURGE_HOOKS: dict[str, PurgeCallback] = {}


def register_trash_entity(
    entity_type: str,
    model: type[Base],
    purge_hook: PurgeCallback | None = None,
) -> None:
    """Register domain model and cleanup callback for trash operations."""
    normalized_type = entity_type.strip().lower()
    _ENTITY_REGISTRY[normalized_type] = model
    if purge_hook is not None:
        _PURGE_HOOKS[normalized_type] = purge_hook


class TrashService:
    """Service orchestrating trash bin queries, restorations and physical purges."""

    def __init__(self, repository: TrashRepository) -> None:
        self.repository = repository
        self.session: AsyncSession = repository.session

    def _resolve_model(self, entity_type: str) -> type[Base] | None:
        """Find SQLAlchemy model class registered for the given entity type."""
        normalized = entity_type.strip().lower()
        if normalized in _ENTITY_REGISTRY:
            return _ENTITY_REGISTRY[normalized]

        candidates = (
            normalized,
            f"sys_{normalized}",
            f"{normalized}s",
            f"sys_{normalized}s",
        )
        for mapper in Base.registry.mappers:
            cls = mapper.class_
            tablename = getattr(cls, "__tablename__", "")
            if tablename in candidates:
                return cls  # type: ignore[no-any-return]
        return None

    def _apply_scope(self, scope: ScopeContext | None, item: TrashItem) -> None:
        """Enforce RBAC ownership checks on a trash item."""
        if scope is None:
            return
        if scope.is_super_admin or str(scope.scope).upper() == ScopeType.GLOBAL:
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
        where: list[Any] = []

        is_own = bool(
            scope
            and not scope.is_super_admin
            and str(scope.scope).upper() == ScopeType.OWN
        )
        if is_own and scope:
            where.append(TrashItem.owner_id == scope.user_id)

        if params.entity_type:
            where.append(TrashItem.entity_type == params.entity_type.strip().lower())

        now = datetime.now(UTC)
        if params.is_expired is True:
            where.append(TrashItem.expires_at <= now)
        elif params.is_expired is False:
            where.append(TrashItem.expires_at > now)

        if params.deleted_at_from:
            where.append(TrashItem.deleted_at >= params.deleted_at_from)
        if params.deleted_at_to:
            where.append(TrashItem.deleted_at <= params.deleted_at_to)

        search_query = params.q or params.search
        if search_query:
            where.append(TrashItem.name.ilike(f"%{search_query}%"))

        sort_col = getattr(TrashItem, params.sort_by, TrashItem.deleted_at)
        order_expr = (
            sort_col.asc() if params.sort_order == SortOrder.ASC else sort_col.desc()
        )

        skip = (params.page - 1) * params.limit
        items, total = await self.repository.find_many_with_count(
            *where,
            skip=skip,
            limit=params.limit,
            order_by=order_expr,
        )

        response_items = [TrashItemResponse.model_validate(item) for item in items]
        meta = PaginationMeta.create(page=params.page, limit=params.limit, total=total)
        return PaginatedResponse(data=response_items, meta=meta)

    async def get_by_id(
        self,
        id: uuid.UUID,
        scope: ScopeContext | None = None,
    ) -> TrashItem:
        """Fetch single trash record verifying RBAC scope."""
        item = await self.repository.get_by_id(id)
        if item is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Trash item not found",
            )
        self._apply_scope(scope, item)
        return item

    async def record_trash(
        self,
        entity_type: str,
        entity_id: uuid.UUID,
        name: str,
        owner_id: uuid.UUID | None = None,
        deleted_by: str | None = None,
        details: str | None = None,
        data_backup: dict[str, Any] | None = None,
        retention_days: int | None = None,
    ) -> TrashItem:
        """Record an entity in the centralized trash bin."""
        days = retention_days or settings.trash_retention_days
        expires_at = datetime.now(UTC) + timedelta(days=days)
        normalized_type = entity_type.strip().lower()

        existing = await self.repository.get_by_entity(normalized_type, entity_id)
        if existing is not None:
            existing.name = name
            existing.deleted_by = deleted_by
            existing.deleted_at = datetime.now(UTC)
            existing.expires_at = expires_at
            existing.data_backup = data_backup
            existing.details = details
            await self.session.flush()
            return existing

        return await self.repository.create(
            {
                "entity_type": normalized_type,
                "entity_id": entity_id,
                "name": name,
                "owner_id": owner_id,
                "deleted_by": deleted_by,
                "deleted_at": datetime.now(UTC),
                "expires_at": expires_at,
                "data_backup": data_backup,
                "details": details,
            }
        )

    async def restore_item(
        self,
        id: uuid.UUID,
        scope: ScopeContext | None = None,
        user_id: str | uuid.UUID | None = None,
    ) -> TrashItem:
        """Restore an item from trash back to active state in its source module."""
        item = await self.get_by_id(id, scope=scope)
        model = self._resolve_model(item.entity_type)

        if model is not None and hasattr(model, "status"):
            now = datetime.now(UTC)
            actor = str(user_id) if user_id else None
            pk_col = inspect(model).primary_key[0]
            stmt = (
                update(model)
                .where(pk_col == item.entity_id)
                .values(
                    status=RecordStatus.ACTIVE,
                    restored_at=now,
                    restored_by=actor,
                )
            )
            await self.session.execute(stmt)

        await self.repository.delete(item.id)
        return item

    async def purge_item(
        self,
        id: uuid.UUID,
        scope: ScopeContext | None = None,
    ) -> TrashItem:
        """Permanently delete an item from the source table, storage, and trash bin."""
        item = await self.get_by_id(id, scope=scope)
        normalized_type = item.entity_type.strip().lower()

        # Run custom purge hook if registered (e.g. to clean S3 files)
        if normalized_type in _PURGE_HOOKS:
            try:
                await _PURGE_HOOKS[normalized_type](self.session, item.entity_id)
            except Exception as err:
                logger.warning(
                    f"Purge hook failed for {item.entity_type}:{item.entity_id}: {err}"
                )

        model = self._resolve_model(item.entity_type)
        if model is not None:
            pk_col = inspect(model).primary_key[0]
            stmt = delete(model).where(pk_col == item.entity_id)
            await self.session.execute(stmt)

        await self.repository.delete(item.id)
        return item

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
    ) -> BulkResponse:
        """Purge multiple trash items in batch."""
        return await self._execute_bulk_action(
            request.ids,
            lambda item_id: self.purge_item(item_id, scope=scope),
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
