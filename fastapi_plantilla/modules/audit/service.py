import uuid
from typing import Any

from fastapi import Depends, HTTPException, status

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

__all__ = ["AuditService"]


class AuditService:
    """Business service governing audit trail querying and emission."""

    def __init__(self, repository: AuditRepository = Depends()) -> None:
        self.repository = repository

    async def record_entry(self, entry: AuditEntry) -> AuditLogResponse:
        """Persist an audit entry and return the validated response."""
        record = await self.repository.record_entry(entry)
        return AuditLogResponse.model_validate(record)

    async def log(
        self,
        entity_type: str,
        action: str,
        entity_id: uuid.UUID | None = None,
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
        return AuditLogResponse.model_validate(item)

    async def get_entity_history(
        self, entity_type: str, entity_id: uuid.UUID
    ) -> list[AuditLogResponse]:
        """Retrieve full chronological history for a specific entity."""
        items = await self.repository.get_entity_history(entity_type, entity_id)
        return [AuditLogResponse.model_validate(item) for item in items]
