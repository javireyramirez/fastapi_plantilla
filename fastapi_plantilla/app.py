from fastapi import FastAPI

from fastapi_plantilla.core.config import settings
from fastapi_plantilla.core.lifespan import lifespan_setup
from fastapi_plantilla.core.logging import configure_logging
from fastapi_plantilla.core.middlewares import setup_middlewares
from fastapi_plantilla.modules.monitoring import (
    PrometheusASGIMiddleware,
    metrics_endpoint,
)
from fastapi_plantilla.router import api_router


def get_app() -> FastAPI:
    """
    Create and configure the FastAPI application.

    :return: FastAPI instance.
    """
    configure_logging()
    app = FastAPI(
        title=settings.app_name,
        lifespan=lifespan_setup,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )

    # Middlewares
    setup_middlewares(app)

    # Main API router
    app.include_router(api_router)

    # Observability & Metrics
    if settings.prometheus_enabled:
        app.add_middleware(PrometheusASGIMiddleware, fastapi_app=app)
        app.add_api_route(
            "/metrics",
            metrics_endpoint,
            methods=["GET"],
            include_in_schema=False,
        )

    return app
