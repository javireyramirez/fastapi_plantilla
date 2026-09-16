import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from fastapi_plantilla.core.crud.schema import PaginatedResponse, PaginationMeta
from fastapi_plantilla.modules.auth.dependencies import (
    get_current_active_superuser,
    get_current_user,
)
from fastapi_plantilla.modules.auth.schema import UserResponse
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
    current_user: Annotated[UserResponse, Depends(get_current_user)],
) -> JobResponse:
    """Enqueue a job to be processed asynchronously by workers."""
    return await service.enqueue(request, created_by_id=current_user.id)


@router.get(
    "",
    response_model=PaginatedResponse[JobResponse],
    summary="List background jobs with filtering and pagination",
)
async def list_jobs(
    params: Annotated[JobFilterParams, Depends()],
    service: Annotated[JobService, Depends(get_job_service)],
    _: Annotated[UserResponse, Depends(get_current_user)],
) -> PaginatedResponse[JobResponse]:
    """Retrieve paginated jobs filtered by name, status, or entity."""
    items, total = await service.list_jobs(params)
    total_pages = (total + params.limit - 1) // params.limit if total > 0 else 0
    meta = PaginationMeta(
        page=params.page,
        limit=params.limit,
        total=total,
        total_pages=total_pages,
        has_next=params.page < total_pages,
        has_prev=params.page > 1,
    )
    return PaginatedResponse(data=items, meta=meta)


@router.get(
    "/{job_id}",
    response_model=JobResponse,
    summary="Get background job status, progress, and result",
)
async def get_job(
    job_id: uuid.UUID,
    service: Annotated[JobService, Depends(get_job_service)],
    _: Annotated[UserResponse, Depends(get_current_user)],
) -> JobResponse:
    """Fetch status and real-time execution progress of a specific job."""
    try:
        return await service.get_job(job_id)
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
    _: Annotated[UserResponse, Depends(get_current_user)],
) -> JobCancelResponse:
    """Request cooperative cancellation of a background job."""
    try:
        return await service.cancel_job(job_id)
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
    _: Annotated[UserResponse, Depends(get_current_active_superuser)],
) -> JobRetryResponse:
    """Reschedule an uncompleted job back to PENDING (Superuser only)."""
    try:
        return await service.retry_job(job_id)
    except JobNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
