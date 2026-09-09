import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from fastapi_plantilla.core.crud.schema import PaginatedResponse
from fastapi_plantilla.modules.audit.dependencies import get_audit_service
from fastapi_plantilla.modules.audit.schema import (
    AuditFilterParams,
    AuditLogResponse,
)
from fastapi_plantilla.modules.audit.service import AuditService
from fastapi_plantilla.modules.auth.dependencies import get_current_active_superuser
from fastapi_plantilla.modules.auth.schema import UserResponse

router = APIRouter(prefix="/audit", tags=["Audit"])


@router.get(
    "",
    response_model=PaginatedResponse[AuditLogResponse],
    summary="List paginated audit logs with filtering",
)
async def list_audit_logs(
    params: Annotated[AuditFilterParams, Depends()],
    service: AuditService = Depends(get_audit_service),
    _: UserResponse = Depends(get_current_active_superuser),
) -> PaginatedResponse[AuditLogResponse]:
    """Retrieve paginated audit logs filtered by entity, action, or date range."""
    return await service.list_logs(params)


@router.get(
    "/{id}",
    response_model=AuditLogResponse,
    summary="Get audit log entry by ID",
)
async def get_audit_log(
    id: uuid.UUID,
    service: AuditService = Depends(get_audit_service),
    _: UserResponse = Depends(get_current_active_superuser),
) -> AuditLogResponse:
    """Retrieve detailed metadata and field diffs for a specific audit log record."""
    return await service.get_by_id(id)


@router.get(
    "/entity/{entity_type}/{entity_id}",
    response_model=list[AuditLogResponse],
    summary="Get complete audit history for a specific entity",
)
async def get_entity_audit_history(
    entity_type: str,
    entity_id: uuid.UUID,
    service: AuditService = Depends(get_audit_service),
    _: UserResponse = Depends(get_current_active_superuser),
) -> list[AuditLogResponse]:
    """Retrieve all chronological audit records associated with a specific entity."""
    return await service.get_entity_history(entity_type, entity_id)
