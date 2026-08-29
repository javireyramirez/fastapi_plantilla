from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from fastapi_plantilla.core.config import settings


def _setup_db(app: FastAPI) -> None:  # pragma: no cover
    """Initialize database engine and session factory on app state."""
    engine = create_async_engine(str(settings.db_url), echo=settings.db_echo)
    session_factory = async_sessionmaker(
        engine,
        expire_on_commit=False,
    )
    app.state.db_engine = engine
    app.state.db_session_factory = session_factory


@asynccontextmanager
async def lifespan_setup(
    app: FastAPI,
) -> AsyncGenerator[None, None]:  # pragma: no cover
    """Manage startup and shutdown lifecycle for FastAPI."""
    app.middleware_stack = None
    _setup_db(app)
    app.middleware_stack = app.build_middleware_stack()

    yield
    await app.state.db_engine.dispose()
