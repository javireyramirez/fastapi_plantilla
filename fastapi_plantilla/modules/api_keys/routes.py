from fastapi import Depends, status

from fastapi_plantilla.core.crud.router import create_crud_router
from fastapi_plantilla.core.crud.schema import ScopeContext
from fastapi_plantilla.modules.api_keys.dependencies import (
    create_api_key_service,
    get_api_key_service,
)
from fastapi_plantilla.modules.api_keys.schema import (
    ApiKeyCreate,
    ApiKeyCreatedResponse,
    ApiKeyPaginationParams,
    ApiKeyResponse,
    ApiKeyUpdate,
)
from fastapi_plantilla.modules.api_keys.service import ApiKeyService
from fastapi_plantilla.modules.auth.dependencies import get_current_user
from fastapi_plantilla.modules.auth.schema import UserResponse
from fastapi_plantilla.modules.rbac.dependencies import require_permission
from fastapi_plantilla.modules.rbac.schema import RbacActions

__all__ = ["router"]

router = create_crud_router(
    service_getter=get_api_key_service,
    schema_out=ApiKeyResponse,
    schema_create=ApiKeyCreate,
    schema_update=ApiKeyUpdate,
    prefix="/api-keys",
    tags=["API Keys"],
    resource_name="api_keys",
    pagination_params=ApiKeyPaginationParams,
    service_factory=create_api_key_service,
    include_create=False,
    include_trash=False,
    include_export=False,
    include_import=False,
)


@router.post(
    "",
    response_model=ApiKeyCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Crear una nueva clave de API programática",
)
async def create_api_key(
    payload: ApiKeyCreate,
    scope: ScopeContext = Depends(require_permission("api_keys", RbacActions.CREATE)),
    current_user: UserResponse = Depends(get_current_user),
    service: ApiKeyService = Depends(get_api_key_service),
) -> ApiKeyCreatedResponse:
    """Create a new API key returning the raw secret token once."""
    api_key, raw_key = await service.create_key(
        schema=payload,
        owner_id=current_user.id,
        scope=scope,
    )
    data = ApiKeyResponse.model_validate(api_key).model_dump()
    return ApiKeyCreatedResponse(**data, raw_key=raw_key)
