from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.database import get_db_session
from fastapi_plantilla.modules.audit.repository import AuditRepository
from fastapi_plantilla.modules.auth.repository import AuthRepository
from fastapi_plantilla.modules.sessions.service import SessionAdminService

__all__ = ["get_session_admin_service"]


def get_session_admin_service(
    session: AsyncSession = Depends(get_db_session),
) -> SessionAdminService:
    """Dependency providing SessionAdminService configured with repositories."""
    return SessionAdminService(
        auth_repo=AuthRepository(session),
        audit_repo=AuditRepository(session),
    )
