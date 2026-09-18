from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.modules.email.dependencies import get_email_service
from fastapi_plantilla.modules.rbac.repository import RbacRepository
from fastapi_plantilla.modules.settings.repository import SystemSettingRepository
from fastapi_plantilla.modules.settings.service import SystemSettingService
from fastapi_plantilla.modules.users.repository import UserAdminRepository
from fastapi_plantilla.modules.users.service import UserAdminService

__all__ = ["create_user_admin_service", "get_user_admin_service"]


def create_user_admin_service(session: AsyncSession) -> UserAdminService:
    """Instantiate a UserAdminService bound directly to the given AsyncSession."""

    return UserAdminService(
        repository=UserAdminRepository(session),
        rbac_repository=RbacRepository(session),
        email_service=get_email_service(),
        settings_service=SystemSettingService(SystemSettingRepository(session)),
    )


def get_user_admin_service(service: UserAdminService = Depends()) -> UserAdminService:
    """Dependency providing UserAdminService instance."""
    return service
