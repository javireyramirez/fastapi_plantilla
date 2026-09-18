from typing import Any

from fastapi_plantilla.modules.email.builder import EmailPayload
from fastapi_plantilla.modules.email.dependencies import get_email_transport
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
    transport = get_email_transport()
    await transport.send(payload=ctx.payload)
    await ctx.update_progress(100, f"Email delivered to {', '.join(ctx.payload.to)}")
    return {
        "status": "sent",
        "to": [str(addr) for addr in ctx.payload.to],
        "subject": ctx.payload.subject,
    }
