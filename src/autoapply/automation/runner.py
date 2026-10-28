"""Runs queued jobs through their adapters with rate limiting and failure capture."""

from __future__ import annotations

import asyncio
import contextlib
import random
from collections import Counter
from collections.abc import Iterable

from rich.console import Console
from rich.markup import escape

from ..config import AppConfig
from ..notify import Notifier
from ..profile.models import UserProfile
from ..store.db import Job, JobStatus, JobStore
from ..utils import now_iso, slugify
from .adapters import ApplyContext, ApplyResult, adapter_for
from .browser import launch_browser
from .errors import ApplicationAborted, HitlTimeout
from .field_mapper import FieldMapper
from .hitl import HumanInTheLoop


class Runner:
    def __init__(
        self,
        cfg: AppConfig,
        profile: UserProfile,
        store: JobStore,
        console: Console,
        *,
        headless: bool,
        limit: int | None = None,
        statuses: Iterable[JobStatus] = (JobStatus.QUEUED,),
        hitl: HumanInTheLoop | None = None,
    ):
        self.cfg = cfg
        self.profile = profile
        self.store = store
        self.console = console
        self.headless = headless
        self.limit = limit or cfg.rate_limits.max_applications_per_run
        self.statuses = tuple(statuses)
        self.hitl = hitl  # the GUI passes its own; the CLI uses terminal prompts

    async def run(self) -> Counter[str]:
        summary: Counter[str] = Counter()
        jobs = self.store.list(self.statuses)[: self.limit]
        if not jobs:
            self.console.print("Nothing to do — queue some postings first.")
            return summary

        hitl = self.hitl or HumanInTheLoop(
            Notifier(self.cfg.notifications),
            self.console,
            headless=self.headless,
            timeout=self.cfg.automation.hitl_timeout_seconds,
        )
        mapper = FieldMapper(self.profile)
        rl = self.cfg.rate_limits
        mode = "headless" if self.headless else "headed"
        self.console.rule(f"autoapply · {len(jobs)} job(s) · {mode}")

        async with launch_browser(self.cfg, headless=self.headless) as browser:
            attempted = 0
            for job in jobs:
                if self.store.count_applied_today(job.company) >= rl.max_per_company_per_day:
                    self.console.print(f"[dim]Skipping {escape(job.company)}: daily cap reached[/]")
                    continue
                if attempted:
                    delay = random.uniform(rl.min_delay_seconds, rl.max_delay_seconds)
                    self.console.print(f"[dim]Pausing {delay:.0f}s between applications…[/]")
                    await asyncio.sleep(delay)
                attempted += 1

                self.console.print(
                    f"\n[bold]{escape(job.company)}[/] · {escape(job.role)} [dim]({job.portal_type})[/]"
                )
                self.store.set_status(job.job_id, JobStatus.IN_PROGRESS)
                page = await browser.new_page()
                stop = False
                try:
                    adapter_cls = adapter_for(job.portal_type, job.url)
                    ctx = ApplyContext(
                        page,
                        self.cfg,
                        self.profile,
                        mapper,
                        hitl,
                        self.store,
                        job,
                        self.console,
                        self.headless,
                    )
                    result = await adapter_cls(ctx).run()
                except asyncio.CancelledError:
                    # Run stopped (GUI "Stop" or Ctrl+C): put the job back in the queue.
                    self.store.set_status(job.job_id, JobStatus.QUEUED, "run stopped")
                    raise
                except HitlTimeout as exc:
                    result, stop = ApplyResult(exc.status, exc.message), True
                except ApplicationAborted as exc:
                    result = ApplyResult(exc.status, exc.message)
                except Exception as exc:  # noqa: BLE001 - one bad page must not kill the run
                    await self._capture(page, job)
                    result = ApplyResult(JobStatus.FAILED, f"{type(exc).__name__}: {exc}")
                finally:
                    with contextlib.suppress(Exception):
                        await page.close()

                self.store.set_status(job.job_id, result.status, result.message)
                summary[str(result.status)] += 1
                color = {"applied": "green", "failed": "red"}.get(str(result.status), "yellow")
                self.console.print(f"  → [{color}]{result.status}[/] {escape(result.message)}")
                if stop:
                    self.console.print("[yellow]Prompt timed out — stopping the run.[/]")
                    break
        return summary

    async def _capture(self, page, job: Job) -> None:
        if not self.cfg.automation.screenshot_on_failure:
            return
        try:
            logs = self.cfg.resolve(self.cfg.paths.logs_dir)
            logs.mkdir(parents=True, exist_ok=True)
            stamp = now_iso().replace(":", "")  # ':' is illegal in Windows filenames
            path = logs / f"{slugify(job.job_id)}-{stamp}.png"
            await page.screenshot(path=str(path), full_page=True)
            self.store.log(job.job_id, "screenshot", str(path), "error")
        except Exception:  # noqa: BLE001
            pass
