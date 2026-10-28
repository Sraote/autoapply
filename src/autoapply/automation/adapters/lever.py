"""Lever (jobs.lever.co/<company>/<id>/apply).

Lever parses the uploaded resume and pre-fills name/email/phone/company, which is why
the base pipeline uploads files first and then re-scans. Custom questions appear as
`cards[...]` fields and go through the same mapper → saved answers → human flow.
"""

from __future__ import annotations

import re

from .base import ATSAdapter


class LeverAdapter(ATSAdapter):
    portal_type = "lever"
    url_patterns = ("lever.co",)
    form_selector = "form#application-form, .application-form form, form[action*='/apply']"
    submit_selector = "#btn-submit, button[type=submit]"

    async def open(self) -> None:
        url = self.job.url
        if "jobs.lever.co" in url and not url.rstrip("/").endswith("/apply"):
            url = url.rstrip("/") + "/apply"
        await self.page.goto(url, wait_until="domcontentloaded")
        await self._settle()

    async def verify_submitted(self) -> bool:
        try:
            await self.page.wait_for_url(re.compile(r"/thanks"), timeout=12_000)
            return True
        except Exception:  # noqa: BLE001
            body = await self.page.locator("body").inner_text()
            return bool(re.search(r"application (submitted|received)|thank you for applying", body, re.I))
