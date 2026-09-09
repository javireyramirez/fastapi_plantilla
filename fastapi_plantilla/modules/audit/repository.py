import uuid
from typing import Any

from fastapi import Depends
from sqlalchemy import desc
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.crud.repository import BaseRepository
from fastapi_plantilla.core.crud.schema import AuditEntry
from fastapi_plantilla.core.database import get_db_session
from fastapi_plantilla.modules.audit.models import AuditLog
from fastapi_plantilla.modules.audit.schema import AuditFilterParams

__all__ = ["AuditRepository"]


class AuditRepository(BaseRepository[AuditLog]):
    """Repository handling persistence and filtering of immutable audit log entries."""

    def __init__(self, session: AsyncSession = Depends(get_db_session)) -> None:
        super().__init__(AuditLog, session)

    async def record_entry(self, entry: AuditEntry) -> AuditLog:
        """Persist a new audit log record from an AuditEntry."""
        log = AuditLog(
            entity_type=entry.entity_type,
            entity_id=entry.entity_id,
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
        """Query paginated audit logs applying optional filters."""
        conditions: list[Any] = []

        if params.entity_type:
            conditions.append(AuditLog.entity_type == params.entity_type)
        if params.entity_id:
            conditions.append(AuditLog.entity_id == params.entity_id)
        if params.action:
            conditions.append(AuditLog.action == params.action)
        if params.actor_id:
            conditions.append(AuditLog.actor_id == params.actor_id)
        if params.from_date:
            conditions.append(AuditLog.created_at >= params.from_date)
        if params.to_date:
            conditions.append(AuditLog.created_at <= params.to_date)

        return await self.find_many_with_count(
            *conditions,
            skip=(params.page - 1) * params.limit,
            limit=params.limit,
            order_by=desc(AuditLog.created_at),
        )

    async def get_entity_history(
        self, entity_type: str, entity_id: uuid.UUID
    ) -> list[AuditLog]:
        """Fetch all chronological audit logs for a specific entity."""
        return await self.find_many(
            AuditLog.entity_type == entity_type,
            AuditLog.entity_id == entity_id,
            limit=500,
            order_by=desc(AuditLog.created_at),
        )
