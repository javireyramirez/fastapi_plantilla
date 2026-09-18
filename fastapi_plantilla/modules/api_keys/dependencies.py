from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.database import get_db_session
from fastapi_plantilla.modules.api_keys.models import ApiKey
from fastapi_plantilla.modules.api_keys.repository import ApiKeyRepository
from fastapi_plantilla.modules.api_keys.service import ApiKeyService
from fastapi_plantilla.modules.auth.models import User

__all__ = [
    "create_api_key_service",
    "get_api_key",
    "get_api_key_service",
    "get_api_key_user",
    "require_api_key_scope",
]


def get_api_key_service(
    session: AsyncSession = Depends(get_db_session),
) -> ApiKeyService:
    """Dependency provider for ApiKeyService."""
    return ApiKeyService(ApiKeyRepository(session))


def create_api_key_service(session: AsyncSession) -> ApiKeyService:
    """Factory helper creating ApiKeyService from an existing session."""
    return ApiKeyService(ApiKeyRepository(session))


async def get_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    auth_header: str | None = Header(default=None, alias="Authorization"),
    service: ApiKeyService = Depends(get_api_key_service),
) -> ApiKey:
    """Validate external API Key from X-API-Key or Authorization: Bearer header."""
    raw_key = None
    if x_api_key and x_api_key.strip():
        raw_key = x_api_key.strip()
    elif auth_header and auth_header.startswith("Bearer ak_"):
        raw_key = auth_header.removeprefix("Bearer ").strip()

    if not raw_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API Key no proporcionada",
        )

    api_key = await service.validate_key(raw_key)
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API Key inválida o expirada",
        )
    return api_key


async def get_api_key_user(
    api_key: Annotated[ApiKey, Depends(get_api_key)],
) -> User:
    """Extract authenticated user entity associated with the validated API key."""
    if not api_key.owner or not api_key.owner.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuario asociado a la API Key inactivo o inexistente",
        )
    return api_key.owner


def require_api_key_scope(
    required_scope: str,
) -> Callable[..., Coroutine[Any, Any, ApiKey]]:
    """Enforce that an API key possesses the required scope or wildcard."""

    async def _scope_checker(
        api_key: Annotated[ApiKey, Depends(get_api_key)],
    ) -> ApiKey:
        if "*" in api_key.scopes or required_scope in api_key.scopes:
            return api_key
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Permisos insuficientes: se requiere el scope '{required_scope}'",
        )

    return _scope_checker
