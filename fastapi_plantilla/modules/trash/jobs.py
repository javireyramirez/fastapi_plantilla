from typing import Any

from loguru import logger
from pydantic import BaseModel, Field

from fastapi_plantilla.modules.jobs.registry import register_job
from fastapi_plantilla.modules.jobs.repository import JobRepository
from fastapi_plantilla.modules.jobs.scheduler import calculate_next_scheduled_time
from fastapi_plantilla.modules.jobs.schema import JobContext, JobCreateRequest
from fastapi_plantilla.modules.jobs.service import JobService
from fastapi_plantilla.modules.settings.repository import SystemSettingRepository
from fastapi_plantilla.modules.settings.service import SystemSettingService
from fastapi_plantilla.modules.trash.service import purge_expired_trash

__all__ = ["TrashPurgeJobPayload", "handle_trash_purge"]


class TrashPurgeJobPayload(BaseModel):
    """Payload controlling expired trash purge run."""

    limit: int = Field(default=1000, ge=1, le=10000)


@register_job(
    "trash.purge",
    payload_model=TrashPurgeJobPayload,
    title="Purga de Papelera",
    description="Limpieza periódica y eliminación definitiva de registros expirados",
    category="system",
    icon="trash",
    is_dispatchable=True,
)
async def handle_trash_purge(ctx: JobContext[TrashPurgeJobPayload]) -> dict[str, Any]:
    """Execute scheduled trash purge and chain the next scheduled execution."""
    settings_service = SystemSettingService(SystemSettingRepository(ctx.session))

    auto_enabled = await settings_service.get_value(
        "trash.auto_purge_enabled", default=True
    )

    purged_count = 0
    if auto_enabled:
        purged_count = await purge_expired_trash(
            ctx.session,
            limit=ctx.payload.limit,
            settings_service=settings_service,
        )
        await ctx.update_progress(100, f"Purged {purged_count} expired trash items")
    else:
        await ctx.update_progress(100, "Trash auto-purge is disabled in settings")

    # Schedule next run using deterministic idempotency key to prevent duplicates
    trash_time = await settings_service.get_value(
        "trash.purge_time_utc", default="03:00"
    )
    next_run = calculate_next_scheduled_time(
        trash_time, default_hour=3, default_minute=0
    )
    idempotency_key = f"trash.purge:{next_run.strftime('%Y-%m-%d')}"

    job_service = JobService(JobRepository(ctx.session))
    try:
        await job_service.enqueue(
            JobCreateRequest(
                name="trash.purge",
                payload={"limit": ctx.payload.limit},
                scheduled_at=next_run,
                idempotency_key=idempotency_key,
            )
        )
    except Exception as exc:
        logger.warning(f"Could not re-enqueue next trash.purge run: {exc}")

    return {
        "status": "completed" if auto_enabled else "disabled",
        "purged_count": purged_count,
        "next_scheduled_at": next_run.isoformat(),
    }
