from email.message import EmailMessage
from email.utils import formataddr
from typing import Protocol

import aiosmtplib
import resend
from loguru import logger
from resend.exceptions import ResendError

from fastapi_plantilla.modules.email.builder import EmailPayload
from fastapi_plantilla.settings import settings


class BaseTransport(Protocol):
    """Base protocol for email transport strategies."""

    async def send(self, payload: EmailPayload) -> None:
        """Send email payload through the transport."""
        ...


class SmtpTransport(BaseTransport):
    """SMTP email transport strategy."""

    async def send(self, payload: EmailPayload) -> None:
        """Send email payload via SMTP server."""
        msg = EmailMessage()
        from_name = payload.from_name or settings.emails_from_name or ""
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

        await aiosmtplib.send(
            msg,
            hostname=settings.smtp_host or "localhost",
            port=settings.smtp_port or 1025,
            username=settings.smtp_user,
            password=settings.smtp_password,
            start_tls=settings.smtp_tls or False,
            use_tls=settings.smtp_ssl or False,
        )


class ResendTransport(BaseTransport):
    """Resend API transport strategy."""

    async def send(self, payload: EmailPayload) -> None:
        """Send email payload via Resend API."""
        if not settings.resend_api_key:
            raise ValueError("Resend API key is not configured in settings.")

        resend.api_key = settings.resend_api_key

        from_name = payload.from_name or settings.emails_from_name
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
            raise error
