"""Initial system welcome notification seeder."""

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.mixins import generate_uuid7
from fastapi_plantilla.modules.auth.models import User
from fastapi_plantilla.modules.notifications.models import (
    Notification,
    NotificationType,
)

__all__ = ["WELCOME_MESSAGE", "WELCOME_TITLE", "seed_notifications"]

WELCOME_TITLE = "¡Bienvenido a tu nueva app!"
WELCOME_MESSAGE = (
    "Te damos la bienvenida al panel de control. Tu entorno ha sido "
    "configurado correctamente y está listo para comenzar a construir."
)


async def seed_notifications(
    session: AsyncSession,
    user: User | None = None,
) -> Notification | None:
    """Idempotently seed a welcome notification for the superadmin user.

    Args:
        session: Active async SQLAlchemy session.
        user: Optional User recipient (typically the initial superadmin).

    Returns:
        The seeded or existing welcome Notification instance, or None.
    """
    if user is None:
        logger.warning(
            "No recipient user provided for welcome notification. Skipping seed."
        )
        return None

    stmt = select(Notification).where(
        Notification.recipient_id == user.id,
        Notification.title == WELCOME_TITLE,
    )
    result = await session.execute(stmt)
    notification = result.scalar_one_or_none()

    if notification is None:
        notification = Notification(
            id=generate_uuid7(),
            recipient_id=user.id,
            title=WELCOME_TITLE,
            message=WELCOME_MESSAGE,
            notification_type=NotificationType.SUCCESS,
            entity_type="system",
            action_url="/dashboard",
            data={"welcome": True},
        )
        session.add(notification)
        await session.flush()
        logger.info(f"Notificación de bienvenida creada para: {user.email}")
    else:
        logger.info(f"Notificación de bienvenida ya existente para: {user.email}")

    return notification
