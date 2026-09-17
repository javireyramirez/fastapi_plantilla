import uuid
from typing import Any

from fastapi_plantilla.core.events import EventBroadcaster, event_broadcaster
from fastapi_plantilla.modules.notifications.models import NotificationType
from fastapi_plantilla.modules.notifications.repository import NotificationRepository
from fastapi_plantilla.modules.notifications.schema import (
    NotificationFilterParams,
    NotificationResponse,
)

__all__ = ["NotificationService"]


class NotificationService:
    """Domain service managing notification lifecycle and SSE dispatch."""

    def __init__(
        self,
        repo: NotificationRepository,
        broadcaster: EventBroadcaster | None = None,
    ) -> None:
        self.repo = repo
        self.broadcaster = broadcaster or event_broadcaster

    async def list_user_notifications(
        self, user_id: uuid.UUID, params: NotificationFilterParams
    ) -> tuple[list[NotificationResponse], int]:
        """Retrieve paginated notifications for the user."""
        items, total = await self.repo.list_for_user(user_id, params)
        responses = [NotificationResponse.model_validate(it) for it in items]
        return responses, total

    async def get_unread_count(self, user_id: uuid.UUID) -> int:
        """Count unread notifications for the user."""
        return await self.repo.count_unread(user_id)

    async def mark_as_read(
        self, notification_id: uuid.UUID, user_id: uuid.UUID
    ) -> NotificationResponse | None:
        """Mark a specific notification as read."""
        notification = await self.repo.mark_as_read(notification_id, user_id)
        if not notification:
            return None
        return NotificationResponse.model_validate(notification)

    async def mark_all_as_read(self, user_id: uuid.UUID) -> int:
        """Mark all unread notifications of the user as read."""
        return await self.repo.mark_all_as_read(user_id)

    async def notify_user(
        self,
        recipient_id: uuid.UUID,
        title: str,
        message: str,
        notification_type: NotificationType = NotificationType.INFO,
        entity_type: str | None = None,
        entity_id: uuid.UUID | None = None,
        action_url: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> NotificationResponse:
        """Create a notification in DB and dispatch it live via SSE."""
        payload = data or {}
        notification = await self.repo.create(
            recipient_id=recipient_id,
            title=title,
            message=message,
            notification_type=notification_type,
            entity_type=entity_type,
            entity_id=entity_id,
            action_url=action_url,
            data=payload,
        )
        response = NotificationResponse.model_validate(notification)

        # Dispatch reactively to active SSE streams for this user
        self.broadcaster.publish_to_user(
            user_id=recipient_id,
            event="notification",
            data=response.model_dump(mode="json", by_alias=True),
            event_id=str(notification.id),
        )
        return response

    async def fan_out(
        self,
        user_ids: list[uuid.UUID],
        title: str,
        message: str,
        notification_type: NotificationType = NotificationType.INFO,
        entity_type: str | None = None,
        entity_id: uuid.UUID | None = None,
        action_url: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> int:
        """Create notifications for multiple users and dispatch live events."""
        payload = data or {}
        records = [
            {
                "recipient_id": uid,
                "title": title,
                "message": message,
                "notification_type": notification_type,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "action_url": action_url,
                "data": payload,
            }
            for uid in user_ids
        ]
        created = await self.repo.create_many(records)

        # Broadcast event to each recipient's active SSE connections
        for item in created:
            resp = NotificationResponse.model_validate(item)
            self.broadcaster.publish_to_user(
                user_id=item.recipient_id,
                event="notification",
                data=resp.model_dump(mode="json", by_alias=True),
                event_id=str(item.id),
            )
        return len(created)

    async def delete_notification(
        self, notification_id: uuid.UUID, user_id: uuid.UUID
    ) -> bool:
        """Delete a notification belonging to the user."""
        return await self.repo.delete(notification_id, user_id)
