"""Detect CAPTCHAs / bot checks. These are ALWAYS handed to the human — never solved or bypassed."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Page


def classify_frame_src(src: str) -> str | None:
    s = (src or "").lower()
    if "challenges.cloudflare.com" in s:
        return "Cloudflare Turnstile"
    if "hcaptcha.com" in s:
        return "hCaptcha"
    if "recaptcha" in s and ("bframe" in s or ("anchor" in s and "size=invisible" not in s)):
        return "reCAPTCHA"
    return None


def classify_title(title: str) -> str | None:
    t = (title or "").lower()
    if "just a moment" in t or "attention required" in t:
        return "Cloudflare check"
    return None


async def detect_challenge(page: Page) -> str | None:
    if kind := classify_title(await page.title()):
        return kind
    for frame in await page.locator("iframe").all():
        kind = classify_frame_src(await frame.get_attribute("src") or "")
        if not kind:
            continue
        try:
            box = await frame.bounding_box() if await frame.is_visible() else None
        except Exception:  # noqa: BLE001 - frame detached mid-check
            continue
        if box and box["height"] > 40:  # invisible badges are tiny or hidden
            return kind
    return None
