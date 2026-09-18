from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.database import get_db_session
from fastapi_plantilla.modules.settings.repository import SystemSettingRepository
from fastapi_plantilla.modules.settings.service import SystemSettingService

__all__ = ["get_settings_repository", "get_settings_service"]


def get_settings_repository(
    session: AsyncSession = Depends(get_db_session),
) -> SystemSettingRepository:
    """Dependency injector for SystemSettingRepository."""
    return SystemSettingRepository(session)


def get_settings_service(
    repository: SystemSettingRepository = Depends(get_settings_repository),
) -> SystemSettingService:
    """Dependency injector for SystemSettingService."""
    return SystemSettingService(repository)


get_system_setting_service = get_settings_service
