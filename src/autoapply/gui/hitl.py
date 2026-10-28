"""Human-in-the-loop for the GUI: each pause becomes a prompt dialog in the app window."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Sequence
from contextlib import suppress
from typing import TYPE_CHECKING, Any, Literal

from rich.console import Console

from ..automation.fields import FieldDescriptor
from ..automation.hitl import HitlAction, HitlResponse, HumanInTheLoop
from ..notify import Notifier
from ..store.db import Job
from .bus import EventBus

if TYPE_CHECKING:
    from playwright.async_api import Page


def _job(job: Job) -> dict[str, str]:
    return {"id": job.job_id, "company": job.company, "role": job.role, "url": job.url}


class GuiHumanInTheLoop(HumanInTheLoop):
    def __init__(self, notifier: Notifier, console: Console, *, headless: bool, bus: EventBus):
        super().__init__(notifier, console, headless=headless, timeout=0)
        self.bus = bus
        self.pending: dict[str, dict[str, Any]] = {}
        self._futures: dict[str, asyncio.Future[dict[str, Any]]] = {}

    # ---------------------------------------------------------------- plumbing

    async def _ask(self, page: Page, kind: str, title: str, payload: dict[str, Any]) -> dict[str, Any]:
        pid = uuid.uuid4().hex[:12]
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        prompt = {"id": pid, "kind": kind, "title": title, **payload}
        self.pending[pid] = prompt
        self._futures[pid] = fut
        self.bus.publish({"type": "prompt", "prompt": prompt})
        await self.notifier.alert(f"autoapply: {title}", payload.get("job", {}).get("company", ""))
        if not self.headless:
            with suppress(Exception):
                await page.bring_to_front()
        try:
            return await fut
        finally:
            self.pending.pop(pid, None)
            self._futures.pop(pid, None)
            self.bus.publish({"type": "prompt_closed", "id": pid})

    def answer(self, pid: str, data: dict[str, Any]) -> bool:
        fut = self._futures.get(pid)
        if fut is None or fut.done():
            return False
        fut.set_result(data)
        return True

    # ---------------------------------------------------------------- pauses

    async def pause(self, page: Page, message: str) -> None:
        await self._ask(page, "pause", "Paused", {"message": message})

    async def ask_field(self, page: Page, job: Job, field: FieldDescriptor, reason: str) -> HitlResponse:
        resp = await self._ask(
            page,
            "field",
            "Your input is needed",
            {
                "job": _job(job),
                "question": field.clean_label or field.name or field.id or "(unlabeled field)",
                "field_kind": field.kind,
                "required": field.required,
                "reason": reason,
                "options": field.options,
                "can_use_browser": not self.headless,
            },
        )
        action = resp.get("action", "skip_field")
        if action == "answer":
            return HitlResponse(HitlAction.ANSWER, str(resp.get("value", "")), bool(resp.get("remember")))
        if action == "manual" and not self.headless:
            return HitlResponse(HitlAction.MANUAL)
        if action == "skip_job":
            return HitlResponse(HitlAction.SKIP_JOB)
        return HitlResponse(HitlAction.SKIP_FIELD)

    async def ask_challenge(self, page: Page, job: Job, kind: str) -> bool:
        resp = await self._ask(
            page,
            "challenge",
            f"{kind} needs you",
            {
                "job": _job(job),
                "message": f"A {kind} appeared. Solve it in the browser window, then press Continue.",
            },
        )
        return resp.get("action") == "continue"

    async def confirm_submit(
        self, page: Page, job: Job, filled: Sequence[tuple[str, str]]
    ) -> Literal["submit", "manual", "abort"]:
        resp = await self._ask(
            page,
            "submit",
            "Ready to submit",
            {
                "job": _job(job),
                "filled": [{"field": f, "value": v} for f, v in filled],
                "can_use_browser": not self.headless,
            },
        )
        decision = resp.get("decision")
        if decision == "manual" and not self.headless:
            return "manual"
        return "submit" if decision == "submit" else "abort"

    async def confirm_outcome(self, page: Page, job: Job) -> bool:
        resp = await self._ask(
            page,
            "outcome",
            "Did it go through?",
            {"job": _job(job), "message": "No confirmation page was detected. Was it submitted?"},
        )
        return bool(resp.get("submitted"))

    async def ask_manual(self, page: Page, job: Job, reason: str) -> Literal["applied", "skip", "later"]:
        resp = await self._ask(
            page,
            "manual",
            "Apply manually",
            {"job": _job(job), "message": f"{reason} Complete the application in the browser window."},
        )
        outcome = resp.get("outcome")
        return outcome if outcome in ("applied", "skip", "later") else "later"
