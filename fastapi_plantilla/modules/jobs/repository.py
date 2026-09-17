import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import (
    and_,
    delete,
    func,
    or_,
    select,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi_plantilla.core.crud.repository import build_scope_filter
from fastapi_plantilla.core.crud.schema import ScopeContext, SortOrder
from fastapi_plantilla.core.crud.service_base import normalize_filter_date
from fastapi_plantilla.modules.common.resolvers import (
    CODE_TO_ENTITY,
    ENTITY_TO_CODE,
    normalize_entity_types,
)
from fastapi_plantilla.modules.jobs.exceptions import (
    JobCancelledError,
    JobLeaseLostError,
)
from fastapi_plantilla.modules.jobs.models import Job, JobStatus

__all__ = ["JobRepository"]


class JobRepository:
    """Persistence operations and atomic claims for background jobs."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, job_id: uuid.UUID) -> Job | None:
        """Fetch job by ID."""
        return await self.session.get(Job, job_id)

    async def get_by_idempotency_key(self, idempotency_key: str) -> Job | None:
        """Fetch active job by idempotency key."""
        stmt = (
            select(Job)
            .where(
                Job.idempotency_key == idempotency_key,
                Job.status.in_([JobStatus.PENDING, JobStatus.RUNNING]),
            )
            .limit(1)
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def create(
        self,
        name: str,
        payload: dict[str, Any],
        entity_type: str | None = None,
        entity_id: uuid.UUID | None = None,
        max_retries: int = 3,
        lease_duration_seconds: int = 300,
        scheduled_at: datetime | None = None,
        idempotency_key: str | None = None,
        created_by_id: uuid.UUID | None = None,
    ) -> Job:
        """Persist a new job in PENDING state."""
        now = datetime.now(UTC)
        job = Job(
            name=name,
            status=JobStatus.PENDING,
            payload=payload,
            entity_type=entity_type,
            entity_id=entity_id,
            max_retries=max_retries,
            lease_duration_seconds=lease_duration_seconds,
            scheduled_at=scheduled_at or now,
            idempotency_key=idempotency_key,
            created_by_id=created_by_id,
        )
        self.session.add(job)
        await self.session.flush()
        return job

    async def claim_next_job(self) -> Job | None:
        """Atomically claim the next eligible job using SKIP LOCKED.

        Advances lease_token. Covers:
        1) PENDING jobs whose scheduled_at <= now()
        2) RUNNING jobs whose lease has expired and attempts < max_retries
        """
        now = datetime.now(UTC)
        bind = self.session.bind
        dialect_name = bind.dialect.name if bind else "postgresql"

        if dialect_name == "postgresql":
            # PostgreSQL single-statement atomic claim with FOR UPDATE SKIP LOCKED
            candidate_subquery = (
                select(Job.id)
                .where(
                    or_(
                        and_(
                            Job.status == JobStatus.PENDING,
                            Job.scheduled_at <= now,
                        ),
                        and_(
                            Job.status == JobStatus.RUNNING,
                            Job.lease_expires_at.is_not(None),
                            Job.lease_expires_at <= now,
                            Job.attempts < Job.max_retries,
                        ),
                    )
                )
                .order_by(Job.scheduled_at.asc(), Job.created_at.asc())
                .limit(1)
                .with_for_update(skip_locked=True)
                .scalar_subquery()
            )

            stmt = (
                update(Job)
                .where(Job.id == candidate_subquery)
                .values(
                    status=JobStatus.RUNNING,
                    started_at=now,
                    attempts=Job.attempts + 1,
                    lease_token=Job.lease_token + 1,
                    lease_expires_at=now
                    + func.make_interval(0, 0, 0, 0, 0, 0, Job.lease_duration_seconds),
                )
                .returning(Job)
            )
            res = await self.session.execute(stmt)
            return res.scalar_one_or_none()

        # SQLite fallback (strictly for unit tests, non-concurrent)
        candidate_stmt = (
            select(Job)
            .where(
                or_(
                    and_(
                        Job.status == JobStatus.PENDING,
                        Job.scheduled_at <= now,
                    ),
                    and_(
                        Job.status == JobStatus.RUNNING,
                        Job.lease_expires_at.is_not(None),
                        Job.lease_expires_at <= now,
                        Job.attempts < Job.max_retries,
                    ),
                )
            )
            .order_by(Job.scheduled_at.asc(), Job.created_at.asc())
            .limit(1)
        )
        res_cand = await self.session.execute(candidate_stmt)
        candidate = res_cand.scalar_one_or_none()
        if not candidate:
            return None

        # Lock and increment token
        target_token = candidate.lease_token
        new_token = target_token + 1
        new_lease_expiry = now + timedelta(seconds=candidate.lease_duration_seconds)

        update_stmt = (
            update(Job)
            .where(Job.id == candidate.id, Job.lease_token == target_token)
            .values(
                status=JobStatus.RUNNING,
                started_at=now,
                attempts=candidate.attempts + 1,
                lease_token=new_token,
                lease_expires_at=new_lease_expiry,
            )
            .returning(Job)
        )
        res_upd = await self.session.execute(update_stmt)
        return res_upd.scalar_one_or_none()

    async def update_progress_with_fencing(
        self,
        job_id: uuid.UUID,
        lease_token: int,
        progress: int,
        message: str | None,
    ) -> None:
        """Update progress if lease_token matches and job is not cancelled."""
        job = await self.get_by_id(job_id)
        if not job:
            raise JobLeaseLostError(f"Job {job_id} not found.")

        if job.status == JobStatus.CANCELLED:
            raise JobCancelledError(f"Job {job_id} was cancelled.")

        if job.lease_token != lease_token:
            raise JobLeaseLostError(
                f"Job {job_id} lease token mismatch "
                f"({job.lease_token} != {lease_token})."
            )

        now = datetime.now(UTC)
        new_lease_expiry = now + timedelta(seconds=job.lease_duration_seconds)

        stmt = (
            update(Job)
            .where(
                Job.id == job_id,
                Job.lease_token == lease_token,
                Job.status == JobStatus.RUNNING,
            )
            .values(
                progress=progress,
                progress_message=message,
                lease_expires_at=new_lease_expiry,
            )
        )
        res = await self.session.execute(stmt)
        if int(getattr(res, "rowcount", 0)) == 0:
            raise JobLeaseLostError(f"Job {job_id} lease lost during progress update.")

    async def check_cancelled(self, job_id: uuid.UUID, lease_token: int) -> bool:
        """Check if job is marked CANCELLED or lease token was taken."""
        job = await self.get_by_id(job_id)
        if not job:
            return True
        if job.status == JobStatus.CANCELLED:
            return True
        return job.lease_token != lease_token

    async def complete_job(
        self,
        job_id: uuid.UUID,
        lease_token: int,
        result: dict[str, Any] | None,
    ) -> None:
        """Mark job as COMPLETED under matching lease token."""
        now = datetime.now(UTC)
        stmt = (
            update(Job)
            .where(
                Job.id == job_id,
                Job.lease_token == lease_token,
                Job.status == JobStatus.RUNNING,
            )
            .values(
                status=JobStatus.COMPLETED,
                progress=100,
                result=result,
                completed_at=now,
                error=None,
            )
        )
        res = await self.session.execute(stmt)
        if int(getattr(res, "rowcount", 0)) == 0:
            raise JobLeaseLostError(f"Job {job_id} lease lost during completion.")

    async def fail_job(
        self,
        job_id: uuid.UUID,
        lease_token: int,
        error_msg: str,
        reschedule_at: datetime | None = None,
    ) -> None:
        """Mark job as PENDING for retry or FAILED if max retries exceeded."""
        now = datetime.now(UTC)
        new_status = JobStatus.PENDING if reschedule_at else JobStatus.FAILED

        values: dict[str, Any] = {
            "status": new_status,
            "error": error_msg,
        }
        if reschedule_at:
            values["scheduled_at"] = reschedule_at
        else:
            values["completed_at"] = now

        stmt = (
            update(Job)
            .where(
                Job.id == job_id,
                Job.lease_token == lease_token,
                Job.status == JobStatus.RUNNING,
            )
            .values(**values)
        )
        res = await self.session.execute(stmt)
        if int(getattr(res, "rowcount", 0)) == 0:
            raise JobLeaseLostError(f"Job {job_id} lease lost during failure update.")

    async def cancel_job(self, job_id: uuid.UUID) -> Job | None:
        """Transition PENDING or RUNNING job to CANCELLED."""
        now = datetime.now(UTC)
        stmt = (
            update(Job)
            .where(
                Job.id == job_id,
                Job.status.in_([JobStatus.PENDING, JobStatus.RUNNING]),
            )
            .values(
                status=JobStatus.CANCELLED,
                completed_at=now,
                error="Cancelled by user or administrator",
            )
            .returning(Job)
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def retry_failed_job(self, job_id: uuid.UUID) -> Job | None:
        """Reschedule a FAILED or CANCELLED job back to PENDING."""
        now = datetime.now(UTC)
        stmt = (
            update(Job)
            .where(
                Job.id == job_id,
                Job.status.in_([JobStatus.FAILED, JobStatus.CANCELLED]),
            )
            .values(
                status=JobStatus.PENDING,
                progress=0,
                progress_message=None,
                error=None,
                attempts=0,
                scheduled_at=now,
                started_at=None,
                completed_at=None,
            )
            .returning(Job)
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def reap_zombies(self) -> int:
        """Reap RUNNING jobs whose lease has expired and attempts >= max_retries."""
        now = datetime.now(UTC)
        stmt = (
            update(Job)
            .where(
                Job.status == JobStatus.RUNNING,
                Job.lease_expires_at.is_not(None),
                Job.lease_expires_at <= now,
                Job.attempts >= Job.max_retries,
            )
            .values(
                status=JobStatus.FAILED,
                error=(
                    "Execution lease expired and max retries exceeded "
                    "(zombie job reaped)"
                ),
                completed_at=now,
            )
        )
        res = await self.session.execute(stmt)
        return int(getattr(res, "rowcount", 0))

    async def purge_old_jobs(self, retention_days: int = 30) -> int:
        """Delete finished jobs older than retention period."""
        threshold = datetime.now(UTC) - timedelta(days=retention_days)
        stmt = delete(Job).where(
            Job.status.in_(
                [JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED]
            ),
            Job.updated_at <= threshold,
        )
        res = await self.session.execute(stmt)
        return int(getattr(res, "rowcount", 0))

    async def list_jobs(
        self,
        status: list[JobStatus] | JobStatus | None = None,
        name: str | None = None,
        entity_type: list[str] | str | None = None,
        entity_id: uuid.UUID | None = None,
        search: str | None = None,
        created_at_from: datetime | None = None,
        created_at_to: datetime | None = None,
        sort_by: str = "created_at",
        sort_order: SortOrder = SortOrder.DESC,
        page: int = 1,
        limit: int = 20,
        scope: ScopeContext | None = None,
    ) -> tuple[list[Job], int]:
        """Paginated list of jobs with query filters and RBAC scope."""
        query = select(Job)
        count_query = select(func.count(Job.id))

        filters = _build_job_filters(
            status=status,
            name=name,
            entity_type=entity_type,
            entity_id=entity_id,
            search=search,
            created_at_from=created_at_from,
            created_at_to=created_at_to,
            scope=scope,
        )

        if filters:
            query = query.where(*filters)
            count_query = count_query.where(*filters)

        total_res = await self.session.execute(count_query)
        total = total_res.scalar_one()

        allowed_sort_fields = {
            "created_at": Job.created_at,
            "scheduled_at": Job.scheduled_at,
            "started_at": Job.started_at,
            "completed_at": Job.completed_at,
            "name": Job.name,
            "status": Job.status,
            "progress": Job.progress,
            "attempts": Job.attempts,
        }
        sort_col = allowed_sort_fields.get(sort_by, Job.created_at)
        order_expr = sort_col.desc() if sort_order == SortOrder.DESC else sort_col.asc()

        offset = (page - 1) * limit
        query = query.order_by(order_expr).offset(offset).limit(limit)
        items_res = await self.session.execute(query)
        items = list(items_res.scalars().all())

        return items, total


def _build_status_filter(
    status: list[JobStatus] | JobStatus | None,
) -> Any | None:
    """Build status equality or IN clause for jobs."""
    if not status:
        return None
    status_list = [status] if isinstance(status, (str, JobStatus)) else list(status)
    if len(status_list) == 1:
        return Job.status == status_list[0]
    return Job.status.in_(status_list) if status_list else None


def _build_name_filter(name: str | None, search: str | None) -> Any | None:
    """Build partial name/search filter using icontains."""
    if name and search and name.strip() != search.strip():
        return or_(
            Job.name.icontains(name.strip(), autoescape=True),
            Job.name.icontains(search.strip(), autoescape=True),
        )
    search_term = (name or search or "").strip()
    return Job.name.icontains(search_term, autoescape=True) if search_term else None


def _build_entity_filter(
    entity_type: list[str] | str | None,
) -> Any | None:
    """Build entity/module filter with canonical resolution."""
    if not entity_type:
        return None
    types_list = [entity_type] if isinstance(entity_type, str) else list(entity_type)
    all_targets: set[str] = set()
    for t in types_list:
        norm_list = normalize_entity_types(t)
        for item in norm_list if norm_list else [t.strip().lower()]:
            all_targets.add(item)
            if item in CODE_TO_ENTITY:
                all_targets.add(CODE_TO_ENTITY[item])
            if item in ENTITY_TO_CODE:
                all_targets.add(ENTITY_TO_CODE[item])
    if len(all_targets) == 1:
        return Job.entity_type == next(iter(all_targets))
    return Job.entity_type.in_(all_targets) if all_targets else None


def _build_date_filters(
    created_at_from: datetime | None,
    created_at_to: datetime | None,
) -> list[Any]:
    """Build date range filters with timezone normalization."""
    filters: list[Any] = []
    if created_at_from:
        filters.append(
            Job.created_at
            >= normalize_filter_date(created_at_from, is_end_of_day=False)
        )
    if created_at_to:
        filters.append(
            Job.created_at <= normalize_filter_date(created_at_to, is_end_of_day=True)
        )
    return filters


def _build_job_filters(
    status: list[JobStatus] | JobStatus | None = None,
    name: str | None = None,
    entity_type: list[str] | str | None = None,
    entity_id: uuid.UUID | None = None,
    search: str | None = None,
    created_at_from: datetime | None = None,
    created_at_to: datetime | None = None,
    scope: ScopeContext | None = None,
) -> list[Any]:
    filters: list[Any] = []

    scope_clause = build_scope_filter(Job.created_by_id, scope)
    if scope_clause is not None:
        filters.append(scope_clause)

    if (st_filter := _build_status_filter(status)) is not None:
        filters.append(st_filter)

    if (name_filter := _build_name_filter(name, search)) is not None:
        filters.append(name_filter)

    if (ent_filter := _build_entity_filter(entity_type)) is not None:
        filters.append(ent_filter)

    if entity_id:
        filters.append(Job.entity_id == entity_id)

    filters.extend(_build_date_filters(created_at_from, created_at_to))

    return filters
