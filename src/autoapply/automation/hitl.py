"""Human-in-the-loop: every pause point routes through here.

Each pause: sound + desktop notification → browser raised (headed mode) → a terminal
prompt describing exactly what is needed → execution resumes with your answer.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Literal

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from ..notify import Notifier
from ..store.db import Job
from .errors import HitlTimeout
from .fields import FieldDescriptor

if TYPE_CHECKING:
    from playwright.async_api import Page


class HitlAction(Enum):
    ANSWER = "answer"
    MANUAL = "manual"  # you filled it directly in the browser
    SKIP_FIELD = "skip_field"
    SKIP_JOB = "skip_job"


@dataclass
class HitlResponse:
    action: HitlAction
    value: str | None = None
    remember: bool = False


_COMMANDS = (":b", ":s", ":x")


class HumanInTheLoop:
    def __init__(self, notifier: Notifier, console: Console, *, headless: bool, timeout: int = 0):
        self.notifier = notifier
        self.console = console
        self.headless = headless
        self.timeout = timeout

    # ---------------------------------------------------------------- plumbing

    async def _input(self, prompt: str = "› ") -> str:
        coro = asyncio.to_thread(self.console.input, escape(prompt))
        if self.timeout > 0:
            try:
                return await asyncio.wait_for(coro, self.timeout)
            except TimeoutError as exc:
                raise HitlTimeout() from exc
        return await coro

    async def _attention(self, page: Page, title: str, message: str) -> None:
        await self.notifier.alert(title, message)
        if not self.headless:
            with suppress(Exception):
                await page.bring_to_front()

    async def pause(self, page: Page, message: str) -> None:
        await self._attention(page, "autoapply paused", message)
        await self._input(f"{message} ")

    # ---------------------------------------------------------------- field input

    async def ask_field(self, page: Page, job: Job, field: FieldDescriptor, reason: str) -> HitlResponse:
        label = field.clean_label or field.name or field.id or "(unlabeled field)"
        await self._attention(page, "autoapply needs your input", f"{job.company}: {label[:90]}")

        body = Table.grid(padding=(0, 1))
        body.add_row("[bold]Question[/]", escape(label))
        body.add_row("[bold]Type[/]", f"{field.kind}{'  (required)' if field.required else ''}")
        body.add_row("[bold]Why[/]", f"[yellow]{escape(reason)}[/]")
        for i, opt in enumerate(field.options, 1):
            body.add_row(f"[cyan]{i:>3}[/]", escape(opt))
        self.console.print(Panel(body, title=escape(f"{job.company} · {job.role}"), border_style="magenta"))

        hints = []
        if field.options:
            hints.append("a number to pick an option")
        hints.append({"checkbox": "y to tick it", "file": "a file path"}.get(field.kind, "your answer"))
        if not self.headless:
            hints.append(":b fill it in the browser yourself")
        hints += [":s skip this field", ":x skip this job"]
        self.console.print("[dim]Type " + " · ".join(hints) + "[/]")

        while True:
            raw = await (self._multiline() if field.kind == "textarea" else self._input())
            raw = raw.strip()
            if raw == ":x":
                return HitlResponse(HitlAction.SKIP_JOB)
            if raw == ":b":
                if self.headless:
                    self.console.print("[red]Running headless — there is no browser to use.[/]")
                    continue
                await self._input("Fill it in the browser, then press Enter… ")
                return HitlResponse(HitlAction.MANUAL)
            if raw == ":s":
                if field.required:
                    self.console.print("[yellow]Skipping a required field; submission may fail.[/]")
                return HitlResponse(HitlAction.SKIP_FIELD)
            if not raw:
                continue
            value = raw
            if field.options and raw.isdigit() and 1 <= int(raw) <= len(field.options):
                value = field.options[int(raw) - 1]
            remember = False
            if field.kind not in ("file", "checkbox"):
                ans = await self._input("Save this answer for identical questions later? [y/N] ")
                remember = ans.strip().lower().startswith("y")
            return HitlResponse(HitlAction.ANSWER, value, remember)

    async def _multiline(self) -> str:
        self.console.print("[dim]Multi-line answer: finish with a line containing only '.'[/]")
        lines: list[str] = []
        while True:
            line = await self._input("")
            if not lines and line.strip() in _COMMANDS:
                return line
            if line.strip() == ".":
                return "\n".join(lines)
            lines.append(line)

    # ---------------------------------------------------------------- checkpoints

    async def ask_challenge(self, page: Page, job: Job, kind: str) -> bool:
        await self._attention(page, f"{kind} needs you", f"{job.company} · {job.role}")
        self.console.print(
            Panel(
                f"A [bold]{kind}[/] appeared. Solve it in the browser window.\n"
                "Press Enter when done, or type :x to skip this job.",
                title="Verification required",
                border_style="red",
            )
        )
        return (await self._input()).strip() != ":x"

    async def confirm_submit(
        self, page: Page, job: Job, filled: Sequence[tuple[str, str]]
    ) -> Literal["submit", "manual", "abort"]:
        await self._attention(page, "Ready to submit — review it", f"{job.company} · {job.role}")
        table = Table("Field", "Value filled", title=escape(f"{job.company} · {job.role}"))
        for label, value in filled:
            table.add_row(escape(label[:60]), escape(value[:70].replace("\n", " ⏎ ")))
        self.console.print(table)
        if self.headless:
            options = "[s] submit · [a] abort"
        else:
            options = "Review the browser. [s] submit · [m] I'll click Submit myself · [a] abort"
        while True:
            ans = (await self._input(f"{options} › ")).strip().lower()
            if ans == "s":
                return "submit"
            if ans == "a":
                return "abort"
            if ans == "m" and not self.headless:
                await self._input("Click Submit in the browser, then press Enter… ")
                return "manual"

    async def confirm_outcome(self, page: Page, job: Job) -> bool:
        await self._attention(page, "Did it go through?", f"{job.company} · {job.role}")
        ans = await self._input("Couldn't detect a confirmation page. Was it submitted? [y/N] ")
        return ans.strip().lower().startswith("y")

    async def ask_manual(self, page: Page, job: Job, reason: str) -> Literal["applied", "skip", "later"]:
        await self._attention(page, "Manual application", f"{job.company} · {job.role}")
        self.console.print(
            Panel(
                escape(reason) + "\nComplete the application in the browser window.",
                title=escape(f"{job.company} · {job.role}"),
                border_style="yellow",
            )
        )
        while True:
            ans = (await self._input("[a] applied · [l] do it later · [s] skip job › ")).strip()
            mapping = {"a": "applied", "l": "later", "s": "skip"}
            if ans.lower() in mapping:
                return mapping[ans.lower()]  # type: ignore[return-value]
