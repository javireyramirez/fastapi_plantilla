import asyncio
from contextlib import suppress
from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from fastapi_plantilla.core.config import settings
from fastapi_plantilla.modules.jobs.repository import JobRepository
from fastapi_plantilla.modules.jobs.service import JobService

__all__ = ["BackgroundJobWorker"]


class BackgroundJobWorker:
    """Async background worker with semaphore concurrency, reaper, and drain."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        max_concurrency: int | None = None,
        poll_interval: float | None = None,
        shutdown_timeout: float | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.max_concurrency = max_concurrency or settings.jobs_max_concurrency
        self.poll_interval = poll_interval or settings.jobs_poll_interval_seconds
        self.shutdown_timeout = (
            shutdown_timeout or settings.jobs_shutdown_timeout_seconds
        )
        self.semaphore = asyncio.Semaphore(self.max_concurrency)
        self.in_flight_tasks: set[asyncio.Task[None]] = set()
        self._running = False
        self._main_loop_task: asyncio.Task[None] | None = None

    async def _process_job(self, job_id: Any) -> None:
        """Process a claimed job under the concurrency semaphore."""
        async with (
            self.semaphore,
            self.session_factory() as session,
            session.begin(),
        ):
            repo = JobRepository(session)
            service = JobService(repo)
            job = await repo.get_by_id(job_id)
            if not job:
                return
            await service.execute_claimed_job(job)

    def _on_task_finished(self, task: asyncio.Task[None]) -> None:
        """Remove finished task from tracking set."""
        self.in_flight_tasks.discard(task)

    async def _poll_step(self) -> bool:
        """Attempt to claim and dispatch one job. Returns True if a job was claimed."""
        async with self.session_factory() as session, session.begin():
            repo = JobRepository(session)
            # 1. Periodic reaper check on each poll
            reaped = await repo.reap_zombies()
            if reaped > 0:
                logger.warning(f"Reaped {reaped} zombie jobs with expired leases.")

            # 2. Claim next available job
            claimed_job = await repo.claim_next_job()
            if not claimed_job:
                return False

            job_id = claimed_job.id

        # Dispatch async task outside the claim transaction
        task = asyncio.create_task(self._process_job(job_id))
        self.in_flight_tasks.add(task)
        task.add_done_callback(self._on_task_finished)
        return True

    async def run(self) -> None:
        """Main worker polling loop."""
        self._running = True
        logger.info(
            f"BackgroundJobWorker started (concurrency={self.max_concurrency}, "
            f"poll={self.poll_interval}s)"
        )

        while self._running:
            try:
                # If semaphore has available capacity, attempt claim
                if not self.semaphore.locked():
                    claimed = await self._poll_step()
                    if claimed:
                        # Immediately try claiming again without sleeping
                        continue

                await asyncio.sleep(self.poll_interval)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"Error in background job worker polling loop: {exc}")
                await asyncio.sleep(self.poll_interval)

        self._running = False

    def start(self) -> asyncio.Task[None]:
        """Start worker in an asyncio task."""
        if not self._main_loop_task or self._main_loop_task.done():
            self._main_loop_task = asyncio.create_task(self.run())
        return self._main_loop_task

    async def stop(self) -> None:
        """Gracefully stop worker and drain in-flight tasks up to shutdown_timeout."""
        self._running = False
        if self._main_loop_task and not self._main_loop_task.done():
            self._main_loop_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._main_loop_task

        if self.in_flight_tasks:
            logger.info(
                f"Draining {len(self.in_flight_tasks)} background job tasks "
                f"(timeout={self.shutdown_timeout}s)..."
            )
            _, pending = await asyncio.wait(
                self.in_flight_tasks, timeout=self.shutdown_timeout
            )
            for task in pending:
                logger.warning("Cancelling uncompleted in-flight job task on shutdown.")
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
