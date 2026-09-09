from fastapi_plantilla.core.crud.router import create_crud_router
from fastapi_plantilla.modules.companies.dependencies import get_company_service
from fastapi_plantilla.modules.companies.schema import (
    CompaniesPaginationParams,
    CompanyCreate,
    CompanyResponse,
    CompanyUpdate,
)

__all__ = ["router"]

router = create_crud_router(
    service_getter=get_company_service,
    schema_out=CompanyResponse,
    schema_create=CompanyCreate,
    schema_update=CompanyUpdate,
    prefix="/companies",
    tags=["Companies"],
    resource_name="companies",
    pagination_params=CompaniesPaginationParams,
)
