from fastapi import FastAPI

from fastapi_plantilla.core.middlewares.request_id import (
    RequestIDMiddleware,
    get_request_id,
    set_request_id,
)

__all__ = [
    "RequestIDMiddleware",
    "get_request_id",
    "set_request_id",
    "setup_middlewares",
]


def setup_middlewares(app: FastAPI) -> None:
    """Register all application middlewares in proper execution order."""
    app.add_middleware(RequestIDMiddleware)
