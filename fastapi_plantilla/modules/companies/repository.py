from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.crud.repository import BaseRepository
from fastapi_plantilla.core.database import get_db_session
from fastapi_plantilla.modules.companies.models import Company

__all__ = ["CompanyRepository"]


class CompanyRepository(BaseRepository[Company]):
    """Data repository for Company domain entities."""

    def __init__(self, session: AsyncSession = Depends(get_db_session)) -> None:
        super().__init__(Company, session)

    async def get_by_nif(self, nif: str) -> Company | None:
        """Fetch company by unique NIF identifier."""
        return await self.find_first(Company.nif == nif)
