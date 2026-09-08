import uuid
from datetime import datetime

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.crud.repository import BaseRepository
from fastapi_plantilla.modules.trash.models import TrashItem
from fastapi_plantilla.modules.trash.schema import DEFAULT_TRASH_PURGE_LIMIT

__all__ = ["TrashRepository"]


class TrashRepository(BaseRepository[TrashItem]):
    """Database repository for centralized trash bin operations."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(TrashItem, session)

    async def get_by_entity(
        self,
        entity_type: str,
        entity_id: uuid.UUID,
    ) -> TrashItem | None:
        """Fetch trash record for a specific polymorphic entity."""
        return await self.find_first(
            TrashItem.entity_type == entity_type,
            TrashItem.entity_id == entity_id,
        )

    async def delete_by_entity(
        self,
        entity_type: str,
        entity_id: uuid.UUID,
    ) -> int:
        """Remove trash record associated with a specific entity."""
        stmt = delete(TrashItem).where(
            TrashItem.entity_type == entity_type,
            TrashItem.entity_id == entity_id,
        )
        result = await self.session.execute(stmt)
        return int(getattr(result, "rowcount", 0))

    async def find_expired(
        self,
        cutoff: datetime,
        limit: int = DEFAULT_TRASH_PURGE_LIMIT,
    ) -> list[TrashItem]:
        """Fetch trash items whose retention period has expired."""
        return await self.find_many(
            TrashItem.expires_at <= cutoff,
            limit=limit,
            order_by=TrashItem.expires_at.asc(),
        )

    async def count_expired(self, cutoff: datetime) -> int:
        """Count the number of expired trash items."""
        return await self.count(TrashItem.expires_at <= cutoff)
