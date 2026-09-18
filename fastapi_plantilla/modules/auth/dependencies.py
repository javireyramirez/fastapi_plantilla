from fastapi import Cookie, Depends, Header, HTTPException, Query, status

from fastapi_plantilla.core.config import settings
from fastapi_plantilla.modules.auth.schema import AuthResponse, UserResponse
from fastapi_plantilla.modules.auth.service import AuthService


async def get_current_session(
    session_token: str | None = Cookie(
        default=None,
        alias=settings.session_cookie_name,
    ),
    auth_header: str | None = Header(
        default=None,
        alias="Authorization",
    ),
    x_api_key: str | None = Header(
        default=None,
        alias="X-API-Key",
    ),
    service: AuthService = Depends(),
) -> AuthResponse:
    """Validate session token or API Key from header or cookie."""
    # 1. API Key authentication path
    api_key_raw = None
    if x_api_key and x_api_key.strip():
        api_key_raw = x_api_key.strip()
    elif auth_header and auth_header.startswith("Bearer ak_"):
        api_key_raw = auth_header.removeprefix("Bearer ").strip()

    if api_key_raw:
        from fastapi_plantilla.modules.api_keys.repository import (  # noqa: PLC0415
            ApiKeyRepository,
        )
        from fastapi_plantilla.modules.api_keys.service import (  # noqa: PLC0415
            ApiKeyService,
        )

        key_repo = ApiKeyRepository(service.repository.session)
        key_service = ApiKeyService(key_repo)
        api_key = await key_service.validate_key(api_key_raw)
        if not api_key or not api_key.owner or not api_key.owner.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="API Key inválida, inactiva o expirada",
            )
        return AuthResponse(
            user=UserResponse.model_validate(api_key.owner),
            session=None,
        )

    # 2. Standard Session Token path
    token = None
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.removeprefix("Bearer ").strip()
    if not token:
        token = session_token

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No autenticado",
        )

    return await service.get_session(token=token)


async def get_current_user(
    auth_data: AuthResponse = Depends(get_current_session),
) -> UserResponse:
    """Extract authenticated user from active session."""
    return auth_data.user


async def get_current_active_superuser(
    current_user: UserResponse = Depends(get_current_user),
) -> UserResponse:
    """Validate that the authenticated user has super admin privileges."""
    if not current_user.is_super_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permisos insuficientes",
        )
    return current_user


async def get_sse_user(
    token_query: str | None = Query(default=None, alias="token"),
    session_token: str | None = Cookie(
        default=None,
        alias=settings.session_cookie_name,
    ),
    auth_header: str | None = Header(
        default=None,
        alias="Authorization",
    ),
    service: AuthService = Depends(),
) -> UserResponse:
    """Validate user authentication for SSE streaming endpoints.

    Supports browser EventSource via query param ?token=..., session cookie,
    or standard Authorization: Bearer header.
    """
    token = None
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.removeprefix("Bearer ").strip()
    if not token:
        token = session_token
    if not token:
        token = token_query

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No autenticado",
        )

    auth_data = await service.get_session(token=token)
    if not auth_data.user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuario inactivo",
        )
    return auth_data.user
