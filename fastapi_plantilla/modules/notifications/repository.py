import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.config import settings
from fastapi_plantilla.core.mixins import generate_uuid7
from fastapi_plantilla.modules.notifications.models import Notification
from fastapi_plantilla.modules.notifications.schema import NotificationFilterParams

__all__ = ["NotificationRepository"]


class NotificationRepository:
    """Database persistence layer for user notifications."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_for_user(
        self, user_id: uuid.UUID, params: NotificationFilterParams
    ) -> tuple[Sequence[Notification], int]:
        """Fetch paginated notifications for a user, ordered by creation."""
        base_filter = [Notification.recipient_id == user_id]

        if params.unread_only:
            base_filter.append(Notification.read_at.is_(None))
        if params.type:
            base_filter.append(Notification.notification_type.in_(params.type))

        # Count total
        count_stmt = select(func.count(Notification.id)).where(*base_filter)
        total = (await self.session.execute(count_stmt)).scalar() or 0

        # Fetch records
        offset = (params.page - 1) * params.limit
        stmt = (
            select(Notification)
            .where(*base_filter)
            .order_by(Notification.created_at.desc())
            .offset(offset)
            .limit(params.limit)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all(), total

    async def count_unread(self, user_id: uuid.UUID) -> int:
        """Count total unread notifications for a user."""
        stmt = select(func.count(Notification.id)).where(
            Notification.recipient_id == user_id,
            Notification.read_at.is_(None),
        )
        return (await self.session.execute(stmt)).scalar() or 0

    async def get_by_id(
        self, notification_id: uuid.UUID, user_id: uuid.UUID
    ) -> Notification | None:
        """Get a single notification scoped to user."""
        stmt = select(Notification).where(
            Notification.id == notification_id,
            Notification.recipient_id == user_id,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def mark_as_read(
        self, notification_id: uuid.UUID, user_id: uuid.UUID
    ) -> Notification | None:
        """Mark single notification as read if unread, returning instance."""
        stmt = (
            update(Notification)
            .where(
                Notification.id == notification_id,
                Notification.recipient_id == user_id,
            )
            .values(read_at=func.now())
            .returning(Notification)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def mark_all_as_read(self, user_id: uuid.UUID) -> int:
        """Mark all unread notifications of a user as read."""
        stmt = (
            update(Notification)
            .where(
                Notification.recipient_id == user_id,
                Notification.read_at.is_(None),
            )
            .values(read_at=func.now())
        )
        result = await self.session.execute(stmt)
        return int(getattr(result, "rowcount", 0))

    async def create(self, **kwargs: Any) -> Notification:
        """Create and flush a new notification."""
        if "id" not in kwargs or not kwargs["id"]:
            kwargs["id"] = generate_uuid7()
        notification = Notification(**kwargs)
        self.session.add(notification)
        await self.session.flush()
        return notification

    async def create_many(
        self,
        items: list[dict[str, Any]],
        chunk_size: int | None = None,
    ) -> list[Notification]:
        """Create multiple notifications using batch chunking against DoS."""
        batch_size = chunk_size or settings.notifications_batch_chunk_size
        created_all: list[Notification] = []
        for i in range(0, len(items), batch_size):
            chunk = items[i : i + batch_size]
            instances: list[Notification] = []
            for d in chunk:
                item_data = dict(d)
                if "id" not in item_data or not item_data["id"]:
                    item_data["id"] = generate_uuid7()
                instances.append(Notification(**item_data))
            self.session.add_all(instances)
            await self.session.flush()
            created_all.extend(instances)
        return created_all

    async def delete(self, notification_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        """Delete a notification belonging to user."""
        stmt = sql_delete(Notification).where(
            Notification.id == notification_id,
            Notification.recipient_id == user_id,
        )
        result = await self.session.execute(stmt)
        return int(getattr(result, "rowcount", 0)) > 0
