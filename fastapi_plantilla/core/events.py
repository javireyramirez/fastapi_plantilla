import asyncio
import json
import uuid
from collections.abc import AsyncGenerator
from contextlib import suppress
from typing import Any

from fastapi_plantilla.core.config import settings

__all__ = [
    "EventBroadcaster",
    "event_broadcaster",
    "format_sse",
]


def format_sse(
    data: dict[str, Any] | str,
    event: str | None = None,
    id: str | None = None,
) -> str:
    """Format data into standard W3C Server-Sent Events text."""
    lines: list[str] = []
    if id is not None:
        lines.append(f"id: {id}")
    if event is not None:
        lines.append(f"event: {event}")

    data_str = json.dumps(data) if isinstance(data, dict) else str(data)
    for line in data_str.splitlines():
        lines.append(f"data: {line}")

    return "\n".join(lines) + "\n\n"


class EventBroadcaster:
    """In-memory event bus managing SSE queues per user and job.

    Enforces anti-DoS bounds (strict FIFO eviction, disconnect via sentinel,
    bounded queue sizes with put_nowait) and sends periodic keepalive pings
    without external dependencies.
    """

    def __init__(self) -> None:
        self._user_queues: dict[uuid.UUID, dict[asyncio.Queue[str | None], None]] = {}
        self._job_queues: dict[uuid.UUID, dict[asyncio.Queue[str | None], None]] = {}

    async def subscribe_user(self, user_id: uuid.UUID) -> AsyncGenerator[str, None]:
        """Subscribe to events directed at a specific user with keepalive."""
        queue: asyncio.Queue[str | None] = asyncio.Queue(
            maxsize=settings.sse_max_queue_size
        )
        queues = self._user_queues.setdefault(user_id, {})

        # Anti-DoS: FIFO eviction closing oldest concurrent listener
        if len(queues) >= settings.sse_max_queues_per_user:
            oldest = next(iter(queues))
            del queues[oldest]
            with suppress(asyncio.QueueFull):
                oldest.put_nowait(None)

        queues[queue] = None

        try:
            while True:
                try:
                    message = await asyncio.wait_for(
                        queue.get(), timeout=settings.sse_ping_interval_seconds
                    )
                    if message is None:
                        break
                    yield message
                except TimeoutError:
                    yield ": ping\n\n"
        finally:
            remaining_user_queues = self._user_queues.get(user_id)
            if remaining_user_queues is not None:
                remaining_user_queues.pop(queue, None)
                if not remaining_user_queues:
                    self._user_queues.pop(user_id, None)

    async def subscribe_job(self, job_id: uuid.UUID) -> AsyncGenerator[str, None]:
        """Subscribe to real-time execution events for a specific job."""
        queue: asyncio.Queue[str | None] = asyncio.Queue(
            maxsize=settings.sse_max_queue_size
        )
        queues = self._job_queues.setdefault(job_id, {})

        # Anti-DoS: FIFO eviction closing oldest concurrent listener
        if len(queues) >= settings.sse_max_queues_per_job:
            oldest = next(iter(queues))
            del queues[oldest]
            with suppress(asyncio.QueueFull):
                oldest.put_nowait(None)

        queues[queue] = None

        try:
            while True:
                try:
                    message = await asyncio.wait_for(
                        queue.get(), timeout=settings.sse_ping_interval_seconds
                    )
                    if message is None:
                        break
                    yield message
                except TimeoutError:
                    yield ": ping\n\n"
        finally:
            remaining_job_queues = self._job_queues.get(job_id)
            if remaining_job_queues is not None:
                remaining_job_queues.pop(queue, None)
                if not remaining_job_queues:
                    self._job_queues.pop(job_id, None)

    def publish_to_user(
        self,
        user_id: uuid.UUID,
        event: str,
        data: dict[str, Any],
        event_id: str | None = None,
    ) -> None:
        """Publish an event to all active SSE connections of a user."""
        queues = self._user_queues.get(user_id)
        if not queues:
            return

        payload = format_sse(data=data, event=event, id=event_id)
        for q in list(queues):
            with suppress(asyncio.QueueFull):
                q.put_nowait(payload)

    def publish_to_job(
        self,
        job_id: uuid.UUID,
        event: str,
        data: dict[str, Any],
        event_id: str | None = None,
    ) -> None:
        """Publish a job event to active listeners of that job."""
        queues = self._job_queues.get(job_id)
        if not queues:
            return

        payload = format_sse(data=data, event=event, id=event_id)
        for q in list(queues):
            with suppress(asyncio.QueueFull):
                q.put_nowait(payload)

    def get_user_queue_count(self, user_id: uuid.UUID) -> int:
        """Return active SSE listener count for user."""
        return len(self._user_queues.get(user_id, {}))

    def get_job_queue_count(self, job_id: uuid.UUID) -> int:
        """Return active SSE listener count for job."""
        return len(self._job_queues.get(job_id, {}))

    def has_job_listener(self, job_id: uuid.UUID) -> bool:
        """Check if job has active SSE listeners."""
        return bool(self._job_queues.get(job_id))

    def clear(self) -> None:
        """Clear all active queues (useful for test isolation)."""
        self._user_queues.clear()
        self._job_queues.clear()


event_broadcaster = EventBroadcaster()
