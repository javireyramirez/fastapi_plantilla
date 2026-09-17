import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse

from fastapi_plantilla.core.crud.schema import PaginatedResponse, PaginationMeta
from fastapi_plantilla.core.events import event_broadcaster
from fastapi_plantilla.modules.auth.dependencies import (
    get_current_user,
    get_sse_user,
)
from fastapi_plantilla.modules.auth.schema import UserResponse
from fastapi_plantilla.modules.notifications.dependencies import (
    get_notification_service,
)
from fastapi_plantilla.modules.notifications.schema import (
    MarkAllReadResponse,
    NotificationFilterParams,
    NotificationResponse,
    UnreadCountResponse,
)
from fastapi_plantilla.modules.notifications.service import NotificationService

__all__ = ["router"]

router = APIRouter(prefix="/notifications", tags=["Notifications"])


@router.get(
    "/unread-count",
    response_model=UnreadCountResponse,
    summary="Get unread notification count for current user badge",
)
async def get_unread_count(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    service: Annotated[NotificationService, Depends(get_notification_service)],
) -> UnreadCountResponse:
    """Retrieve total count of unread notifications for bell badge."""
    count = await service.get_unread_count(current_user.id)
    return UnreadCountResponse(unread_count=count)


@router.get(
    "/stream",
    summary="Real-time Server-Sent Events stream for user notifications",
)
async def stream_notifications(
    current_user: Annotated[UserResponse, Depends(get_sse_user)],
) -> StreamingResponse:
    """Establish SSE stream to receive in-app notifications live."""
    return StreamingResponse(
        event_broadcaster.subscribe_user(current_user.id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get(
    "",
    response_model=PaginatedResponse[NotificationResponse],
    summary="List in-app notifications for current user",
)
async def list_notifications(
    params: Annotated[NotificationFilterParams, Query()],
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    service: Annotated[NotificationService, Depends(get_notification_service)],
) -> PaginatedResponse[NotificationResponse]:
    """Fetch paginated notifications with optional unread_only and type filters."""
    items, total = await service.list_user_notifications(current_user.id, params)
    meta = PaginationMeta.create(page=params.page, limit=params.limit, total=total)
    return PaginatedResponse(data=items, meta=meta)


@router.patch(
    "/{notification_id}/read",
    response_model=NotificationResponse,
    summary="Mark single notification as read",
)
async def mark_as_read(
    notification_id: uuid.UUID,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    service: Annotated[NotificationService, Depends(get_notification_service)],
) -> NotificationResponse:
    """Mark a notification belonging to the current user as read."""
    updated = await service.mark_as_read(notification_id, current_user.id)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notificación no encontrada",
        )
    return updated


@router.post(
    "/read-all",
    response_model=MarkAllReadResponse,
    summary="Mark all notifications as read",
)
async def mark_all_as_read(
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    service: Annotated[NotificationService, Depends(get_notification_service)],
) -> MarkAllReadResponse:
    """Mark all unread notifications of the current user as read."""
    count = await service.mark_all_as_read(current_user.id)
    return MarkAllReadResponse(marked_count=count)


@router.delete(
    "/{notification_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a notification",
)
async def delete_notification(
    notification_id: uuid.UUID,
    current_user: Annotated[UserResponse, Depends(get_current_user)],
    service: Annotated[NotificationService, Depends(get_notification_service)],
) -> None:
    """Delete a notification owned by the current user."""
    deleted = await service.delete_notification(notification_id, current_user.id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notificación no encontrada",
        )
