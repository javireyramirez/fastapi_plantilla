from typing import Any

from fastapi import APIRouter, Depends, Query, status

from fastapi_plantilla.modules.auth.dependencies import (
    get_current_active_superuser,
    get_current_user,
)
from fastapi_plantilla.modules.auth.schema import UserResponse
from fastapi_plantilla.modules.settings.dependencies import get_settings_service
from fastapi_plantilla.modules.settings.schema import (
    SettingCreate,
    SettingResponse,
    SettingUpdate,
)
from fastapi_plantilla.modules.settings.service import SystemSettingService

router = APIRouter(prefix="/settings", tags=["Settings"])


@router.get("/public", response_model=dict[str, Any])
async def get_public_settings(
    _: UserResponse = Depends(get_current_user),
    service: SystemSettingService = Depends(get_settings_service),
) -> dict[str, Any]:
    """
    Retrieve public system configuration map.

    Returns key-value settings accessible to the frontend (e.g. storage limits,
    allowed file types, application metadata) served from memory cache.
    """
    return await service.get_public_settings()


@router.get("", response_model=list[SettingResponse])
async def list_settings(
    category: str | None = Query(default=None),
    _: UserResponse = Depends(get_current_active_superuser),
    service: SystemSettingService = Depends(get_settings_service),
) -> list[SettingResponse]:
    """List all system settings, optionally filtered by category (SuperAdmin only)."""
    settings_list = await service.list_settings(category=category)
    return [SettingResponse.model_validate(s) for s in settings_list]


@router.post("", response_model=SettingResponse, status_code=status.HTTP_201_CREATED)
async def create_setting(
    data: SettingCreate,
    _: UserResponse = Depends(get_current_active_superuser),
    service: SystemSettingService = Depends(get_settings_service),
) -> SettingResponse:
    """Create a new system setting (SuperAdmin only)."""
    setting = await service.create_setting(data)
    return SettingResponse.model_validate(setting)


@router.get("/{key}", response_model=SettingResponse)
async def get_setting(
    key: str,
    _: UserResponse = Depends(get_current_active_superuser),
    service: SystemSettingService = Depends(get_settings_service),
) -> SettingResponse:
    """Retrieve detailed setting by key (SuperAdmin only)."""
    setting = await service.get_setting(key)
    return SettingResponse.model_validate(setting)


@router.patch("/{key}", response_model=SettingResponse)
async def update_setting(
    key: str,
    data: SettingUpdate,
    _: UserResponse = Depends(get_current_active_superuser),
    service: SystemSettingService = Depends(get_settings_service),
) -> SettingResponse:
    """
    Update setting value or metadata (SuperAdmin only).

    Automatically invalidates the in-memory cache.
    """
    setting = await service.update_setting(key, data)
    return SettingResponse.model_validate(setting)
