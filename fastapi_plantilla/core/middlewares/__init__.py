from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from fastapi_plantilla.core.config import settings
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

    origins = [
        "http://localhost",
        "http://127.0.0.1",
        "http://localhost:80",
        "http://127.0.0.1:80",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]
    if settings.frontend_url and settings.frontend_url not in origins:
        origins.append(settings.frontend_url)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
