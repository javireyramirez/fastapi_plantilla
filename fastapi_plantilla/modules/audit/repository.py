import re
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import asc, delete, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.crud.repository import BaseRepository
from fastapi_plantilla.core.crud.schema import AuditEntry
from fastapi_plantilla.core.crud.service_base import adjust_end_of_day
from fastapi_plantilla.modules.audit.models import AuditLog
from fastapi_plantilla.modules.audit.schema import (
    DEFAULT_AUDIT_PURGE_LIMIT,
    AuditFilterParams,
)

__all__ = ["AuditRepository"]

_SORT_ALIASES: dict[str, str] = {
    "module_slug": "entity_type",
    "user_name": "actor_name",
    "user_email": "actor_email",
}

_ALLOWED_SORT_COLUMNS: frozenset[str] = frozenset(
    {
        "id",
        "created_at",
        "entity_type",
        "entity_id",
        "entity_name",
        "action",
        "actor_id",
        "actor_name",
        "actor_email",
        "ip_address",
        "user_agent",
    }
)


def _resolve_action_filter(action: str) -> Any:
    """Resolve action filter clause handling grouped actions."""
    act = action.strip().upper()
    if act in ("DELETE", "PERMANENT_DELETE", "PURGE"):
        return AuditLog.action.in_(["DELETE", "PERMANENT_DELETE", "PURGE"])
    if act in ("REACTIVATE", "ACTIVATE"):
        return AuditLog.action.in_(["REACTIVATE", "ACTIVATE"])
    return AuditLog.action == act


def _resolve_date_conditions(params: AuditFilterParams) -> list[Any]:
    """Build date range comparison conditions with timezone normalization."""
    conditions: list[Any] = []
    if params.created_at_from:
        effective_from = params.created_at_from
        if effective_from.tzinfo is None:
            effective_from = effective_from.replace(tzinfo=UTC)
        conditions.append(AuditLog.created_at >= effective_from)
    if params.created_at_to:
        effective_to = params.created_at_to
        if effective_to.tzinfo is None:
            effective_to = effective_to.replace(tzinfo=UTC)
        conditions.append(AuditLog.created_at <= adjust_end_of_day(effective_to))
    return conditions


def _build_audit_conditions(params: AuditFilterParams) -> list[Any]:
    """Construct SQLAlchemy query filters from AuditFilterParams."""
    conditions: list[Any] = []
    if params.entity_type:
        raw_types = [
            t.strip().lower() for t in params.entity_type.split(",") if t.strip()
        ]
        if len(raw_types) == 1:
            conditions.append(AuditLog.entity_type == raw_types[0])
        elif raw_types:
            conditions.append(AuditLog.entity_type.in_(raw_types))
    if params.entity_id:
        conditions.append(AuditLog.entity_id == params.entity_id)
    if params.entity_name:
        conditions.append(
            AuditLog.entity_name.icontains(params.entity_name.strip(), autoescape=True)
        )
    if params.action:
        conditions.append(_resolve_action_filter(params.action))
    if params.actor_id:
        conditions.append(AuditLog.actor_id == params.actor_id)
    conditions.extend(_resolve_date_conditions(params))
    return conditions


def _build_audit_order_by(sort_by: str | None, sort_order: Any = "desc") -> Any:
    """Build dynamic order by clause for audit log queries with whitelist validation."""
    raw_col = re.sub(r"(?<!^)(?=[A-Z])", "_", sort_by or "created_at").lower()
    col_name = _SORT_ALIASES.get(raw_col, raw_col)
    if col_name in _ALLOWED_SORT_COLUMNS:
        col = getattr(AuditLog, col_name, AuditLog.created_at)
    else:
        col = AuditLog.created_at
    return desc(col) if str(sort_order).lower() == "desc" else asc(col)


class AuditRepository(BaseRepository[AuditLog]):
    """Repository handling persistence and filtering of immutable audit log entries."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(AuditLog, session)

    async def update(self, *args: Any, **kwargs: Any) -> Any:
        """Audit logs are immutable and cannot be modified."""
        raise NotImplementedError(
            "Audit logs are strictly immutable and cannot be updated."
        )

    async def update_many(self, *args: Any, **kwargs: Any) -> Any:
        """Audit logs are immutable and cannot be modified."""
        raise NotImplementedError(
            "Audit logs are strictly immutable and cannot be updated."
        )

    async def delete(self, *args: Any, **kwargs: Any) -> Any:
        """Audit logs are immutable and cannot be deleted."""
        raise NotImplementedError(
            "Audit logs are strictly immutable and cannot be deleted."
        )

    async def delete_many(self, *args: Any, **kwargs: Any) -> Any:
        """Audit logs are immutable and cannot be deleted."""
        raise NotImplementedError(
            "Audit logs are strictly immutable and cannot be deleted."
        )

    async def delete_where(self, *args: Any, **kwargs: Any) -> Any:
        """Audit logs are immutable and cannot be deleted."""
        raise NotImplementedError(
            "Audit logs are strictly immutable and cannot be deleted."
        )

    async def record_entry(self, entry: AuditEntry) -> AuditLog:
        """Persist a new audit log record from an AuditEntry."""
        log = AuditLog(
            entity_type=entry.entity_type,
            entity_id=entry.entity_id,
            entity_name=entry.entity_name,
            action=entry.action,
            actor_id=entry.actor_id,
            actor_name=entry.actor_name,
            actor_email=entry.actor_email,
            ip_address=entry.ip_address,
            user_agent=entry.user_agent,
            changes=entry.changes,
            details=entry.details,
        )
        self.session.add(log)
        await self.session.flush()
        return log

    async def list_logs(self, params: AuditFilterParams) -> tuple[list[AuditLog], int]:
        """Query paginated audit logs applying optional filters and dynamic ordering."""
        conditions = _build_audit_conditions(params)
        order_clause = _build_audit_order_by(params.sort_by, params.sort_order)
        return await self.find_many_with_count(
            *conditions,
            skip=(params.page - 1) * params.limit,
            limit=params.limit,
            order_by=order_clause,
        )

    async def get_entity_history(
        self, entity_type: str, entity_id: uuid.UUID
    ) -> list[AuditLog]:
        """Fetch all chronological audit logs for a specific entity."""
        return await self.find_many(
            AuditLog.entity_type == entity_type.strip().lower(),
            AuditLog.entity_id == entity_id,
            limit=500,
            order_by=desc(AuditLog.created_at),
        )

    async def get_logs_for_export(
        self,
        ids: list[uuid.UUID] | None = None,
        filters: dict[str, Any] | None = None,
        sort_by: str = "created_at",
        sort_order: Any = "desc",
        limit: int = 1000,
    ) -> list[AuditLog]:
        """Fetch audit log records for data export with optional filters."""
        conditions: list[Any] = []
        if ids:
            conditions.append(AuditLog.id.in_(ids))
        elif filters:
            try:
                filter_params = AuditFilterParams.model_validate(filters)
            except Exception:
                filter_params = AuditFilterParams()
            conditions.extend(_build_audit_conditions(filter_params))

        order_clause = _build_audit_order_by(sort_by, sort_order)
        return await self.find_many(
            *conditions,
            limit=limit,
            order_by=order_clause,
        )

    async def purge_before(
        self,
        cutoff: datetime,
        limit: int = DEFAULT_AUDIT_PURGE_LIMIT,
    ) -> int:
        """Purge audit logs older than cutoff date up to limit (retention policy)."""
        stmt = select(AuditLog.id).where(AuditLog.created_at < cutoff).limit(limit)
        res = await self.session.execute(stmt)
        target_ids = list(res.scalars().all())
        if not target_ids:
            return 0

        del_stmt = delete(AuditLog).where(AuditLog.id.in_(target_ids))
        del_res = await self.session.execute(del_stmt)
        await self.session.flush()
        rowcount = getattr(del_res, "rowcount", -1)
        return int(rowcount) if rowcount >= 0 else len(target_ids)
