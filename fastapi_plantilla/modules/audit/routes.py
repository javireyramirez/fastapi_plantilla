import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Response

from fastapi_plantilla.core.crud.schema import (
    ExportRequest,
    PaginatedResponse,
    ScopeContext,
)
from fastapi_plantilla.modules.audit.dependencies import get_audit_service
from fastapi_plantilla.modules.audit.schema import (
    AuditFilterParams,
    AuditLogResponse,
)
from fastapi_plantilla.modules.audit.service import AuditService
from fastapi_plantilla.modules.auth.dependencies import get_current_active_superuser
from fastapi_plantilla.modules.auth.schema import UserResponse
from fastapi_plantilla.modules.rbac.dependencies import require_permission
from fastapi_plantilla.modules.rbac.schema import RbacActions

router = APIRouter(
    prefix="/audit",
    tags=["Audit"],
)


@router.post(
    "/export",
    response_class=Response,
    summary="Export audit logs in CSV, Excel, or JSON format",
)
async def export_audit_logs(
    req: ExportRequest,
    service: AuditService = Depends(get_audit_service),
    scope: ScopeContext = Depends(require_permission("audit", RbacActions.EXPORT)),
) -> Response:
    """Export filtered audit logs."""
    result = await service.export_data(req, scope=scope)
    headers = {
        "Content-Disposition": f'attachment; filename="{result.filename}"',
        "X-Total-Count": str(result.total_count),
    }
    if result.is_truncated:
        headers["X-Export-Truncated"] = "true"
    return Response(
        content=result.content,
        media_type=result.media_type,
        headers=headers,
    )


@router.post(
    "/purge-expired",
    summary="Purge audit logs older than retention period",
)
async def purge_expired_logs(
    limit: int | None = None,
    service: AuditService = Depends(get_audit_service),
    current_user: UserResponse = Depends(get_current_active_superuser),
) -> dict[str, int]:
    """Purge audit logs older than retention period."""
    purged_count = await service.purge_expired(limit=limit)
    return {"purged_count": purged_count}


@router.get(
    "",
    response_model=PaginatedResponse[AuditLogResponse],
    summary="List paginated audit logs with filtering",
)
async def list_audit_logs(
    params: Annotated[AuditFilterParams, Depends()],
    service: AuditService = Depends(get_audit_service),
    scope: ScopeContext = Depends(require_permission("audit", RbacActions.READ)),
) -> PaginatedResponse[AuditLogResponse]:
    """Retrieve paginated audit logs filtered by entity, action, or date range."""
    return await service.list_logs(params, scope=scope)


@router.get(
    "/{id}",
    response_model=AuditLogResponse,
    summary="Get audit log entry by ID",
)
async def get_audit_log(
    id: uuid.UUID,
    service: AuditService = Depends(get_audit_service),
    scope: ScopeContext = Depends(require_permission("audit", RbacActions.READ)),
) -> AuditLogResponse:
    """Retrieve detailed metadata and field diffs for a specific audit log record."""
    return await service.get_by_id(id, scope=scope)


@router.get(
    "/entity/{entity_type}/{entity_id}",
    response_model=list[AuditLogResponse],
    summary="Get complete audit history for a specific entity",
)
async def get_entity_audit_history(
    entity_type: str,
    entity_id: uuid.UUID,
    service: AuditService = Depends(get_audit_service),
    scope: ScopeContext = Depends(require_permission("audit", RbacActions.READ)),
) -> list[AuditLogResponse]:
    """Retrieve all chronological audit records associated with a specific entity."""
    return await service.get_entity_history(entity_type, entity_id, scope=scope)
