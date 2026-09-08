import asyncio
from typing import Any

from fastapi import FastAPI
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.config import settings
from fastapi_plantilla.modules.trash.repository import TrashRepository
from fastapi_plantilla.modules.trash.schema import DEFAULT_TRASH_PURGE_LIMIT
from fastapi_plantilla.modules.trash.service import TrashService

__all__ = [
    "purge_expired_trash",
    "run_periodic_trash_purge",
]


async def purge_expired_trash(
    session: AsyncSession,
    limit: int = DEFAULT_TRASH_PURGE_LIMIT,
) -> int:
    """Purge expired trash records in a database session."""
    repo = TrashRepository(session)
    service = TrashService(repo)
    return await service.purge_expired(limit=limit)


async def run_periodic_trash_purge(app: FastAPI, **kwargs: Any) -> None:
    """Run periodic background task to purge expired trash records."""
    interval_seconds = settings.trash_purge_interval_hours * 3600
    hours = settings.trash_purge_interval_hours
    logger.info(f"Trash purge background worker started (interval: {hours}h).")

    try:
        while True:
            await asyncio.sleep(interval_seconds)
            try:
                session_factory = getattr(app.state, "db_session_factory", None)
                if session_factory is None:
                    continue

                async with session_factory() as session:
                    purged = await purge_expired_trash(session)
                    await session.commit()
                    if purged > 0:
                        logger.info(
                            f"Periodic trash purge: purged {purged} expired records."
                        )
            except Exception as err:
                logger.error(f"Error during periodic trash purge execution: {err}")
    except asyncio.CancelledError:
        logger.info("Trash purge background worker successfully stopped.")
        raise
