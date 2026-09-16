from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.database import get_db_session
from fastapi_plantilla.modules.jobs.repository import JobRepository
from fastapi_plantilla.modules.jobs.service import JobService

__all__ = ["get_job_repository", "get_job_service"]


async def get_job_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> JobRepository:
    """Provide a JobRepository instance bound to the request's DB session."""
    return JobRepository(session)


async def get_job_service(
    repo: Annotated[JobRepository, Depends(get_job_repository)],
) -> JobService:
    """Provide a JobService instance bound to the request's DB session."""
    return JobService(repo)
