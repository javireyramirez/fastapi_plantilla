import uuid
from typing import Any

from sqlalchemy import delete, func, inspect, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.database import Base


class BaseRepository[ModelT: Base]:
    """Generic repository providing asynchronous CRUD database operations."""

    def __init__(self, model: type[ModelT], session: AsyncSession) -> None:
        self.model = model
        self.session = session
        self.pk = inspect(model).primary_key[0]

    async def get_by_id(self, id: uuid.UUID) -> ModelT | None:
        """Retrieve a single record by its primary key ID."""
        stmt = select(self.model).where(self.pk == id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def find_first(self, *where: Any, order_by: Any = None) -> ModelT | None:
        """Fetch the first record matching given filter criteria."""
        stmt = select(self.model).where(*where)
        if order_by is not None:
            stmt = stmt.order_by(order_by)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def find_many(
        self, *where: Any, skip: int = 0, limit: int = 10, order_by: Any = None
    ) -> list[ModelT]:
        """Fetch a paginated list of records matching filter criteria."""
        stmt = select(self.model).where(*where).offset(skip).limit(limit)
        if order_by is not None:
            stmt = stmt.order_by(order_by)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def find_many_with_count(
        self, *where: Any, skip: int = 0, limit: int = 10, order_by: Any = None
    ) -> tuple[list[ModelT], int]:
        """Fetch records and total count, skipping count query on partial first page."""
        items = await self.find_many(*where, skip=skip, limit=limit, order_by=order_by)
        if skip == 0 and len(items) < limit:
            return items, len(items)
        total = await self.count(*where)
        return items, total

    async def count(self, *where: Any) -> int:
        """Count the total number of records matching filter criteria."""
        stmt = select(func.count()).select_from(self.model).where(*where)
        result = await self.session.execute(stmt)
        return result.scalar() or 0

    async def exists(self, *where: Any) -> bool:
        """Check whether any record matches the given filter criteria."""
        stmt = select(select(self.model).where(*where).exists())
        return bool(await self.session.scalar(stmt))

    async def create(self, data: dict[str, Any] | ModelT) -> ModelT:
        """Insert a new record into the database."""
        instance = self.model(**data) if isinstance(data, dict) else data
        self.session.add(instance)
        await self.session.flush()
        await self.session.refresh(instance)
        return instance

    async def create_many(self, data_list: list[dict[str, Any]]) -> int:
        """Insert multiple records into the database in a batch."""
        if not data_list:
            return 0
        instances = [self.model(**d) if isinstance(d, dict) else d for d in data_list]
        self.session.add_all(instances)
        await self.session.flush()
        return len(instances)

    async def update(
        self,
        id: uuid.UUID,
        data: dict[str, Any],
        *where: Any,
        expected_version: int | None = None,
    ) -> ModelT | None:
        """Update a record by ID and optional filters with optimistic lock check."""
        where_clauses: list[Any] = [self.pk == id, *where]
        values = {**data}

        version_attr = "version"
        if expected_version is not None and hasattr(self.model, version_attr):
            version_col = getattr(self.model, version_attr)
            where_clauses.append(version_col == expected_version)
            values[version_attr] = expected_version + 1

        stmt = (
            update(self.model)
            .where(*where_clauses)
            .values(**values)
            .returning(self.model)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def update_many(
        self,
        *where: Any,
        data: dict[str, Any],
    ) -> int:
        """Update multiple records matching filter criteria."""
        if not data:
            return 0
        stmt = update(self.model).where(*where).values(**data)
        result = await self.session.execute(stmt)
        return int(getattr(result, "rowcount", 0))

    async def delete(self, id: uuid.UUID, *where: Any) -> ModelT | None:
        """Physically delete a single record matching primary key and filters."""
        stmt = delete(self.model).where(self.pk == id, *where).returning(self.model)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def delete_many(self, ids: list[uuid.UUID], *where: Any) -> int:
        """Physically delete multiple records matching primary keys and filters."""

        if not ids:
            return 0
        stmt = delete(self.model).where(self.pk.in_(ids), *where)
        result = await self.session.execute(stmt)
        return int(getattr(result, "rowcount", 0))
