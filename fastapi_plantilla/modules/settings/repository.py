from collections.abc import Sequence

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.crud.repository import BaseRepository
from fastapi_plantilla.core.database import get_db_session
from fastapi_plantilla.modules.settings.models import SystemSetting

__all__ = ["SystemSettingRepository"]


class SystemSettingRepository(BaseRepository[SystemSetting]):
    """Data repository for dynamic system settings."""

    def __init__(self, session: AsyncSession = Depends(get_db_session)) -> None:
        super().__init__(SystemSetting, session)

    async def get_by_key(self, key: str) -> SystemSetting | None:
        """Fetch setting by unique key."""
        return await self.find_first(SystemSetting.key == key)

    async def get_all_public(self) -> Sequence[SystemSetting]:
        """Fetch all settings marked as public."""
        stmt = select(SystemSetting).where(SystemSetting.is_public.is_(True))
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def get_by_category(self, category: str) -> Sequence[SystemSetting]:
        """Fetch settings grouped under a specific category."""
        stmt = (
            select(SystemSetting)
            .where(SystemSetting.category == category)
            .order_by(SystemSetting.key.asc())
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()
