"""ATSAdapter: the template every portal adapter follows.

    open → challenge check → prepare_form → fill_form (multi-pass) → challenge check
         → required-field sweep → [dry run stops] → confirm_submit → submit → verify

Subclasses usually only override `open`, `prepare_form`, `verify_submitted` and the
selectors. All field handling, mapping and human pauses live here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from rich.console import Console
from rich.markup import escape

from ...config import AppConfig
from ...profile.answers import save_learned
from ...profile.models import UserProfile
from ...store.db import Job, JobStatus, JobStore
from ..challenges import detect_challenge
from ..errors import ApplicationAborted
from ..field_mapper import DECLINE, FieldMapper, choose_option
from ..fields import FieldDescriptor, collect_fields
from ..hitl import HitlAction, HumanInTheLoop

if TYPE_CHECKING:
    from playwright.async_api import Locator, Page


@dataclass
class ApplyResult:
    status: JobStatus
    message: str = ""


@dataclass
class ApplyContext:
    page: Page
    cfg: AppConfig
    profile: UserProfile
    mapper: FieldMapper
    hitl: HumanInTheLoop
    store: JobStore
    job: Job
    console: Console
    headless: bool
    filled: list[tuple[str, str]] = field(default_factory=list)


class ATSAdapter:
    portal_type: ClassVar[str] = "generic"
    url_patterns: ClassVar[tuple[str, ...]] = ()
    form_selector: ClassVar[str | None] = None
    submit_selector: ClassVar[str] = "button[type=submit], input[type=submit]"
    max_passes: ClassVar[int] = 5

    def __init__(self, ctx: ApplyContext):
        self.ctx = ctx
        self.page = ctx.page
        self.job = ctx.job
        self._seen: set[str] = set()  # fields already processed (avoid re-prompting)
        self._done: set[str] = set()  # fields known to hold a value

    @classmethod
    def handles(cls, url: str) -> bool:
        return any(p in url.lower() for p in cls.url_patterns)

    # ---------------------------------------------------------------- hooks

    async def open(self) -> None:
        await self.page.goto(self.job.url, wait_until="domcontentloaded")
        await self._settle()

    async def prepare_form(self) -> None:
        if self.form_selector:
            await self.page.locator(self.form_selector).first.wait_for(state="attached")

    async def collect_fields(self) -> list[FieldDescriptor]:
        return await collect_fields(self.page, self.form_selector)

    async def submit(self) -> None:
        await self.page.locator(self.submit_selector).first.click()

    async def verify_submitted(self) -> bool:
        return False

    # ---------------------------------------------------------------- pipeline

    async def run(self) -> ApplyResult:
        auto = self.ctx.cfg.automation
        await self.open()
        await self.handle_challenges()
        await self.prepare_form()
        await self.fill_form()
        await self.handle_challenges()
        for f in await self.missing_required():
            await self.ask_human(f, "Required field is still empty after autofill")

        if auto.dry_run:
            self.log("dry_run", "form filled, not submitted")
            if not self.ctx.headless:
                await self.ctx.hitl.pause(self.page, "Dry run — review the form, then press Enter…")
            return ApplyResult(JobStatus.QUEUED, "dry run (not submitted)")

        decision = "submit"
        if auto.require_submit_confirmation:
            decision = await self.ctx.hitl.confirm_submit(self.page, self.job, self.ctx.filled)
        if decision == "abort":
            raise ApplicationAborted(JobStatus.SKIPPED, "Submission declined by user")
        if decision == "submit":
            await self.submit()
            self.log("submit_clicked")
        await self._settle(3000)
        await self.handle_challenges()

        if await self.verify_submitted():
            return ApplyResult(JobStatus.APPLIED, "confirmation page detected")
        if await self.ctx.hitl.confirm_outcome(self.page, self.job):
            return ApplyResult(JobStatus.APPLIED, "confirmed by user")
        return ApplyResult(JobStatus.FAILED, "submission not confirmed")

    async def fill_form(self) -> None:
        """Re-scan after every batch: answers and resume parsing often reveal new fields."""
        for _ in range(self.max_passes):
            fresh = [f for f in await self.collect_fields() if self._key(f) not in self._seen]
            if not fresh:
                return
            files = [f for f in fresh if f.kind == "file"]
            # Upload files first: many ATSs parse the resume and prefill the rest.
            for f in files or fresh:
                self._seen.add(self._key(f))
                await self.process_field(f)
        self.log("max_passes", "form kept changing; stopped re-scanning", "warning")

    async def process_field(self, f: FieldDescriptor) -> None:
        key = self._key(f)
        if f.kind == "file":
            await self.handle_file(f)
            return
        if f.value:
            self._done.add(key)  # prefilled by the ATS (or checked already)
            return
        if f.kind == "checkbox":
            if f.required:
                await self.ask_human(f, "Required checkbox — consents and attestations are yours to tick")
            return

        res = self.ctx.mapper.resolve(f)
        if res is None or res.value is None:
            if f.required or self.ctx.cfg.automation.ask_on_optional_unknown:
                why = (
                    "No rule or saved answer matches this question"
                    if res is None
                    else f"Recognized as '{res.source}', but your profile has no value for it"
                )
                await self.ask_human(f, why)
            return
        if await self.fill(f, res.value):
            self._record(f, res.value)
            return
        if f.required:
            await self.ask_human(f, f"Profile value '{res.value}' doesn't match any option exactly")

    async def handle_file(self, f: FieldDescriptor) -> None:
        if f.value:
            self._done.add(self._key(f))
            return
        if re.search(r"resum|\bcv\b", f.key_text):
            resume = self.ctx.cfg.resolve(self.ctx.cfg.paths.resume_pdf)
            if not resume.is_file():
                raise ApplicationAborted(JobStatus.FAILED, f"Resume not found at {resume}")
            await self.page.locator(f.selector).set_input_files(str(resume))
            self._record(f, resume.name)
            self.log("resume_uploaded", resume.name)
            await self._settle(2500)
            return
        if f.required:
            await self.ask_human(f, "A file upload other than your resume is required")
        else:
            self.log("skip_optional_file", f.clean_label, "debug")

    # ---------------------------------------------------------------- filling

    async def fill(self, f: FieldDescriptor, value: str) -> bool:
        from playwright.async_api import Error as PlaywrightError

        loc = self.page.locator(f.selector)
        try:
            match f.kind:
                case "file":
                    path = Path(value.strip().strip('"')).expanduser()
                    if not path.is_file():
                        return False
                    await loc.set_input_files(str(path))
                case "checkbox":
                    answer = value.strip().lower()
                    if answer in ("y", "yes", "true", "1"):
                        await loc.check()
                    elif answer not in ("n", "no", "false", "0"):
                        return False
                case "select" | "radio":
                    opt = choose_option(f.options, value)
                    if opt is None:
                        return False
                    i = f.options.index(opt)
                    if f.kind == "select":
                        await loc.select_option(value=f.option_values[i])
                    else:
                        await self.page.locator(f'[data-autoapply-radio="{f.idx}-{i}"]').check()
                case "combobox":
                    return await self.fill_combobox(loc, value)
                case _:
                    await loc.fill(value)
            return True
        except PlaywrightError as exc:
            self.log("fill_error", f"{f.clean_label[:50]}: {exc}".splitlines()[0], "warning")
            return False

    async def fill_combobox(self, loc: Locator, value: str) -> bool:
        """React-select style dropdowns: type to filter, then click exactly one matching option."""
        from playwright.async_api import TimeoutError as PlaywrightTimeout

        typed = "" if value.lower() in (DECLINE, "yes", "no") else value[:40]
        await loc.click()
        if typed:
            await loc.fill(typed)
        options = self.page.get_by_role("option")
        try:
            await options.first.wait_for(state="visible", timeout=5000)
        except PlaywrightTimeout:
            await loc.press("Escape")
            return False
        texts = [t.strip() for t in await options.all_inner_texts()]
        choice = choose_option(texts, value)
        if choice is None:
            await loc.press("Escape")
            return False
        await options.nth(texts.index(choice)).click()
        return True

    # ---------------------------------------------------------------- human pauses

    async def ask_human(self, f: FieldDescriptor, reason: str) -> None:
        for _ in range(3):
            resp = await self.ctx.hitl.ask_field(self.page, self.job, f, reason)
            self.log("hitl", f"{f.clean_label[:60]!r} → {resp.action.value}")
            if resp.action is HitlAction.SKIP_JOB:
                raise ApplicationAborted(JobStatus.SKIPPED, "Skipped by user")
            if resp.action is HitlAction.MANUAL:
                self._done.add(self._key(f))
                self.ctx.filled.append((f.clean_label, "(filled by you in browser)"))
                return
            if resp.action is HitlAction.SKIP_FIELD:
                return
            assert resp.value is not None
            if await self.fill(f, resp.value):
                self._record(f, resp.value)
                if resp.remember:
                    path = self.ctx.cfg.resolve(self.ctx.cfg.paths.answers_learned)
                    self.ctx.profile.answers.insert(0, save_learned(path, f.label, resp.value))
                return
            reason = f"'{resp.value}' couldn't be applied (no matching option or invalid path)"
        raise ApplicationAborted(JobStatus.PENDING_INPUT, f"Could not fill {f.clean_label!r}")

    async def handle_challenges(self) -> None:
        kind = await detect_challenge(self.page)
        if not kind:
            return
        self.log("challenge", kind, "warning")
        if self.ctx.headless:
            raise ApplicationAborted(
                JobStatus.PENDING_INPUT, f"{kind} encountered headless — run `autoapply resume`"
            )
        if not await self.ctx.hitl.ask_challenge(self.page, self.job, kind):
            raise ApplicationAborted(JobStatus.SKIPPED, f"Skipped at {kind}")
        await self._settle()

    async def missing_required(self) -> list[FieldDescriptor]:
        return [
            f
            for f in await self.collect_fields()
            if f.required and not f.value and self._key(f) not in self._done
        ]

    # ---------------------------------------------------------------- utils

    @staticmethod
    def _key(f: FieldDescriptor) -> str:
        return f"{f.name}|{f.id}|{f.clean_label}|{f.input_type}"

    def _record(self, f: FieldDescriptor, value: str) -> None:
        self._done.add(self._key(f))
        self.ctx.filled.append((f.clean_label or f.name, value))

    async def _settle(self, extra_ms: int = 0) -> None:
        from playwright.async_api import TimeoutError as PlaywrightTimeout

        try:
            await self.page.wait_for_load_state("networkidle", timeout=8000)
        except PlaywrightTimeout:
            pass
        if extra_ms:
            await self.page.wait_for_timeout(extra_ms)

    def log(self, event: str, detail: str = "", level: str = "info") -> None:
        self.ctx.store.log(self.job.job_id, event, detail, level)
        if level != "debug":
            color = {"warning": "yellow", "error": "red"}.get(level, "dim")
            self.ctx.console.print(f"  [{color}]{event}[/] {escape(detail)}")
