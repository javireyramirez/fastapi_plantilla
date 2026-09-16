"""Domain exceptions for background jobs."""

__all__ = [
    "JobCancelledError",
    "JobError",
    "JobLeaseLostError",
    "JobNotFoundError",
    "JobRegistrationError",
]


class JobError(Exception):
    """Base exception for all job-related errors."""


class JobNotFoundError(JobError):
    """Raised when a requested job does not exist."""


class JobCancelledError(JobError):
    """Raised when a job execution has been cancelled cooperatively."""


class JobLeaseLostError(JobError):
    """Raised when a worker attempts to update a job whose lease was lost."""


class JobRegistrationError(JobError):
    """Raised when a handler is invalid or already registered."""
