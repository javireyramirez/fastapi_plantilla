import pkgutil
from collections.abc import AsyncGenerator
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from starlette.requests import Request

from fastapi_plantilla.core.config import settings

meta = sa.MetaData()


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""

    metadata = meta


def load_all_models() -> None:
    """Load all domain models from the modules directory."""
    modules_dir = Path(__file__).resolve().parent.parent / "modules"
    if modules_dir.exists():
        for module in pkgutil.walk_packages(
            path=[str(modules_dir)],
            prefix="fastapi_plantilla.modules.",
        ):
            if module.name.endswith(".models"):
                __import__(module.name)


async def get_db_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """Provide a transactional database session for the request lifecycle."""
    session: AsyncSession = request.app.state.db_session_factory()
    try:
        yield session
    finally:
        await session.commit()
        await session.close()


async def create_database() -> None:
    """Create application database if it does not exist."""
    db_url = make_url(str(settings.db_url.with_path("/postgres")))
    engine = create_async_engine(db_url, isolation_level="AUTOCOMMIT")

    async with engine.connect() as conn:
        database_existance = await conn.execute(
            text(
                f"SELECT 1 FROM pg_database WHERE datname='{settings.db_base}'",  # noqa: S608
            )
        )
        database_exists = database_existance.scalar() == 1

    if database_exists:
        await drop_database()

    async with engine.connect() as conn:
        await conn.execute(
            text(
                f'CREATE DATABASE "{settings.db_base}" ENCODING "utf8" TEMPLATE template1',  # noqa: E501
            )
        )
    await engine.dispose()


async def drop_database() -> None:
    """Drop application database if it exists."""
    db_url = make_url(str(settings.db_url.with_path("/postgres")))
    engine = create_async_engine(db_url, isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        disc_users = (
            "SELECT pg_terminate_backend(pg_stat_activity.pid) "  # noqa: S608
            "FROM pg_stat_activity "
            f"WHERE pg_stat_activity.datname = '{settings.db_base}' "
            "AND pid <> pg_backend_pid();"
        )
        await conn.execute(text(disc_users))
        await conn.execute(text(f'DROP DATABASE "{settings.db_base}"'))
    await engine.dispose()
