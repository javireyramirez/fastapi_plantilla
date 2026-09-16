from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from fastapi_plantilla.core.config import settings

TEMPLATES_DIR = Path(__file__).parent / "templates"


class TemplateRenderer:
    """HTML email template rendering engine."""

    def __init__(self, template_dir: Path = TEMPLATES_DIR) -> None:
        """Initialize Jinja2 environment with filesystem loader and global defaults."""
        self.env = Environment(
            loader=FileSystemLoader(str(template_dir)),
            autoescape=select_autoescape(["html", "xml"]),
        )
        self.env.globals.update(
            app_name=settings.app_name,
            frontend_url=settings.frontend_url or "",
            support_email=settings.support_email or settings.emails_from_email or "",
        )

    def render(self, template_name: str, context: dict[str, Any] | None = None) -> str:
        """Render template with dynamic context into HTML string."""
        template = self.env.get_template(template_name)
        ctx: dict[str, Any] = {"current_year": datetime.now(UTC).year}
        if context:
            ctx.update(context)
        return template.render(**ctx)
