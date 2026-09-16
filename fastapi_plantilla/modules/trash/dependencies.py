from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.database import get_db_session
from fastapi_plantilla.modules.settings.dependencies import get_settings_service
from fastapi_plantilla.modules.settings.service import SystemSettingService
from fastapi_plantilla.modules.trash.repository import TrashRepository
from fastapi_plantilla.modules.trash.service import TrashService

__all__ = ["get_trash_repository", "get_trash_service"]


def get_trash_repository(
    session: AsyncSession = Depends(get_db_session),
) -> TrashRepository:
    """Provide TrashRepository bound to request DB session."""
    return TrashRepository(session=session)


def get_trash_service(
    repository: TrashRepository = Depends(get_trash_repository),
    settings_service: SystemSettingService | None = Depends(get_settings_service),
) -> TrashService:
    """Provide TrashService instance."""
    return TrashService(repository=repository, settings_service=settings_service)
