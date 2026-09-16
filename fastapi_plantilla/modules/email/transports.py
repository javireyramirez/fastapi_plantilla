from email.message import EmailMessage
from email.utils import formataddr
from typing import Final, Protocol

import aiosmtplib
import resend
from loguru import logger
from resend.exceptions import ResendError

from fastapi_plantilla.core.config import settings
from fastapi_plantilla.modules.email.builder import EmailPayload

__all__ = [
    "DEFAULT_SMTP_TIMEOUT_SECONDS",
    "BaseTransport",
    "ConsoleTransport",
    "MemoryTransport",
    "ResendTransport",
    "SmtpTransport",
]

DEFAULT_SMTP_TIMEOUT_SECONDS: Final[float] = 10.0


class BaseTransport(Protocol):
    """Base protocol for email transport strategies."""

    async def send(self, payload: EmailPayload) -> None:
        """Send email payload through the transport."""
        ...


class ConsoleTransport(BaseTransport):
    """Console transport strategy logging emails for local development."""

    async def send(self, payload: EmailPayload) -> None:
        """Log email content directly to logger."""
        logger.info(
            f"[EMAIL CONSOLE] To: {payload.to} | Subject: {payload.subject} | "
            f"HTML: {len(payload.html) if payload.html else 0} chars | "
            f"Text: {len(payload.text) if payload.text else 0} chars"
        )


class MemoryTransport(BaseTransport):
    """In-memory transport storing sent emails for test verification."""

    def __init__(self) -> None:
        self.sent_emails: list[EmailPayload] = []

    @property
    def outbox(self) -> list[EmailPayload]:
        """Alias for sent_emails following test outbox convention."""
        return self.sent_emails

    async def send(self, payload: EmailPayload) -> None:
        """Store payload in memory."""
        self.sent_emails.append(payload)

    def clear(self) -> None:
        """Clear all stored emails."""
        self.sent_emails.clear()


def _resolve_smtp_port() -> int:
    """Resolve SMTP port based on settings, TLS/SSL flags, or environment."""
    if settings.smtp_port:
        return settings.smtp_port
    if settings.smtp_ssl:
        return 465
    if settings.smtp_tls:
        return 587
    if settings.environment in ("development", "test"):
        return 1025
    return 25


class SmtpTransport(BaseTransport):
    """SMTP email transport strategy."""

    def __init__(self, timeout: float = DEFAULT_SMTP_TIMEOUT_SECONDS) -> None:
        self.timeout = timeout

    async def send(self, payload: EmailPayload) -> None:
        """Send email payload via SMTP server."""
        host = settings.smtp_host
        if not host:
            if settings.environment in ("development", "test"):
                host = "localhost"
            else:
                raise ValueError("SMTP host is not configured in settings.")

        port = _resolve_smtp_port()

        msg = EmailMessage()
        from_name = payload.from_name or settings.emails_from_name or settings.app_name
        from_email = payload.from_email or settings.emails_from_email or ""
        msg["From"] = formataddr((from_name, from_email))
        msg["To"] = ", ".join(email for email in payload.to if email)
        msg["Subject"] = payload.subject
        if payload.text:
            msg.set_content(payload.text)
            if payload.html:
                msg.add_alternative(payload.html, subtype="html")
        elif payload.html:
            msg.set_content(payload.html, subtype="html")

        if payload.reply_to:
            msg["Reply-To"] = payload.reply_to
        if payload.cc:
            msg["Cc"] = ", ".join(payload.cc)
        if payload.bcc:
            msg["Bcc"] = ", ".join(payload.bcc)

        try:
            await aiosmtplib.send(
                msg,
                hostname=host,
                port=port,
                username=settings.smtp_user,
                password=settings.smtp_password,
                start_tls=settings.smtp_tls or False,
                use_tls=settings.smtp_ssl or False,
                timeout=self.timeout,
            )
        except Exception as exc:
            logger.error(f"Error sending email via SMTP to {payload.to}: {exc}")
            raise


class ResendTransport(BaseTransport):
    """Resend API transport strategy."""

    async def send(self, payload: EmailPayload) -> None:
        """Send email payload via Resend API."""
        if not settings.resend_api_key:
            raise ValueError("Resend API key is not configured in settings.")

        resend.api_key = settings.resend_api_key

        from_name = payload.from_name or settings.emails_from_name or settings.app_name
        from_email = payload.from_email or settings.emails_from_email or ""
        from_address = f"{from_name} <{from_email}>" if from_name else from_email

        params: resend.Emails.SendParams = {
            "from": from_address,
            "to": payload.to,
            "subject": payload.subject,
        }
        if payload.html:
            params["html"] = payload.html
        if payload.text:
            params["text"] = payload.text
        if payload.reply_to:
            params["reply_to"] = payload.reply_to
        if payload.cc:
            params["cc"] = payload.cc
        if payload.bcc:
            params["bcc"] = payload.bcc

        try:
            await resend.Emails.send_async(params)
        except ResendError as error:
            logger.error(f"Error sending email with Resend: {error}")
            raise
