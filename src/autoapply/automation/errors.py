from __future__ import annotations

from ..store.db import JobStatus


class ApplicationAborted(Exception):
    """Stop the current application and record `status` for the job."""

    def __init__(self, status: JobStatus, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class HitlTimeout(ApplicationAborted):
    """Nobody answered a prompt in time: park the job and stop the whole run."""

    def __init__(self, message: str = "No response to prompt before timeout"):
        super().__init__(JobStatus.PENDING_INPUT, message)
