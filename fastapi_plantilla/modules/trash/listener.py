"""Listeners for synchronizing domain entity trash states with sys_trash_bin."""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.crud.service_audit import (
    _TRASH_SYNC_HOOKS,
    register_purge_sync_hook,
    register_trash_sync_hook,
)
from fastapi_plantilla.modules.trash.repository import TrashRepository
from fastapi_plantilla.modules.trash.service import (
    TrashService,
    resolve_entity_name,
)

__all__ = ["setup_trash_listeners"]


def _extract_entity_type(item: Any) -> str:
    """Derive normalized entity type from model class or table name."""
    cls_name = getattr(item, "__class__", type(item)).__name__.lower()
    if cls_name not in ("object", "model", "base"):
        return cls_name
    tablename = getattr(type(item), "__tablename__", "").lower()
    return tablename or cls_name


def _extract_name(item: Any, entity_id: uuid.UUID) -> str:
    """Extract human-readable name from entity."""
    for attr in ("name", "title", "filename", "code", "username", "email"):
        val = getattr(item, attr, None)
        if val:
            return str(val)
    return str(entity_id)


async def _handle_trash_sync(
    session: AsyncSession,
    item: Any,
    is_trash: bool,
    user_id: str | uuid.UUID | None,
) -> None:
    """Handle soft-delete or restore hook from BaseAuditService."""
    entity_id = getattr(item, "id", None)
    if not isinstance(entity_id, uuid.UUID):
        return

    entity_type = _extract_entity_type(item)
    repo = TrashRepository(session)
    service = TrashService(repo)

    if is_trash:
        name = _extract_name(item, entity_id)
        owner_id = getattr(item, "owner_id", None)
        if not isinstance(owner_id, uuid.UUID):
            owner_id = None
        details = getattr(item, "description", None) or getattr(
            item, "content_type", None
        )
        deleted_by = str(user_id) if user_id else getattr(item, "deleted_by", None)

        target_entity_type: str | None = None
        target_entity_id: uuid.UUID | None = None
        tet = getattr(item, "entity_type", None)
        tei = getattr(item, "entity_id", None)
        if isinstance(tet, str) and tet.strip():
            target_entity_type = tet.strip().lower()
        if isinstance(tei, uuid.UUID):
            target_entity_id = tei

        target_entity_name: str | None = None
        if target_entity_type and target_entity_id:
            target_entity_name = await resolve_entity_name(
                session, target_entity_type, target_entity_id
            )

        await service.record_trash(
            entity_type=entity_type,
            entity_id=entity_id,
            name=name,
            owner_id=owner_id,
            deleted_by=deleted_by,
            details=str(details) if details else None,
            target_entity_type=target_entity_type,
            target_entity_id=target_entity_id,
            target_entity_name=target_entity_name,
        )
    else:
        await repo.delete_by_entity(entity_type, entity_id)


async def _handle_purge_sync(
    session: AsyncSession,
    entity_type: str,
    entity_id: uuid.UUID,
) -> None:
    """Handle permanent delete hook from BaseAuditService."""
    repo = TrashRepository(session)
    await repo.delete_by_entity(entity_type, entity_id)


def setup_trash_listeners() -> None:
    """Register trash synchronization hooks with BaseAuditService idempotently."""
    if _handle_trash_sync in _TRASH_SYNC_HOOKS:
        return
    register_trash_sync_hook(_handle_trash_sync)
    register_purge_sync_hook(_handle_purge_sync)
