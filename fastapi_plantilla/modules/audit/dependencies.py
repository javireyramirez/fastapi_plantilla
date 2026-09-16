from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.database import get_db_session
from fastapi_plantilla.modules.audit.repository import AuditRepository
from fastapi_plantilla.modules.audit.service import AuditService
from fastapi_plantilla.modules.settings.dependencies import get_settings_service
from fastapi_plantilla.modules.settings.service import SystemSettingService

__all__ = ["get_audit_repository", "get_audit_service"]


def get_audit_repository(
    session: AsyncSession = Depends(get_db_session),
) -> AuditRepository:
    """Provide AuditRepository bound to request DB session."""
    return AuditRepository(session=session)


def get_audit_service(
    repository: AuditRepository = Depends(get_audit_repository),
    settings_service: SystemSettingService | None = Depends(get_settings_service),
) -> AuditService:
    """Provide AuditService instance."""
    return AuditService(repository=repository, settings_service=settings_service)
