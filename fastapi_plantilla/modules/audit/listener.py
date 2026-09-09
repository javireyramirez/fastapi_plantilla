from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.crud.schema import AuditEntry
from fastapi_plantilla.core.crud.service_audit import register_audit_sync_hook
from fastapi_plantilla.modules.audit.repository import AuditRepository

__all__ = ["setup_audit_listeners"]


async def _handle_audit_sync(
    session: AsyncSession,
    entry: AuditEntry,
) -> None:
    """Persist domain audit event emitted from BaseAuditService."""
    repo = AuditRepository(session)
    await repo.record_entry(entry)


def setup_audit_listeners() -> None:
    """Register audit synchronization hook with BaseAuditService."""
    register_audit_sync_hook(_handle_audit_sync)
