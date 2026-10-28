"""Greenhouse (boards.greenhouse.io, job-boards.greenhouse.io, and embedded iframes).

Selectors reflect Greenhouse's hosted boards as of 2026; ATS markup drifts, so run
`autoapply run --dry-run` against a live posting after upgrading and adjust here.
"""

from __future__ import annotations

import re

from .base import ATSAdapter

_CONFIRM_TEXT = re.compile(
    r"thank(s| you) for (applying|your application)|application (has been )?(received|submitted)",
    re.I,
)


class GreenhouseAdapter(ATSAdapter):
    portal_type = "greenhouse"
    url_patterns = ("greenhouse.io",)
    form_selector = "#application-form, #application_form, form#application, form[action*='applications']"
    submit_selector = "#submit_app, button[type=submit]"

    async def open(self) -> None:
        await super().open()
        # Company career pages often embed the board in an iframe; open its source directly.
        iframe = self.page.locator("iframe#grnhse_iframe")
        if await iframe.count():
            src = await iframe.first.get_attribute("src")
            if src:
                await self.page.goto(src, wait_until="domcontentloaded")
                await self._settle()

    async def prepare_form(self) -> None:
        form = self.page.locator(self.form_selector)
        if not await form.count():
            apply = self.page.get_by_role("button", name=re.compile(r"^\s*apply", re.I))
            if await apply.count():
                await apply.first.click()
        await form.first.wait_for(state="attached", timeout=15_000)
        await form.first.scroll_into_view_if_needed()

    async def verify_submitted(self) -> bool:
        if re.search(r"confirmation|thank", self.page.url, re.I):
            return True
        try:
            await self.page.get_by_text(_CONFIRM_TEXT).first.wait_for(timeout=12_000)
            return True
        except Exception:  # noqa: BLE001
            return False
