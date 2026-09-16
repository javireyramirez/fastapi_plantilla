from typing import Any

from fastapi import APIRouter, Depends, Query

from fastapi_plantilla.core.crud.exporter import get_supported_export_formats
from fastapi_plantilla.modules.auth.dependencies import (
    get_current_active_superuser,
    get_current_user,
)
from fastapi_plantilla.modules.auth.schema import UserResponse
from fastapi_plantilla.modules.settings.dependencies import get_settings_service
from fastapi_plantilla.modules.settings.schema import (
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


@router.get(
    "/export-formats",
    response_model=list[str],
    summary="Get globally supported export file formats",
)
async def get_export_formats() -> list[str]:
    """Return all export formats supported by the system (read-only)."""
    return get_supported_export_formats()


@router.get("", response_model=list[SettingResponse])
async def list_settings(
    category: str | None = Query(default=None),
    _: UserResponse = Depends(get_current_active_superuser),
    service: SystemSettingService = Depends(get_settings_service),
) -> list[SettingResponse]:
    """List all system settings, optionally filtered by category (SuperAdmin only)."""
    settings_list = await service.list_settings(category=category)
    return [SettingResponse.model_validate(s) for s in settings_list]


@router.get(
    "/categories",
    response_model=list[str],
    summary="List all setting categories",
)
async def list_categories(
    _: UserResponse = Depends(get_current_active_superuser),
    service: SystemSettingService = Depends(get_settings_service),
) -> list[str]:
    """Retrieve list of distinct setting categories (SuperAdmin only)."""
    return await service.get_categories()


@router.get("/{key}", response_model=SettingResponse)
async def get_setting(
    key: str,
    _: UserResponse = Depends(get_current_active_superuser),
    service: SystemSettingService = Depends(get_settings_service),
) -> SettingResponse:
    """Retrieve detailed setting by key (SuperAdmin only)."""
    setting = await service.get_setting(key.strip())
    return SettingResponse.model_validate(setting)


@router.patch("/{key}", response_model=SettingResponse)
async def update_setting(
    key: str,
    data: SettingUpdate,
    current_user: UserResponse = Depends(get_current_active_superuser),
    service: SystemSettingService = Depends(get_settings_service),
) -> SettingResponse:
    """
    Update setting value or metadata (SuperAdmin only).

    Automatically invalidates the in-memory cache and records an audit log.
    """
    setting = await service.update_setting(key.strip(), data, actor=current_user)
    return SettingResponse.model_validate(setting)
