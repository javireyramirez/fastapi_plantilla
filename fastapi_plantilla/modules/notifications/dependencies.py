from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.database import get_db_session
from fastapi_plantilla.modules.notifications.repository import NotificationRepository
from fastapi_plantilla.modules.notifications.service import NotificationService

__all__ = ["get_notification_service"]


def get_notification_service(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> NotificationService:
    """Dependency provider for NotificationService within request lifecycle."""
    repo = NotificationRepository(session)
    return NotificationService(repo)
