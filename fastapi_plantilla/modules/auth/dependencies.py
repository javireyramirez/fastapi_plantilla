from fastapi import Cookie, Depends, Header, HTTPException, status

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
    service: AuthService = Depends(),
) -> AuthResponse:
    """Validate session token from cookie or Authorization header."""
    token = session_token
    if not token and auth_header and auth_header.startswith("Bearer "):
        token = auth_header.removeprefix("Bearer ").strip()

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
