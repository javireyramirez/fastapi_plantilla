import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from fastapi_plantilla.core.crud.repository import BaseRepository
from fastapi_plantilla.modules.api_keys.models import ApiKey

__all__ = ["ApiKeyRepository"]


class ApiKeyRepository(BaseRepository[ApiKey]):
    """Repository for managing API Key database records."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(ApiKey, session)

    async def get_by_key_hash(self, key_hash: str) -> ApiKey | None:
        """Find an active API Key by its secure SHA-256 hash."""
        stmt = (
            select(ApiKey)
            .options(selectinload(ApiKey.owner))
            .where(ApiKey.key_hash == key_hash)
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def update_last_used(self, key_id: uuid.UUID) -> None:
        """Update last_used_at timestamp for a validated API Key."""
        stmt = (
            update(ApiKey)
            .where(ApiKey.id == key_id)
            .values(last_used_at=datetime.now(UTC))
        )
        await self.session.execute(stmt)
        await self.session.flush()
