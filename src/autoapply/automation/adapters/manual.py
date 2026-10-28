"""Fallback for portals without an adapter yet (Workday, Ashby, Taleo, iCIMS…).

Opens the posting so you can apply by hand, then records the outcome in the tracker.
Turning one of these into a real adapter = subclass ATSAdapter (see docs/ADAPTERS.md).
"""

from __future__ import annotations

from ...store.db import JobStatus
from ..errors import ApplicationAborted
from .base import ApplyResult, ATSAdapter


class ManualAdapter(ATSAdapter):
    portal_type = "manual"

    async def run(self) -> ApplyResult:
        if self.ctx.headless:
            raise ApplicationAborted(
                JobStatus.PENDING_INPUT,
                f"No adapter for '{self.job.portal_type}' — run `autoapply resume` (headed)",
            )
        await self.open()
        outcome = await self.ctx.hitl.ask_manual(
            self.page, self.job, f"No automation for '{self.job.portal_type}' portals yet."
        )
        return {
            "applied": ApplyResult(JobStatus.APPLIED, "applied manually"),
            "later": ApplyResult(JobStatus.PENDING_INPUT, "deferred by user"),
            "skip": ApplyResult(JobStatus.SKIPPED, "skipped by user"),
        }[outcome]
