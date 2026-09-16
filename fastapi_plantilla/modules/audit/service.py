import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException, status
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.crud.actors import enrich_actors
from fastapi_plantilla.core.crud.exporter import format_export
from fastapi_plantilla.core.crud.schema import (
    AuditEntry,
    ExportRequest,
    PaginatedResponse,
    PaginationMeta,
    ScopeContext,
)
from fastapi_plantilla.core.crud.service_base import ExportResult
from fastapi_plantilla.modules.audit.models import AuditLog
from fastapi_plantilla.modules.audit.repository import AuditRepository
from fastapi_plantilla.modules.audit.schema import (
    DEFAULT_AUDIT_EXPORT_LIMIT,
    DEFAULT_AUDIT_PURGE_LIMIT,
    DEFAULT_AUDIT_RETENTION_DAYS,
    MAX_AUDIT_EXPORT_LIMIT,
    AuditFilterParams,
    AuditLogExportResponse,
    AuditLogResponse,
)
from fastapi_plantilla.modules.auth.models import User
from fastapi_plantilla.modules.common.resolvers import (
    resolve_entity_model,
    resolve_module_metadata,
)
from fastapi_plantilla.modules.companies.models import Company
from fastapi_plantilla.modules.rbac.models import Role, SystemModule
from fastapi_plantilla.modules.settings.models import SystemSetting
from fastapi_plantilla.modules.settings.service import SystemSettingService
from fastapi_plantilla.modules.storage.models import Storage
from fastapi_plantilla.modules.teams.models import Team
from fastapi_plantilla.modules.trash.models import TrashItem

__all__ = [
    "AuditService",
    "enrich_audit_modules",
    "enrich_entity_names",
    "purge_expired_audit",
    "resolve_audit_module",
]


MODEL_TYPE_MAP: dict[str, tuple[Any, bool]] = {
    "user": (User, True),
    "company": (Company, False),
    "team": (Team, False),
    "role": (Role, False),
    "module": (SystemModule, False),
    "storage": (Storage, False),
    "trash": (TrashItem, False),
    "setting": (SystemSetting, False),
    "systemsetting": (SystemSetting, False),
}


def _extract_name_from_changes(changes: Any) -> str | None:
    """Extract candidate entity name from audit diff changes."""
    if not isinstance(changes, dict):
        return None
    for key in ("name", "title", "filename", "username", "code", "email", "key"):
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
    name_col = getattr(model, "name", None) or getattr(model, "key", None)
    if name_col is None:
        return {}
    cols = [model.id, name_col, model.email] if is_user else [model.id, name_col]
    stmt = select(*cols).where(model.id.in_(list(ids)))
    res = await session.execute(stmt)
    name_attr = "name" if hasattr(model, "name") else "key"
    return {
        r.id: (r.name or r.email) if is_user else getattr(r, name_attr)
        for r in res.all()
        if getattr(r, "id", None)
    }


async def _resolve_entities_from_db(
    session: AsyncSession,
    type_to_ids: dict[str, set[uuid.UUID]],
) -> dict[uuid.UUID, str]:
    """Query domain models in batch to resolve entity names using centralized SSOT."""
    id_to_name: dict[uuid.UUID, str] = {}
    for etype, ids in type_to_ids.items():
        if not ids:
            continue
        model = resolve_entity_model(etype)
        if not model:
            continue
        is_user = etype in ("user", "users")
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


def resolve_audit_module(entity_type: str | None) -> tuple[str, str]:
    """Resolve canonical module_slug and module_name via common SSOT."""
    return resolve_module_metadata(entity_type)


def enrich_audit_modules(items: Sequence[AuditLog]) -> None:
    """Populate module_slug and module_name on AuditLog items in-place."""
    for item in items:
        slug, name = resolve_audit_module(item.entity_type)
        item.module_slug = slug  # type: ignore[attr-defined]
        item.module_name = name  # type: ignore[attr-defined]


class AuditService:
    """Business service governing audit trail querying and emission."""

    def __init__(
        self,
        repository: AuditRepository,
        settings_service: SystemSettingService | None = None,
    ) -> None:
        self.repository = repository
        self.settings_service = settings_service

    async def record_entry(self, entry: AuditEntry) -> AuditLogResponse:
        """Persist an audit entry and return the validated response."""
        record = await self.repository.record_entry(entry)
        await enrich_actors(self.repository.session, [record])
        await enrich_entity_names(self.repository.session, [record])
        enrich_audit_modules([record])
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
        self, params: AuditFilterParams, scope: ScopeContext | None = None
    ) -> PaginatedResponse[AuditLogResponse]:
        """Fetch filtered paginated audit logs with metadata and RBAC scope."""
        items, total = await self.repository.list_logs(params, scope=scope)
        await enrich_actors(self.repository.session, items)
        await enrich_entity_names(self.repository.session, items)
        enrich_audit_modules(items)
        return PaginatedResponse(
            data=[AuditLogResponse.model_validate(item) for item in items],
            meta=PaginationMeta.create(
                page=params.page,
                limit=params.limit,
                total=total,
            ),
        )

    async def get_by_id(
        self, log_id: uuid.UUID, scope: ScopeContext | None = None
    ) -> AuditLogResponse:
        """Retrieve a single audit log entry by ID enforcing RBAC scope."""
        item: AuditLog | None = await self.repository.get_by_id(log_id, scope=scope)
        if not item:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Audit log entry not found",
            )
        await enrich_actors(self.repository.session, [item])
        await enrich_entity_names(self.repository.session, [item])
        enrich_audit_modules([item])
        return AuditLogResponse.model_validate(item)

    async def get_entity_history(
        self,
        entity_type: str,
        entity_id: uuid.UUID,
        scope: ScopeContext | None = None,
    ) -> list[AuditLogResponse]:
        """Retrieve full chronological for a specific entity enforcing RBAC scope."""
        items = await self.repository.get_entity_history(
            entity_type, entity_id, scope=scope
        )
        await enrich_actors(self.repository.session, items)
        await enrich_entity_names(self.repository.session, items)
        enrich_audit_modules(items)
        return [AuditLogResponse.model_validate(item) for item in items]

    async def purge_expired(self, limit: int | None = None) -> int:
        """Purge all audit logs whose retention period has expired."""
        days = DEFAULT_AUDIT_RETENTION_DAYS
        effective_limit = limit or DEFAULT_AUDIT_PURGE_LIMIT
        if self.settings_service is not None:
            try:
                raw_days = await self.settings_service.get_value(
                    "audit.retention_days", default=DEFAULT_AUDIT_RETENTION_DAYS
                )
                days = int(raw_days)
            except (ValueError, TypeError):
                days = DEFAULT_AUDIT_RETENTION_DAYS
            if limit is None:
                try:
                    raw_limit = await self.settings_service.get_value(
                        "audit.purge_limit", default=DEFAULT_AUDIT_PURGE_LIMIT
                    )
                    effective_limit = int(raw_limit)
                except (ValueError, TypeError):
                    effective_limit = DEFAULT_AUDIT_PURGE_LIMIT

        cutoff = datetime.now(UTC) - timedelta(days=days)
        return await self.repository.purge_before(cutoff=cutoff, limit=effective_limit)

    async def export_data(
        self,
        req: ExportRequest,
        limit: int = DEFAULT_AUDIT_EXPORT_LIMIT,
        scope: ScopeContext | None = None,
    ) -> ExportResult:
        """Export audit logs to CSV, Excel, or JSON format with standard headers."""
        effective_limit = (
            min(limit, MAX_AUDIT_EXPORT_LIMIT) if limit else DEFAULT_AUDIT_EXPORT_LIMIT
        )
        items = await self.repository.get_logs_for_export(
            ids=req.ids,
            filters=req.filters,
            sort_by=req.sort_by,
            sort_order=req.sort_order,
            limit=effective_limit + 1,
            scope=scope,
        )

        is_truncated = len(items) > effective_limit
        if is_truncated:
            items = items[:effective_limit]

        total_count = len(items)
        await enrich_actors(self.repository.session, items)
        await enrich_entity_names(self.repository.session, items)
        enrich_audit_modules(items)

        for item in items:
            user = getattr(item, "user", None)
            if user:
                if not item.actor_name and user.name:
                    item.actor_name = user.name
                if not item.actor_email and user.email:
                    item.actor_email = user.email

        rows = [
            AuditLogExportResponse.model_validate(item).model_dump(mode="json")
            for item in items
        ]
        content, media_type, filename = format_export(
            format=req.format,
            data=rows,
            slug="audit_logs",
            columns=req.columns,
        )
        return ExportResult(
            content=content,
            media_type=media_type,
            filename=filename,
            total_count=total_count,
            is_truncated=is_truncated,
        )


async def purge_expired_audit(
    session: AsyncSession,
    limit: int = DEFAULT_AUDIT_PURGE_LIMIT,
    settings_service: SystemSettingService | None = None,
) -> int:
    """Purge expired audit records in a database session."""
    repo = AuditRepository(session)
    service = AuditService(repo, settings_service=settings_service)
    return await service.purge_expired(limit=limit)
