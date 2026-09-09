from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Depends, HTTPException, status

from fastapi_plantilla.core.crud.schema import ScopeContext
from fastapi_plantilla.modules.auth.dependencies import get_current_user
from fastapi_plantilla.modules.auth.schema import UserResponse
from fastapi_plantilla.modules.rbac.schema import RbacActions
from fastapi_plantilla.modules.rbac.service import RbacService

__all__ = ["get_rbac_service", "require_permission"]


def get_rbac_service(service: RbacService = Depends()) -> RbacService:
    """Dependency providing RbacService instance."""
    return service


def require_permission(
    module_code: str, action: RbacActions
) -> Callable[..., Coroutine[Any, Any, ScopeContext]]:
    """
    FastAPI dependency factory enforcing granular RBAC permissions.

    Evaluates user's direct and team-inherited permissions for the given module
    and action, resolving the highest scope (GLOBAL > TEAM > OWN).
    Returns an authorized ScopeContext or raises HTTP 403 Forbidden.
    """

    async def _permission_checker(
        current_user: UserResponse = Depends(get_current_user),
        service: RbacService = Depends(get_rbac_service),
    ) -> ScopeContext:
        effective_scope, team_ids, teammate_ids = await service.resolve_user_permission(
            user_id=current_user.id,
            module_code=module_code,
            action=action,
            is_super_admin=current_user.is_super_admin,
        )

        if effective_scope is None:
            msg = f"Insufficient permissions for {action.value} on {module_code}"
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=msg,
            )

        return ScopeContext(
            scope=effective_scope,
            user_id=current_user.id,
            team_ids=team_ids,
            teammate_ids=teammate_ids,
            is_super_admin=current_user.is_super_admin,
        )

    return _permission_checker
