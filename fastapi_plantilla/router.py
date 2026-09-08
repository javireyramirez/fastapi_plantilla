from fastapi import APIRouter

from fastapi_plantilla.modules.auth.routes import router as auth_router
from fastapi_plantilla.modules.health.routes import router as health_router
from fastapi_plantilla.modules.storage.routes import router as storage_router
from fastapi_plantilla.modules.trash.routes import router as trash_router

api_router = APIRouter(prefix="/api")

api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(storage_router)
api_router.include_router(trash_router)
