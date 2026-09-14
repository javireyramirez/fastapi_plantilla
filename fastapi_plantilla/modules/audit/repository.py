import re
import uuid
from datetime import UTC
from typing import Any

from fastapi import Depends
from sqlalchemy import asc, desc
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.crud.repository import BaseRepository
from fastapi_plantilla.core.crud.schema import AuditEntry
from fastapi_plantilla.core.crud.service_base import adjust_end_of_day
from fastapi_plantilla.core.database import get_db_session
from fastapi_plantilla.modules.audit.models import AuditLog
from fastapi_plantilla.modules.audit.schema import AuditFilterParams

__all__ = ["AuditRepository", "normalize_entity_types"]


def normalize_entity_types(raw_type: str) -> list[str]:
    """Return candidate entity_type strings covering singular and plural forms."""
    raw = raw_type.strip().lower()
    candidates = {raw}

    module_synonyms: dict[str, set[str]] = {
        "storage": {"storage", "storages", "document", "documents"},
        "document": {"storage", "storages", "document", "documents"},
        "documents": {"storage", "storages", "document", "documents"},
        "roles": {"roles", "role", "rbac"},
        "role": {"roles", "role", "rbac"},
        "rbac": {"roles", "role", "rbac"},
        "users": {"users", "user", "auth", "session", "sessions"},
        "user": {"users", "user", "auth", "session", "sessions"},
        "companies": {"companies", "company"},
        "company": {"companies", "company"},
        "teams": {"teams", "team"},
        "team": {"teams", "team"},
        "settings": {"settings", "setting"},
        "setting": {"settings", "setting"},
        "auth": {"users", "user", "auth", "session", "sessions"},
        "trash": {"trash", "trashitem"},
        "audit": {"audit", "audits"},
    }
    if raw in module_synonyms:
        candidates.update(module_synonyms[raw])

    if raw.endswith("ies"):
        candidates.add(raw[:-3] + "y")
    elif raw.endswith("es"):
        candidates.add(raw[:-2])
        candidates.add(raw[:-1])
    elif raw.endswith("s"):
        candidates.add(raw[:-1])
    else:
        candidates.add(raw + "s")

    return list(candidates)


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
    effective_from = params.created_at_from or params.from_date
    if effective_from:
        if effective_from.tzinfo is None:
            effective_from = effective_from.replace(tzinfo=UTC)
        conditions.append(AuditLog.created_at >= effective_from)
    effective_to = params.created_at_to or params.to_date
    if effective_to:
        if effective_to.tzinfo is None:
            effective_to = effective_to.replace(tzinfo=UTC)
        conditions.append(AuditLog.created_at <= adjust_end_of_day(effective_to))
    return conditions


def _build_audit_conditions(params: AuditFilterParams) -> list[Any]:
    """Construct SQLAlchemy query filters from AuditFilterParams."""
    conditions: list[Any] = []
    if params.entity_type:
        raw_types = [t.strip() for t in params.entity_type.split(",") if t.strip()]
        all_candidates: set[str] = set()
        for rt in raw_types:
            all_candidates.update(normalize_entity_types(rt))
        if all_candidates:
            candidates = list(all_candidates)
            conditions.append(
                AuditLog.entity_type == candidates[0]
                if len(candidates) == 1
                else AuditLog.entity_type.in_(candidates)
            )
    if params.entity_id:
        conditions.append(AuditLog.entity_id == params.entity_id)
    if params.entity_name:
        conditions.append(AuditLog.entity_name.ilike(f"%{params.entity_name.strip()}%"))
    if params.action:
        conditions.append(_resolve_action_filter(params.action))
    effective_actor_id = params.actor_id or params.user_id
    if effective_actor_id:
        conditions.append(AuditLog.actor_id == effective_actor_id)
    conditions.extend(_resolve_date_conditions(params))
    return conditions


def _build_audit_order_by(sort_by: str | None, sort_order: Any = "desc") -> Any:
    """Build dynamic order by clause for audit log queries."""
    col_name = re.sub(r"(?<!^)(?=[A-Z])", "_", sort_by or "created_at").lower()
    if col_name == "module_slug":
        col_name = "entity_type"
    col = getattr(AuditLog, col_name, AuditLog.created_at)
    return desc(col) if str(sort_order).lower() == "desc" else asc(col)


class AuditRepository(BaseRepository[AuditLog]):
    """Repository handling persistence and filtering of immutable audit log entries."""

    def __init__(self, session: AsyncSession = Depends(get_db_session)) -> None:
        super().__init__(AuditLog, session)

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
        candidates = normalize_entity_types(entity_type)
        cond = (
            AuditLog.entity_type == candidates[0]
            if len(candidates) == 1
            else AuditLog.entity_type.in_(candidates)
        )
        return await self.find_many(
            cond,
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
            filter_params = AuditFilterParams.model_validate(filters)
            conditions.extend(_build_audit_conditions(filter_params))

        order_clause = _build_audit_order_by(sort_by, sort_order)
        return await self.find_many(
            *conditions,
            limit=limit,
            order_by=order_clause,
        )
