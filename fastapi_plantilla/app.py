from fastapi import FastAPI

from fastapi_plantilla.core.lifespan import lifespan_setup
from fastapi_plantilla.core.logging import configure_logging
from fastapi_plantilla.router import api_router


def get_app() -> FastAPI:
    """
    Create and configure the FastAPI application.

    :return: FastAPI instance.
    """
    configure_logging()
    app = FastAPI(
        title="fastapi_plantilla",
        lifespan=lifespan_setup,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )

    # Main API router
    app.include_router(api_router)

    return app
