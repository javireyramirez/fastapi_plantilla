from fastapi import Depends

from fastapi_plantilla.modules.companies.service import CompanyService

__all__ = ["get_company_service"]


def get_company_service(
    service: CompanyService = Depends(),
) -> CompanyService:
    """Dependency provider yielding a configured CompanyService instance."""
    return service
