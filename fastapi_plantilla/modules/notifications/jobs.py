from typing import Any

from fastapi_plantilla.modules.jobs.registry import register_job
from fastapi_plantilla.modules.jobs.schema import JobContext
from fastapi_plantilla.modules.notifications.repository import NotificationRepository
from fastapi_plantilla.modules.notifications.schema import NotificationFanOutPayload
from fastapi_plantilla.modules.notifications.service import NotificationService

__all__ = ["handle_notifications_fan_out"]


@register_job(
    "notifications.fan_out",
    payload_model=NotificationFanOutPayload,
    title="Difusión de Notificaciones",
    description=(
        "Distribución masiva de notificaciones y alertas a múltiples destinatarios"
    ),
    category="communication",
    icon="bell",
    is_dispatchable=False,
)
async def handle_notifications_fan_out(
    ctx: JobContext[NotificationFanOutPayload],
) -> dict[str, Any]:
    """Execute background fan-out delivering notifications to multiple recipients."""
    repo = NotificationRepository(ctx.session)
    service = NotificationService(repo)

    count = await service.fan_out(
        user_ids=ctx.payload.user_ids,
        title=ctx.payload.title,
        message=ctx.payload.message,
        notification_type=ctx.payload.notification_type,
        entity_type=ctx.payload.entity_type,
        entity_id=ctx.payload.entity_id,
        action_url=ctx.payload.action_url,
        data=ctx.payload.data,
    )
    await ctx.update_progress(100, f"Dispatched {count} notifications")
    return {"dispatched_count": count}
