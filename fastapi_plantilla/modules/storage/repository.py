import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.crud.repository import BaseRepository
from fastapi_plantilla.core.mixins import RecordStatus
from fastapi_plantilla.modules.storage.models import Document

__all__ = ["DocumentRepository"]


class DocumentRepository(BaseRepository[Document]):
    """Database repository for Document entity operations."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Document, session)

    async def get_by_file_key(self, file_key: str) -> Document | None:
        """Fetch document record by its storage file key."""
        return await self.find_first(Document.file_key == file_key)

    async def find_by_entity(
        self,
        entity_type: str,
        entity_id: uuid.UUID,
        include_trashed: bool = False,
    ) -> list[Document]:
        """Fetch documents associated with a specific entity."""
        filters = [
            Document.entity_type == entity_type,
            Document.entity_id == entity_id,
        ]
        if not include_trashed:
            filters.append(Document.status != RecordStatus.TRASHED)

        stmt = select(Document).where(*filters).order_by(Document.created_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def find_uploaded_by_ids(
        self, ids: list[uuid.UUID], include_trashed: bool = False
    ) -> list[Document]:
        """Fetch uploaded documents matching the provided IDs."""
        if not ids:
            return []
        filters = [
            Document.id.in_(ids),
            Document.is_uploaded == True,  # noqa: E712
        ]
        if not include_trashed:
            filters.append(Document.status != RecordStatus.TRASHED)

        stmt = select(Document).where(*filters)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())
