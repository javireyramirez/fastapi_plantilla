"""Application entrypoint exporting app for CLI and ASGI runners."""

from fastapi_plantilla.app import get_app

app = get_app()

__all__ = ["app"]
