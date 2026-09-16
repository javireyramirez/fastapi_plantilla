import re
from typing import Any, Self

from pydantic import BaseModel, EmailStr, Field, field_validator

from fastapi_plantilla.core.config import settings
from fastapi_plantilla.modules.email.renderer import TemplateRenderer

__all__ = ["EmailBuilder", "EmailPayload"]


def _html_to_plain_text(html_text: str) -> str:
    """Extract readable plain text from HTML content without external dependencies."""
    text = re.sub(
        r"<(script|style)[^>]*>.*?</\1>", "", html_text, flags=re.DOTALL | re.IGNORECASE
    )
    # Convert <a href="url">text</a> to text (url)
    text = re.sub(
        r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
        r"\2 (\1)",
        text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    text = re.sub(r"<br\s*/?>|</p>|</div>|</tr>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


class EmailPayload(BaseModel):
    """Data transfer object representing an email message."""

    to: list[EmailStr]
    subject: str
    html: str | None = None
    text: str | None = None
    from_email: EmailStr | None = None
    from_name: str | None = None
    reply_to: EmailStr | None = None
    cc: list[EmailStr] = Field(default_factory=list)
    bcc: list[EmailStr] = Field(default_factory=list)

    @field_validator("subject", "from_name", mode="before")
    @classmethod
    def sanitize_headers(cls, v: Any) -> Any:
        """Prevent email header injection by stripping CR and LF characters."""
        if isinstance(v, str):
            return "".join(v.splitlines()).strip()
        return v


class EmailBuilder:
    """Fluent builder for constructing email payloads."""

    def __init__(self, renderer: TemplateRenderer) -> None:
        """Initialize builder with template renderer."""
        self.renderer = renderer
        self._to: list[str] = []
        self._subject: str = ""
        self._html: str | None = None
        self._text: str | None = None
        self._from_email: str | None = None
        self._from_name: str | None = None
        self._reply_to: str | None = None
        self._cc: list[str] = []
        self._bcc: list[str] = []

    def to(self, *recipients: str) -> Self:
        """Add recipient email addresses, trimming whitespace and filtering empty."""
        cleaned = [r.strip().lower() for r in recipients if r and r.strip()]
        self._to = list(dict.fromkeys(self._to + cleaned))
        return self

    def subject(self, subject: str) -> Self:
        """Set email subject line."""
        self._subject = subject.strip()
        return self

    def template(
        self, template_name: str, context: dict[str, Any] | None = None, **kwargs: Any
    ) -> Self:
        """Render and set HTML body from template."""
        merged_context = {**(context or {}), **kwargs}
        self._html = self.renderer.render(
            template_name=template_name, context=merged_context
        )
        return self

    def html(self, html: str) -> Self:
        """Set raw HTML body content."""
        self._html = html
        return self

    def text(self, text: str) -> Self:
        """Set plain text body content."""
        self._text = text
        return self

    def sender(self, email: str, name: str | None = None) -> Self:
        """Set custom sender email and optional name."""
        self._from_email = email.strip().lower() if email else None
        if name is not None:
            self._from_name = name.strip() if name.strip() else None
        return self

    def reply_to(self, email: str) -> Self:
        """Set reply-to email address."""
        self._reply_to = email.strip().lower() if email else None
        return self

    def cc(self, *recipients: str) -> Self:
        """Add CC recipients, trimming whitespace and filtering empty."""
        cleaned = [r.strip().lower() for r in recipients if r and r.strip()]
        self._cc = list(dict.fromkeys(self._cc + cleaned))
        return self

    def bcc(self, *recipients: str) -> Self:
        """Add BCC recipients, trimming whitespace and filtering empty."""
        cleaned = [r.strip().lower() for r in recipients if r and r.strip()]
        self._bcc = list(dict.fromkeys(self._bcc + cleaned))
        return self

    def build(self) -> EmailPayload:
        """Validate state and construct immutable EmailPayload."""
        if not self._to:
            raise ValueError("At least one recipient is required.")
        if not self._subject.strip():
            raise ValueError("Email subject cannot be empty.")
        if not self._html and not self._text:
            raise ValueError("Email content (HTML or plain text) is required.")

        text_content = self._text
        if not text_content and self._html:
            text_content = _html_to_plain_text(self._html)

        from_name = (
            self._from_name
            if self._from_name is not None
            else (settings.emails_from_name or settings.app_name)
        )
        from_email = (
            self._from_email
            if self._from_email is not None
            else settings.emails_from_email
        )

        return EmailPayload(
            to=self._to,
            subject=self._subject,
            html=self._html,
            text=text_content,
            from_email=from_email,
            from_name=from_name,
            reply_to=self._reply_to,
            cc=self._cc,
            bcc=self._bcc,
        )
