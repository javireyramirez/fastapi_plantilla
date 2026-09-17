import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from fastapi_plantilla.core.crud.schema import (
    BulkIdsRequest,
    BulkResponse,
    ExportRequest,
    MessageResponse,
    PaginatedResponse,
    ScopeContext,
)
from fastapi_plantilla.modules.auth.dependencies import (
    get_current_session,
    get_current_user,
)
from fastapi_plantilla.modules.auth.schema import AuthResponse, UserResponse
from fastapi_plantilla.modules.rbac.dependencies import require_permission
from fastapi_plantilla.modules.rbac.schema import RbacActions
from fastapi_plantilla.modules.sessions.dependencies import get_session_admin_service
from fastapi_plantilla.modules.sessions.schema import (
    SessionAdminResponse,
    SessionPaginationParams,
)
from fastapi_plantilla.modules.sessions.service import SessionAdminService

router = APIRouter(prefix="/sessions", tags=["Sessions Admin"])


@router.get("", response_model=PaginatedResponse[SessionAdminResponse])
async def list_sessions(
    params: Annotated[SessionPaginationParams, Depends()],
    service: SessionAdminService = Depends(get_session_admin_service),
    scope: ScopeContext = Depends(require_permission("sessions", RbacActions.READ)),
    current_session: AuthResponse = Depends(get_current_session),
) -> PaginatedResponse[SessionAdminResponse]:
    """List paginated user sessions with scope enforcement and device identification."""
    current_token = current_session.session.token if current_session.session else None
    return await service.list_sessions(
        params=params,
        scope=scope,
        current_token=current_token,
    )


@router.get("/{session_id}", response_model=SessionAdminResponse)
async def get_session(
    session_id: uuid.UUID,
    service: SessionAdminService = Depends(get_session_admin_service),
    scope: ScopeContext = Depends(require_permission("sessions", RbacActions.READ)),
    current_session: AuthResponse = Depends(get_current_session),
) -> SessionAdminResponse:
    """Fetch session details by ID with scope enforcement."""
    current_token = current_session.session.token if current_session.session else None
    return await service.get_session(
        session_id=session_id,
        scope=scope,
        current_token=current_token,
    )


@router.delete("/{session_id}", response_model=MessageResponse)
async def revoke_session(
    session_id: uuid.UUID,
    request: Request,
    service: SessionAdminService = Depends(get_session_admin_service),
    scope: ScopeContext = Depends(require_permission("sessions", RbacActions.DELETE)),
    current_session: AuthResponse = Depends(get_current_session),
) -> MessageResponse:
    """Revoke a specific session by ID with audit logging."""
    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    current_token = current_session.session.token if current_session.session else None
    return await service.revoke_session(
        session_id=session_id,
        scope=scope,
        current_user=current_session.user,
        ip_address=ip_address,
        user_agent=user_agent,
        current_token=current_token,
    )


@router.post("/bulk/revoke", response_model=BulkResponse)
async def bulk_revoke_sessions(
    req: BulkIdsRequest,
    request: Request,
    service: SessionAdminService = Depends(get_session_admin_service),
    scope: ScopeContext = Depends(require_permission("sessions", RbacActions.DELETE)),
    current_user: UserResponse = Depends(get_current_user),
) -> BulkResponse:
    """Bulk revoke multiple active sessions in a single operation."""
    ip_address = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    return await service.bulk_revoke_sessions(
        req=req,
        scope=scope,
        current_user=current_user,
        ip_address=ip_address,
        user_agent=user_agent,
    )


@router.post("/export", response_class=Response)
async def export_sessions(
    req: ExportRequest,
    service: SessionAdminService = Depends(get_session_admin_service),
    scope: ScopeContext = Depends(require_permission("sessions", RbacActions.EXPORT)),
) -> Response:
    """Export filtered session records in CSV, Excel, TSV, or JSON format."""
    content, media_type, filename, count = await service.export_sessions(
        req=req,
        scope=scope,
    )
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "X-Total-Count": str(count),
    }
    return Response(
        content=content,
        media_type=media_type,
        headers=headers,
    )
