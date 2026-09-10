import uuid
from collections.abc import Sequence
from typing import Any

from fastapi import Depends, HTTPException, status
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.crud.actors import enrich_actors
from fastapi_plantilla.core.crud.schema import (
    AuditEntry,
    PaginatedResponse,
    PaginationMeta,
)
from fastapi_plantilla.modules.audit.models import AuditLog
from fastapi_plantilla.modules.audit.repository import AuditRepository
from fastapi_plantilla.modules.audit.schema import (
    AuditFilterParams,
    AuditLogResponse,
)
from fastapi_plantilla.modules.auth.models import User
from fastapi_plantilla.modules.companies.models import Company
from fastapi_plantilla.modules.rbac.models import Role, SystemModule
from fastapi_plantilla.modules.storage.models import Document
from fastapi_plantilla.modules.teams.models import Team
from fastapi_plantilla.modules.trash.models import TrashItem

__all__ = ["AuditService", "enrich_entity_names"]


MODEL_TYPE_MAP: dict[str, tuple[Any, bool]] = {
    "user": (User, True),
    "users": (User, True),
    "company": (Company, False),
    "companies": (Company, False),
    "team": (Team, False),
    "teams": (Team, False),
    "role": (Role, False),
    "roles": (Role, False),
    "module": (SystemModule, False),
    "modules": (SystemModule, False),
    "systemmodule": (SystemModule, False),
    "document": (Document, False),
    "documents": (Document, False),
    "storage": (Document, False),
    "trash": (TrashItem, False),
    "trashitem": (TrashItem, False),
}


def _extract_name_from_changes(changes: Any) -> str | None:
    """Extract candidate entity name from audit diff changes."""
    if not isinstance(changes, dict):
        return None
    for key in ("name", "title", "filename", "username", "code", "email"):
        val = changes.get(key)
        if isinstance(val, dict) and ("new" in val or "old" in val):
            val = val.get("new") or val.get("old")
        if val is not None and str(val).strip():
            return str(val).strip()
    return None


async def _query_names_for_model(
    session: AsyncSession,
    model: Any,
    ids: Sequence[uuid.UUID],
    *,
    is_user: bool = False,
) -> dict[uuid.UUID, str]:
    """Fetch entity names for a specific model class in batch."""
    if not ids:
        return {}
    cols = [model.id, model.name, model.email] if is_user else [model.id, model.name]
    stmt = select(*cols).where(model.id.in_(list(ids)))
    res = await session.execute(stmt)
    return {
        r.id: (r.name or r.email) if is_user else r.name
        for r in res.all()
        if getattr(r, "id", None)
    }


async def _resolve_entities_from_db(
    session: AsyncSession,
    type_to_ids: dict[str, set[uuid.UUID]],
) -> dict[uuid.UUID, str]:
    """Query domain models in batch to resolve entity names."""
    id_to_name: dict[uuid.UUID, str] = {}
    for etype, ids in type_to_ids.items():
        mapping = MODEL_TYPE_MAP.get(etype)
        if not mapping or not ids:
            continue
        model, is_user = mapping
        try:
            resolved = await _query_names_for_model(
                session, model, list(ids), is_user=is_user
            )
            id_to_name.update(resolved)
        except Exception as err:
            logger.warning(f"Error querying entity name for type {etype}: {err}")
    return id_to_name


async def _resolve_trash_fallback(
    session: AsyncSession,
    missing_ids: list[uuid.UUID],
) -> dict[uuid.UUID, str]:
    """Fallback query to trash bin for soft-deleted entities."""
    if not missing_ids:
        return {}
    try:
        stmt = select(TrashItem.entity_id, TrashItem.name).where(
            TrashItem.entity_id.in_(missing_ids)
        )
        res = await session.execute(stmt)
        return {r.entity_id: r.name for r in res.all() if r.entity_id and r.name}
    except Exception as err:
        logger.warning(f"Error querying trash for missing entity names: {err}")
        return {}


def _collect_missing_audit_items(items: Sequence[AuditLog]) -> list[AuditLog]:
    """Extract audit log records that lack entity names."""
    missing: list[AuditLog] = []
    for item in items:
        if item.entity_name and item.entity_name.strip():
            continue
        resolved = _extract_name_from_changes(item.changes)
        if resolved:
            item.entity_name = resolved
        elif item.entity_id is not None:
            missing.append(item)
    return missing


def _collect_type_ids(items: list[AuditLog]) -> dict[str, set[uuid.UUID]]:
    """Group missing audit item entity IDs by normalized entity type."""
    type_to_ids: dict[str, set[uuid.UUID]] = {}
    for it in items:
        if it.entity_id is not None:
            etype = (it.entity_type or "").strip().lower()
            type_to_ids.setdefault(etype, set()).add(it.entity_id)
    return type_to_ids


async def enrich_entity_names(
    session: AsyncSession,
    items: Sequence[AuditLog],
) -> None:
    """Resolve and populate entity_name for audit logs in-place if missing."""
    if not items:
        return

    missing_items = _collect_missing_audit_items(items)
    if not missing_items:
        return

    type_to_ids = _collect_type_ids(missing_items)
    id_to_name = await _resolve_entities_from_db(session, type_to_ids)

    remaining_ids = [
        it.entity_id
        for it in missing_items
        if it.entity_id and it.entity_id not in id_to_name
    ]
    if remaining_ids:
        fallback_names = await _resolve_trash_fallback(session, remaining_ids)
        id_to_name.update(fallback_names)

    for item in missing_items:
        if item.entity_id and item.entity_id in id_to_name:
            item.entity_name = id_to_name[item.entity_id]


class AuditService:
    """Business service governing audit trail querying and emission."""

    def __init__(self, repository: AuditRepository = Depends()) -> None:
        self.repository = repository

    async def record_entry(self, entry: AuditEntry) -> AuditLogResponse:
        """Persist an audit entry and return the validated response."""
        record = await self.repository.record_entry(entry)
        await enrich_actors(self.repository.session, [record])
        await enrich_entity_names(self.repository.session, [record])
        return AuditLogResponse.model_validate(record)

    async def log(
        self,
        entity_type: str,
        action: str,
        entity_id: uuid.UUID | None = None,
        entity_name: str | None = None,
        actor_id: uuid.UUID | None = None,
        actor_name: str | None = None,
        actor_email: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        changes: dict[str, Any] | None = None,
        details: str | None = None,
    ) -> AuditLogResponse:
        """Convenience method to manually record an audited action."""
        entry = AuditEntry(
            entity_type=entity_type,
            entity_id=entity_id,
            entity_name=entity_name,
            action=action,
            actor_id=actor_id,
            actor_name=actor_name,
            actor_email=actor_email,
            ip_address=ip_address,
            user_agent=user_agent,
            changes=changes,
            details=details,
        )
        return await self.record_entry(entry)

    async def list_logs(
        self, params: AuditFilterParams
    ) -> PaginatedResponse[AuditLogResponse]:
        """Fetch filtered paginated audit logs with metadata."""
        items, total = await self.repository.list_logs(params)
        await enrich_actors(self.repository.session, items)
        await enrich_entity_names(self.repository.session, items)
        return PaginatedResponse(
            data=[AuditLogResponse.model_validate(item) for item in items],
            meta=PaginationMeta.create(
                page=params.page,
                limit=params.limit,
                total=total,
            ),
        )

    async def get_by_id(self, log_id: uuid.UUID) -> AuditLogResponse:
        """Retrieve a single audit log entry by ID."""
        item: AuditLog | None = await self.repository.get_by_id(log_id)
        if not item:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Audit log entry not found",
            )
        await enrich_actors(self.repository.session, [item])
        await enrich_entity_names(self.repository.session, [item])
        return AuditLogResponse.model_validate(item)

    async def get_entity_history(
        self, entity_type: str, entity_id: uuid.UUID
    ) -> list[AuditLogResponse]:
        """Retrieve full chronological history for a specific entity."""
        items = await self.repository.get_entity_history(entity_type, entity_id)
        await enrich_actors(self.repository.session, items)
        await enrich_entity_names(self.repository.session, items)
        return [AuditLogResponse.model_validate(item) for item in items]
