import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.crud.repository import BaseRepository
from fastapi_plantilla.core.mixins import RecordStatus
from fastapi_plantilla.modules.storage.models import Storage

__all__ = ["StorageRepository"]


class StorageRepository(BaseRepository[Storage]):
    """Database repository for Storage entity operations."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Storage, session)

    async def get_by_file_key(self, file_key: str) -> Storage | None:
        """Fetch storage record by its storage file key."""
        return await self.find_first(Storage.file_key == file_key)

    async def find_by_entity(
        self,
        entity_type: str,
        entity_id: uuid.UUID,
        include_trashed: bool = False,
        limit: int | None = 100,
        scope_filters: list[Any] | None = None,
    ) -> list[Storage]:
        """Fetch storage records associated with a specific entity."""
        filters: list[Any] = [
            Storage.entity_type == entity_type,
            Storage.entity_id == entity_id,
        ]
        if not include_trashed:
            filters.append(Storage.status != RecordStatus.TRASHED)
        if scope_filters:
            filters.extend(scope_filters)

        stmt = select(Storage).where(*filters).order_by(Storage.created_at.desc())
        if limit is not None:
            stmt = stmt.limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def find_uploaded_by_ids(
        self,
        ids: list[uuid.UUID],
        include_trashed: bool = False,
        scope_filters: list[Any] | None = None,
    ) -> list[Storage]:
        """Fetch uploaded storage records matching the provided IDs."""
        if not ids:
            return []
        filters: list[Any] = [
            Storage.id.in_(ids),
            Storage.is_uploaded == True,  # noqa: E712
        ]
        if not include_trashed:
            filters.append(Storage.status != RecordStatus.TRASHED)
        if scope_filters:
            filters.extend(scope_filters)

        stmt = select(Storage).where(*filters)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def purge_pending_orphans(self, cutoff: datetime) -> int:
        """Permanently delete unconfirmed presigned upload records older than cutoff."""
        stmt = delete(Storage).where(
            Storage.is_uploaded == False,  # noqa: E712
            Storage.created_at < cutoff,
        )
        res = await self.session.execute(stmt)
        return int(getattr(res, "rowcount", 0))
