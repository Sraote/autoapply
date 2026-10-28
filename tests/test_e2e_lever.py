"""End-to-end: real Chromium + LeverAdapter against an offline Lever-like form.

The human is scripted, so this checks the important contract: known fields are filled
from the profile, and ONLY the essay and the certification checkbox reach the human.
Skips automatically when Playwright's Chromium isn't installed.
"""

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("playwright")

from autoapply.automation.adapters import ApplyContext, LeverAdapter  # noqa: E402
from autoapply.automation.field_mapper import FieldMapper  # noqa: E402
from autoapply.automation.hitl import HitlAction, HitlResponse, HumanInTheLoop  # noqa: E402
from autoapply.config import AppConfig  # noqa: E402
from autoapply.jobs.sources import manual_job  # noqa: E402
from autoapply.notify import Notifier  # noqa: E402
from autoapply.store.db import JobStatus, JobStore  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "lever_form.html"


class ScriptedHuman(HumanInTheLoop):
    def __init__(self, console):
        super().__init__(Notifier.__new__(Notifier), console, headless=True)
        self.asked: list[str] = []

    async def ask_field(self, page, job, field, reason):
        self.asked.append(field.clean_label)
        if field.kind == "checkbox":
            return HitlResponse(HitlAction.ANSWER, "y")
        return HitlResponse(HitlAction.ANSWER, "I built a forecasting pipeline.")

    async def ask_challenge(self, page, job, kind):
        raise AssertionError("no challenge expected")

    async def confirm_submit(self, page, job, filled):
        self.filled = dict(filled)
        return "submit"

    async def confirm_outcome(self, page, job):
        return False


def test_lever_form_end_to_end(tmp_path, profile):
    from playwright.async_api import async_playwright
    from rich.console import Console

    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4 test")
    cfg = AppConfig()
    cfg.project_root = tmp_path
    cfg.paths.resume_pdf = resume
    cfg.paths.answers_learned = tmp_path / "answers.yaml"
    console = Console()
    human = ScriptedHuman(console)

    async def go():
        async with async_playwright() as pw:
            try:
                browser = await pw.chromium.launch()
            except Exception as exc:  # noqa: BLE001
                pytest.skip(f"Chromium unavailable: {exc}")
            page = await browser.new_page()
            with JobStore(tmp_path / "t.db") as store:
                job = manual_job(FIXTURE.as_uri(), "acme", "Data Science Intern")
                store.upsert(job)
                ctx = ApplyContext(
                    page, cfg, profile, FieldMapper(profile), human, store, job, console, headless=True
                )
                result = await LeverAdapter(ctx).run()
            await browser.close()
            return result

    result = asyncio.run(go())

    assert result.status == JobStatus.APPLIED, result.message
    assert sorted(human.asked) == sorted(
        [
            "Why do you want to work at Acme? Tell us about a project you are proud of.",
            "I certify that the information above is accurate",
        ]
    )
    f = human.filled
    assert f["Full name"] == "Jane Doe"
    assert f["Preferred name"] == "Jane"  # revealed only after resume upload → re-scan works
    assert f["Location"] == "Miami, FL"
    assert f["Are you legally authorized to work in the United States?"] == "Yes"
    assert f["Will you now or in the future require visa sponsorship?"] == "No"
    assert f["Are you willing to relocate?"] == "Yes"
    assert f["Gender"] == "decline"
    assert "Phone" not in f  # prefilled by the "resume parser", left untouched
