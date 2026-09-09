from fastapi import Depends

from fastapi_plantilla.modules.users.service import UserAdminService

__all__ = ["get_user_admin_service"]


def get_user_admin_service(service: UserAdminService = Depends()) -> UserAdminService:
    """Dependency providing UserAdminService instance."""
    return service
