"""End-to-end GUI test: a real browser drives the app UI while the app applies to an offline
Lever-style form in a second (headless) browser. The prompts are answered through the app's
dialogs, exactly as a person would."""

import asyncio
import socket
from pathlib import Path

import pytest
import yaml

pytest.importorskip("playwright")
pytest.importorskip("starlette")
pytest.importorskip("uvicorn")

from autoapply.gui.bus import EventBus  # noqa: E402
from autoapply.gui.server import GuiState, create_app  # noqa: E402
from autoapply.store.db import Job, JobStatus, JobStore  # noqa: E402
from autoapply.workspace import create_workspace  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "lever_form.html"
ESSAY = "Why do you want to work at Acme? Tell us about a project you are proud of."
CERTIFY = "I certify that the information above is accurate"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    monkeypatch.setenv("AUTOAPPLY_HOME", str(ws))
    create_workspace(ws)
    cfg_path = ws / "config" / "config.yaml"
    cfg = yaml.safe_load(cfg_path.read_text("utf-8"))
    cfg["notifications"] = {"desktop": False, "sound": False}
    cfg["rate_limits"].update(min_delay_seconds=0, max_delay_seconds=0)
    cfg_path.write_text(yaml.safe_dump(cfg), "utf-8")
    (ws / "data" / "private" / "resume.pdf").write_bytes(b"%PDF-1.4 test resume")
    with JobStore(ws / "data" / "autoapply.db") as store:
        store.upsert(
            Job(
                job_id="lever:acme:1",
                company="Acme",
                role="Data Science Intern",
                url=FIXTURE.as_uri(),
                portal_type="lever",
                location="Miami, FL",
            )
        )
        store.set_status("lever:acme:1", JobStatus.QUEUED)
    return ws


def test_gui_applies_through_dialogs(workspace):
    import uvicorn
    from playwright.async_api import async_playwright, expect

    async def go():
        bus = EventBus()
        bus.loop = asyncio.get_running_loop()
        state = GuiState(None, "test-token", bus)
        port = _free_port()
        server = uvicorn.Server(
            uvicorn.Config(
                create_app(state), host="127.0.0.1", port=port, log_level="warning", lifespan="off"
            )
        )
        server_task = asyncio.create_task(server.serve())
        while not server.started:
            await asyncio.sleep(0.05)
        asked: list[str] = []
        try:
            async with async_playwright() as pw:
                try:
                    browser = await pw.chromium.launch()
                except Exception as exc:  # noqa: BLE001
                    pytest.skip(f"Chromium unavailable: {exc}")
                page = await browser.new_page()
                errors: list[str] = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                await page.goto(f"http://127.0.0.1:{port}/?token=test-token")

                # Postings view lists the queued job with its box ticked.
                row = page.locator("#jobs-body tr", has_text="Acme")
                await expect(row).to_be_visible()
                await expect(row.locator("input[type=checkbox]")).to_be_checked()
                await expect(row.locator(".status")).to_have_text("Queued")

                # Profile editor rejects invalid YAML and keeps the file intact.
                await page.click("button.nav[data-view=profile]")
                await expect(page.locator("#profile-text")).not_to_have_value("")
                good = await page.locator("#profile-text").input_value()
                await page.fill("#profile-text", "personal: [unclosed")
                await page.click("#save-profile")
                await expect(page.locator("#profile-error")).to_contain_text("YAML")
                await page.fill("#profile-text", good)

                # Start a headless run from the Apply view.
                await page.click("button.nav[data-view=run]")
                await page.uncheck("#opt-visible")
                await page.click("#start-run")
                await expect(page.locator("#run-indicator")).to_be_visible()

                dialog = page.locator("#prompt")
                status = page.locator("#run-status")
                for _ in range(10):
                    await page.wait_for_function(
                        "document.querySelector('#prompt').open || "
                        "document.querySelector('#run-status').textContent.startsWith('Finished')",
                        timeout=60_000,
                    )
                    if (await status.text_content() or "").startswith("Finished"):
                        break
                    question = await dialog.locator("#prompt-question").text_content()
                    asked.append(question)
                    if await dialog.locator("textarea").count():
                        await dialog.locator("textarea").fill("I built a forecasting pipeline.")
                        await dialog.get_by_role("button", name="Use this answer").click()
                    elif await dialog.get_by_role("button", name="Tick it").count():
                        await dialog.get_by_role("button", name="Tick it").click()
                    elif await dialog.get_by_role("button", name="Submit application").count():
                        filled = await dialog.locator(".filled").text_content()
                        assert "Jane Doe" in filled and "Miami, FL" in filled
                        await dialog.get_by_role("button", name="Submit application").click()
                    else:
                        raise AssertionError(f"unexpected prompt: {question}")
                    await page.wait_for_function(
                        "q => !document.querySelector('#prompt').open || "
                        "document.querySelector('#prompt-question').textContent !== q",
                        arg=question,
                    )

                await expect(status).to_contain_text("1 applied")
                await page.click("button.nav[data-view=jobs]")
                await page.select_option("#status-filter", "applied")
                await expect(page.locator("#jobs-body tr", has_text="Acme").locator(".status")).to_have_text(
                    "Applied"
                )
                assert "resume_uploaded" in await page.locator("#log").text_content()
                assert not errors, errors
                await browser.close()
        finally:
            state.closing = True
            server.should_exit = True
            await server_task
        return asked

    asked = asyncio.run(go())
    assert asked == [ESSAY, CERTIFY, "Check the form before it's sent"]
