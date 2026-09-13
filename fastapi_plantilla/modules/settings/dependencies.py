from fastapi import Depends

from fastapi_plantilla.modules.settings.service import SystemSettingService

__all__ = ["get_settings_service"]


def get_settings_service(
    service: SystemSettingService = Depends(),
) -> SystemSettingService:
    """Dependency injector for SystemSettingService."""
    return service
