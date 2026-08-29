from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATES_DIR = Path(__file__).parent / "templates"


class TemplateRenderer:
    """HTML email template rendering engine."""

    def __init__(self, template_dir: Path = TEMPLATES_DIR) -> None:
        """Initialize Jinja2 environment with filesystem loader."""
        self.env = Environment(
            loader=FileSystemLoader(str(template_dir)),
            autoescape=select_autoescape(["html", "xml"]),
        )

    def render(self, template_name: str, context: dict[str, Any] | None = None) -> str:
        """Render template with dynamic context into HTML string."""
        template = self.env.get_template(template_name)
        return template.render(**(context or {}))
