from fastapi import APIRouter

from fastapi_plantilla.modules.audit.routes import router as audit_router
from fastapi_plantilla.modules.auth.routes import router as auth_router
from fastapi_plantilla.modules.companies.routes import router as companies_router
from fastapi_plantilla.modules.health.routes import router as health_router
from fastapi_plantilla.modules.rbac.routes import router as rbac_router
from fastapi_plantilla.modules.settings.routes import router as settings_router
from fastapi_plantilla.modules.storage.routes import router as storage_router
from fastapi_plantilla.modules.teams.routes import router as teams_router
from fastapi_plantilla.modules.trash.routes import router as trash_router
from fastapi_plantilla.modules.users.routes import router as users_router

api_router = APIRouter(prefix="/api")

api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(rbac_router)
api_router.include_router(teams_router)
api_router.include_router(users_router)
api_router.include_router(companies_router)
api_router.include_router(storage_router)
api_router.include_router(trash_router)
api_router.include_router(audit_router)
api_router.include_router(settings_router)


@api_router.get(
    "/export/formats",
    response_model=list[str],
    tags=["Export"],
    summary="Get globally supported export file formats",
)
async def get_global_export_formats() -> list[str]:
    """Return all export formats supported by the system."""
    from fastapi_plantilla.core.crud.exporter import openpyxl  # noqa: PLC0415
    from fastapi_plantilla.core.crud.schema import ExportFormat  # noqa: PLC0415

    formats = [f.value for f in ExportFormat]
    if openpyxl is None and ExportFormat.EXCEL.value in formats:
        formats.remove(ExportFormat.EXCEL.value)
    return formats
