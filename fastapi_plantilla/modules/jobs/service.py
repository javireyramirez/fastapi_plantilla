import secrets
import uuid
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any

from loguru import logger
from pydantic import ValidationError

from fastapi_plantilla.core.config import settings
from fastapi_plantilla.core.crud.schema import ScopeContext, ScopeType
from fastapi_plantilla.core.events import event_broadcaster
from fastapi_plantilla.modules.jobs.constants import (
    DEFAULT_BACKOFF_BASE_SECONDS,
    DEFAULT_BACKOFF_MAX_SECONDS,
)
from fastapi_plantilla.modules.jobs.exceptions import (
    JobCancelledError,
    JobError,
    JobLeaseLostError,
    JobNotFoundError,
)
from fastapi_plantilla.modules.jobs.models import Job, JobStatus
from fastapi_plantilla.modules.jobs.registry import job_registry
from fastapi_plantilla.modules.jobs.repository import JobRepository
from fastapi_plantilla.modules.jobs.schema import (
    JobCancelResponse,
    JobContext,
    JobCreateRequest,
    JobFilterParams,
    JobResponse,
    JobRetryResponse,
)
from fastapi_plantilla.modules.notifications.models import NotificationType
from fastapi_plantilla.modules.notifications.repository import (
    NotificationRepository,
)

__all__ = ["JobService"]


def calculate_backoff_delay(attempts: int) -> timedelta:
    """Calculate exponential backoff with random jitter.

    Formula: min(base_delay * 2^(attempts-1) + jitter, max_delay) seconds.
    """
    jitter = secrets.SystemRandom().uniform(0.0, 1.0)
    delay_seconds = min(
        DEFAULT_BACKOFF_BASE_SECONDS * (2 ** max(0, attempts - 1)) + jitter,
        DEFAULT_BACKOFF_MAX_SECONDS,
    )
    return timedelta(seconds=delay_seconds)


class JobService:
    """Service layer managing the lifecycle of background jobs."""

    def __init__(self, repo: JobRepository) -> None:
        self.repo = repo

    async def enqueue(
        self,
        request: JobCreateRequest,
        created_by_id: uuid.UUID | None = None,
    ) -> JobResponse:
        """Enqueue a new job with optional user-namespaced idempotency."""
        if request.idempotency_key:
            # Namespace idempotency by user to prevent cross-user job hijacking/leakage
            scoped_key = (
                f"{created_by_id}:{request.idempotency_key}"
                if created_by_id
                else request.idempotency_key
            )
            existing = await self.repo.get_by_idempotency_key(scoped_key)
            if existing:
                return JobResponse.model_validate(existing)
        else:
            scoped_key = None

        job = await self.repo.create(
            name=request.name,
            payload=request.payload,
            entity_type=request.entity_type,
            entity_id=request.entity_id,
            max_retries=request.max_retries,
            lease_duration_seconds=request.lease_duration_seconds,
            scheduled_at=request.scheduled_at,
            idempotency_key=scoped_key,
            created_by_id=created_by_id,
        )
        return JobResponse.model_validate(job)

    def _verify_scope(self, job: Job, scope: ScopeContext | None) -> None:
        """Verify user has permission to access the specified job under RBAC scope."""
        if not scope or scope.is_super_admin:
            return

        if scope.scope == ScopeType.OWN and job.created_by_id != scope.user_id:
            raise JobNotFoundError(f"Job {job.id} not found.")

        if scope.scope == ScopeType.TEAM:
            allowed = set(scope.teammate_ids or [])
            if scope.user_id:
                allowed.add(scope.user_id)
            if job.created_by_id not in allowed:
                raise JobNotFoundError(f"Job {job.id} not found.")

    async def _get_scoped_job(
        self, job_id: uuid.UUID, scope: ScopeContext | None
    ) -> Job:
        """Fetch job by ID and verify RBAC scope."""
        job = await self.repo.get_by_id(job_id)
        if not job:
            raise JobNotFoundError(f"Job {job_id} not found.")
        self._verify_scope(job, scope)
        return job

    async def get_job(
        self, job_id: uuid.UUID, scope: ScopeContext | None = None
    ) -> JobResponse:
        """Fetch job details by ID enforcing RBAC scope."""
        job = await self._get_scoped_job(job_id, scope)
        return JobResponse.model_validate(job)

    async def list_jobs(
        self, params: JobFilterParams, scope: ScopeContext | None = None
    ) -> tuple[list[JobResponse], int]:
        """Fetch paginated list of jobs filtered by RBAC scope."""
        items, total = await self.repo.list_jobs(
            status=params.status,
            name=params.name,
            entity_type=params.entity_type,
            entity_id=params.entity_id,
            search=params.search,
            created_at_from=params.created_at_from,
            created_at_to=params.created_at_to,
            sort_by=params.sort_by,
            sort_order=params.sort_order,
            page=params.page,
            limit=params.limit,
            scope=scope,
        )
        return [JobResponse.model_validate(item) for item in items], total

    async def cancel_job(
        self, job_id: uuid.UUID, scope: ScopeContext | None = None
    ) -> JobCancelResponse:
        """Cancel a pending or running job enforcing RBAC scope."""
        existing = await self._get_scoped_job(job_id, scope)
        job = await self.repo.cancel_job(job_id)
        if not job:
            return JobCancelResponse(
                id=existing.id,
                status=existing.status,
                message=(
                    f"Job is already in {existing.status} state and "
                    "cannot be cancelled."
                ),
            )
        return JobCancelResponse(
            id=job.id,
            status=job.status,
            message="Job cancelled successfully.",
        )

    async def retry_job(
        self, job_id: uuid.UUID, scope: ScopeContext | None = None
    ) -> JobRetryResponse:
        """Retry a failed or cancelled job enforcing RBAC scope."""
        existing = await self._get_scoped_job(job_id, scope)
        job = await self.repo.retry_failed_job(job_id)
        if not job:
            return JobRetryResponse(
                id=existing.id,
                status=existing.status,
                message=f"Job is in {existing.status} state and cannot be retried.",
            )
        return JobRetryResponse(
            id=job.id,
            status=job.status,
            message="Job rescheduled for retry successfully.",
        )

    async def purge_old_jobs(self, retention_days: int | None = None) -> int:
        """Purge finished jobs older than retention period."""
        days = (
            retention_days
            if retention_days is not None
            else settings.jobs_retention_days
        )
        return await self.repo.purge_old_jobs(retention_days=days)

    async def _build_terminal_actions(
        self,
        job: Job,
        event: str,
        event_data: dict[str, Any],
        title: str,
        message: str,
        notification_type: NotificationType,
        notif_data: dict[str, Any],
    ) -> list[Callable[[], None]]:
        """Persist terminal notification and return post-commit SSE callbacks."""
        actions: list[Callable[[], None]] = []
        if job.created_by_id is not None:
            user_id = job.created_by_id
            await NotificationRepository(self.repo.session).create(
                recipient_id=user_id,
                title=title,
                message=message,
                notification_type=notification_type,
                entity_type="job",
                entity_id=job.id,
                data=notif_data,
            )
            actions.append(
                lambda: event_broadcaster.publish_to_user(user_id, event, event_data)
            )

        actions.append(
            lambda: event_broadcaster.publish_to_job(job.id, event, event_data)
        )
        return actions

    async def _on_job_completed(
        self, job: Job, result: dict[str, Any] | None
    ) -> list[Callable[[], None]]:
        """Persist completion notification and prepare post-commit SSE callbacks."""
        return await self._build_terminal_actions(
            job,
            event="job_completed",
            event_data={
                "job_id": str(job.id),
                "status": JobStatus.COMPLETED.value,
                "result": result,
            },
            title=f"Tarea '{job.name}' finalizada",
            message="La tarea en segundo plano ha finalizado exitosamente.",
            notification_type=NotificationType.JOB_COMPLETED,
            notif_data={"job_name": job.name, "result": result or {}},
        )

    async def _on_terminal_failure(
        self, job: Job, error_msg: str
    ) -> list[Callable[[], None]]:
        """Persist failure notification and prepare post-commit SSE callbacks."""
        return await self._build_terminal_actions(
            job,
            event="job_failed",
            event_data={
                "job_id": str(job.id),
                "status": JobStatus.FAILED.value,
                "error": error_msg,
            },
            title=f"Tarea '{job.name}' falló",
            message=error_msg,
            notification_type=NotificationType.JOB_FAILED,
            notif_data={"job_name": job.name, "error": error_msg},
        )

    async def execute_claimed_job(self, job: Job) -> list[Callable[[], None]]:
        """Execute a claimed job with fencing, validation, and error recovery."""
        handler_def = job_registry.get(job.name)
        if not handler_def:
            error_msg = f"No handler registered for job '{job.name}'"
            logger.error(error_msg)
            await self.repo.fail_job(job.id, job.lease_token, error_msg)
            return []

        payload_data: Any = job.payload
        if handler_def.payload_model:
            try:
                payload_data = handler_def.payload_model.model_validate(job.payload)
            except ValidationError as err:
                error_msg = f"Payload validation failed: {err}"
                logger.error(error_msg)
                await self.repo.fail_job(job.id, job.lease_token, error_msg)
                return []

        async def _wrapped_update_progress(
            job_id: uuid.UUID, lease_token: int, progress: int, message: str | None
        ) -> None:
            await self.repo.update_progress_with_fencing(
                job_id, lease_token, progress, message
            )
            progress_data = {
                "job_id": str(job_id),
                "progress": progress,
                "progress_message": message,
                "status": JobStatus.RUNNING.value,
            }
            event_broadcaster.publish_to_job(job_id, "job_progress", progress_data)
            if job.created_by_id:
                event_broadcaster.publish_to_user(
                    job.created_by_id, "job_progress", progress_data
                )

        context: JobContext[Any] = JobContext(
            job_id=job.id,
            name=job.name,
            payload=payload_data,
            entity_type=job.entity_type,
            entity_id=job.entity_id,
            lease_token=job.lease_token,
            session=self.repo.session,
            _update_progress_fn=_wrapped_update_progress,
            _check_cancelled_fn=self.repo.check_cancelled,
        )

        try:
            result = await handler_def.handler(context)
            await self.repo.complete_job(job.id, job.lease_token, result)
            logger.info(f"Job '{job.name}' ({job.id}) completed successfully.")
            return await self._on_job_completed(job, result)
        except JobCancelledError:
            logger.warning(f"Job '{job.name}' ({job.id}) was cancelled by user.")
        except JobLeaseLostError as err:
            logger.error(
                f"Job '{job.name}' ({job.id}) lease was lost to another worker: {err}"
            )
        except Exception as exc:
            error_msg = f"Unhandled error: {exc}"
            logger.exception(f"Job '{job.name}' ({job.id}) execution failed")

            if job.attempts < job.max_retries:
                delay = calculate_backoff_delay(job.attempts)
                reschedule_at = datetime.now(UTC) + delay
                logger.info(
                    f"Rescheduling job '{job.name}' ({job.id}) "
                    f"attempt {job.attempts}/{job.max_retries} for {reschedule_at}"
                )
                with suppress(JobError):
                    await self.repo.fail_job(
                        job.id,
                        job.lease_token,
                        error_msg,
                        reschedule_at=reschedule_at,
                    )
            else:
                logger.error(
                    f"Job '{job.name}' ({job.id}) exceeded max retries "
                    f"({job.max_retries})."
                )
                with suppress(JobError):
                    await self.repo.fail_job(job.id, job.lease_token, error_msg)
                return await self._on_terminal_failure(job, error_msg)

        return []
