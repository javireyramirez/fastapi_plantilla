from fastapi import Depends, Request

from fastapi_plantilla.core.crud.schema import ScopeContext, ScopeType, WriteOptions
from fastapi_plantilla.modules.auth.dependencies import get_current_user
from fastapi_plantilla.modules.auth.schema import UserResponse

__all__ = ["get_scope_context", "get_write_options"]


async def get_scope_context(
    current_user: UserResponse = Depends(get_current_user),
) -> ScopeContext:
    """Build default ScopeContext from the authenticated user."""
    return ScopeContext(
        scope=ScopeType.GLOBAL if current_user.is_super_admin else ScopeType.OWN,
        user_id=current_user.id,
        is_super_admin=current_user.is_super_admin,
    )


async def get_write_options(
    request: Request,
    current_user: UserResponse = Depends(get_current_user),
    scope: ScopeContext = Depends(get_scope_context),
) -> WriteOptions:
    """Build WriteOptions extracting actor and client metadata from the request."""
    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    return WriteOptions(
        user_id=current_user.id,
        scope=scope,
        user_agent=user_agent,
        ip_address=ip_address,
    )
