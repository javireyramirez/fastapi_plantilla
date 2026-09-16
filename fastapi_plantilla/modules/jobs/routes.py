import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from fastapi_plantilla.core.crud.schema import (
    PaginatedResponse,
    PaginationMeta,
    ScopeContext,
)
from fastapi_plantilla.modules.jobs.dependencies import get_job_service
from fastapi_plantilla.modules.jobs.exceptions import JobNotFoundError
from fastapi_plantilla.modules.jobs.schema import (
    JobCancelResponse,
    JobCreateRequest,
    JobFilterParams,
    JobResponse,
    JobRetryResponse,
)
from fastapi_plantilla.modules.jobs.service import JobService
from fastapi_plantilla.modules.rbac.dependencies import require_permission
from fastapi_plantilla.modules.rbac.schema import RbacActions

__all__ = ["router"]

router = APIRouter(prefix="/jobs", tags=["Jobs"])


@router.post(
    "",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Enqueue a new background job",
)
async def enqueue_job(
    request: JobCreateRequest,
    service: Annotated[JobService, Depends(get_job_service)],
    scope: Annotated[
        ScopeContext, Depends(require_permission("jobs", RbacActions.CREATE))
    ],
) -> JobResponse:
    """Enqueue a job to be processed asynchronously by workers."""
    return await service.enqueue(request, created_by_id=scope.user_id)


@router.get(
    "",
    response_model=PaginatedResponse[JobResponse],
    summary="List background jobs with filtering, pagination and RBAC scope",
)
async def list_jobs(
    params: Annotated[JobFilterParams, Depends()],
    service: Annotated[JobService, Depends(get_job_service)],
    scope: Annotated[
        ScopeContext, Depends(require_permission("jobs", RbacActions.READ))
    ],
) -> PaginatedResponse[JobResponse]:
    """Retrieve paginated jobs filtered by name, status, or entity under user scope."""
    items, total = await service.list_jobs(params, scope=scope)
    meta = PaginationMeta.create(page=params.page, limit=params.limit, total=total)
    return PaginatedResponse(data=items, meta=meta)


@router.get(
    "/{job_id}",
    response_model=JobResponse,
    summary="Get background job status, progress, and result",
)
async def get_job(
    job_id: uuid.UUID,
    service: Annotated[JobService, Depends(get_job_service)],
    scope: Annotated[
        ScopeContext, Depends(require_permission("jobs", RbacActions.READ))
    ],
) -> JobResponse:
    """Fetch status and real-time execution progress of a specific job."""
    try:
        return await service.get_job(job_id, scope=scope)
    except JobNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.post(
    "/{job_id}/cancel",
    response_model=JobCancelResponse,
    summary="Cancel a pending or running background job",
)
async def cancel_job(
    job_id: uuid.UUID,
    service: Annotated[JobService, Depends(get_job_service)],
    scope: Annotated[
        ScopeContext, Depends(require_permission("jobs", RbacActions.UPDATE))
    ],
) -> JobCancelResponse:
    """Request cooperative cancellation of a background job under user scope."""
    try:
        return await service.cancel_job(job_id, scope=scope)
    except JobNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.post(
    "/{job_id}/retry",
    response_model=JobRetryResponse,
    summary="Reschedule a failed or cancelled background job",
)
async def retry_job(
    job_id: uuid.UUID,
    service: Annotated[JobService, Depends(get_job_service)],
    scope: Annotated[
        ScopeContext, Depends(require_permission("jobs", RbacActions.SETTINGS))
    ],
) -> JobRetryResponse:
    """Reschedule an uncompleted job back to PENDING (SETTINGS permission required)."""
    try:
        return await service.retry_job(job_id, scope=scope)
    except JobNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
