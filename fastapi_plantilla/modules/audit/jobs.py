from typing import Any

from loguru import logger
from pydantic import BaseModel, Field

from fastapi_plantilla.modules.audit.service import purge_expired_audit
from fastapi_plantilla.modules.jobs.registry import register_job
from fastapi_plantilla.modules.jobs.repository import JobRepository
from fastapi_plantilla.modules.jobs.scheduler import calculate_next_scheduled_time
from fastapi_plantilla.modules.jobs.schema import JobContext, JobCreateRequest
from fastapi_plantilla.modules.jobs.service import JobService
from fastapi_plantilla.modules.settings.repository import SystemSettingRepository
from fastapi_plantilla.modules.settings.service import SystemSettingService

__all__ = ["AuditPurgeJobPayload", "handle_audit_purge"]


class AuditPurgeJobPayload(BaseModel):
    """Payload controlling expired audit logs purge run."""

    limit: int = Field(default=1000, ge=1, le=10000)


@register_job(
    "audit.purge",
    payload_model=AuditPurgeJobPayload,
    title="Purga de Auditoría",
    description="Limpieza periódica de registros históricos de auditoría expirados",
    category="system",
    icon="shield",
    is_dispatchable=True,
)
async def handle_audit_purge(ctx: JobContext[AuditPurgeJobPayload]) -> dict[str, Any]:
    """Execute scheduled audit log purge and chain the next scheduled execution."""
    settings_service = SystemSettingService(SystemSettingRepository(ctx.session))

    auto_enabled = await settings_service.get_value(
        "audit.auto_purge_enabled", default=True
    )

    purged_count = 0
    if auto_enabled:
        purged_count = await purge_expired_audit(
            ctx.session,
            limit=ctx.payload.limit,
            settings_service=settings_service,
        )
        await ctx.update_progress(100, f"Purged {purged_count} expired audit logs")
    else:
        await ctx.update_progress(100, "Audit auto-purge is disabled in settings")

    # Schedule next run using deterministic idempotency key to prevent duplicates
    audit_time = await settings_service.get_value(
        "audit.purge_time_utc", default="03:30"
    )
    next_run = calculate_next_scheduled_time(
        audit_time, default_hour=3, default_minute=30
    )
    idempotency_key = f"audit.purge:{next_run.strftime('%Y-%m-%d')}"

    job_service = JobService(JobRepository(ctx.session))
    try:
        await job_service.enqueue(
            JobCreateRequest(
                name="audit.purge",
                payload={"limit": ctx.payload.limit},
                scheduled_at=next_run,
                idempotency_key=idempotency_key,
            )
        )
    except Exception as exc:
        logger.warning(f"Could not re-enqueue next audit.purge run: {exc}")

    return {
        "status": "completed" if auto_enabled else "disabled",
        "purged_count": purged_count,
        "next_scheduled_at": next_run.isoformat(),
    }
