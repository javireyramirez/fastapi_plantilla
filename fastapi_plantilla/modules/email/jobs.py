from typing import Any

from loguru import logger

from fastapi_plantilla.modules.email.builder import EmailPayload, html_to_plain_text
from fastapi_plantilla.modules.email.dependencies import get_email_transport
from fastapi_plantilla.modules.email.email_log_service import EmailLogService
from fastapi_plantilla.modules.email.renderer import TemplateRenderer
from fastapi_plantilla.modules.email.repository import EmailLogRepository
from fastapi_plantilla.modules.email.schema import EmailLogStatus
from fastapi_plantilla.modules.jobs.registry import register_job
from fastapi_plantilla.modules.jobs.schema import JobContext

__all__ = ["handle_email_send"]


@register_job(
    "emails.send",
    payload_model=EmailPayload,
    title="Envío de Email",
    description="Envío transaccional asíncrono de correos con reintentos y backoff",
    category="communication",
    icon="mail",
    is_dispatchable=False,
)
async def handle_email_send(ctx: JobContext[EmailPayload]) -> dict[str, Any]:
    """Execute background email delivery using configured email transport."""
    # 1. Render template dynamically if HTML is not present in job payload
    if not ctx.payload.html and ctx.payload.template_name:
        renderer = TemplateRenderer()
        ctx.payload.html = renderer.render(
            template_name=ctx.payload.template_name,
            context=ctx.payload.template_context,
        )
        if not ctx.payload.text and ctx.payload.html:
            ctx.payload.text = html_to_plain_text(ctx.payload.html)

    valid_job_id = None
    if ctx.job_id:
        from fastapi_plantilla.modules.jobs.models import Job  # noqa: PLC0415

        if await ctx.session.get(Job, ctx.job_id):
            valid_job_id = ctx.job_id

    repo = EmailLogRepository(ctx.session)
    log_service = EmailLogService(repo)
    attempt = await repo.get_next_attempt(valid_job_id)

    transport = get_email_transport()
    recipients = [str(addr) for addr in ctx.payload.to]

    try:
        await transport.send(payload=ctx.payload)
        await log_service.record_attempt(
            to=recipients,
            subject=ctx.payload.subject,
            template_name=ctx.payload.template_name,
            job_id=valid_job_id,
            attempt=attempt,
            status=EmailLogStatus.SENT,
        )
        await ctx.update_progress(100, f"Email delivered to {', '.join(recipients)}")
        return {
            "status": "sent",
            "to": recipients,
            "subject": ctx.payload.subject,
            "attempt": attempt,
        }
    except Exception as exc:
        logger.error(
            f"Failed to send email for job {ctx.job_id} (attempt {attempt}): {exc}"
        )
        await log_service.record_attempt(
            to=recipients,
            subject=ctx.payload.subject,
            template_name=ctx.payload.template_name,
            job_id=valid_job_id,
            attempt=attempt,
            status=EmailLogStatus.FAILED,
            error=str(exc),
        )
        await ctx.session.flush()
        raise
