"""Listeners for synchronizing domain entity trash states with sys_trash_bin."""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.crud.service_audit import (
    register_purge_sync_hook,
    register_trash_sync_hook,
)
from fastapi_plantilla.modules.trash.repository import TrashRepository
from fastapi_plantilla.modules.trash.service import TrashService

__all__ = ["setup_trash_listeners"]


def _extract_entity_type(item: Any) -> str:
    """Derive normalized entity type from model class."""
    tablename = getattr(type(item), "__tablename__", type(item).__name__.lower())
    if tablename.startswith("sys_"):
        return tablename.removeprefix("sys_").rstrip("s")
    return tablename.rstrip("s")


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

        await service.record_trash(
            entity_type=entity_type,
            entity_id=entity_id,
            name=name,
            owner_id=owner_id,
            deleted_by=deleted_by,
            details=str(details) if details else None,
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
    """Register trash synchronization hooks with BaseAuditService."""
    register_trash_sync_hook(_handle_trash_sync)
    register_purge_sync_hook(_handle_purge_sync)
