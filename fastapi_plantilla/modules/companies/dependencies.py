from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.modules.companies.repository import CompanyRepository
from fastapi_plantilla.modules.companies.service import CompanyService

__all__ = ["create_company_service", "get_company_service"]


def create_company_service(session: AsyncSession) -> CompanyService:
    """Instantiate a CompanyService bound directly to the given AsyncSession."""
    return CompanyService(CompanyRepository(session))


def get_company_service(
    service: CompanyService = Depends(),
) -> CompanyService:
    """Dependency provider yielding a configured CompanyService instance."""
    return service
