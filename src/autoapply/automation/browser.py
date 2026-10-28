"""Playwright launch with a persistent profile, tuned for Wayland/X11 and Windows."""

from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from ..config import AppConfig

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext


def chromium_args(headless: bool) -> list[str]:
    args: list[str] = []
    if sys.platform.startswith("linux") and not headless and os.environ.get("WAYLAND_DISPLAY"):
        # Native Wayland (Hyprland, Sway, GNOME); Chromium falls back to XWayland/X11 otherwise.
        args.append("--ozone-platform-hint=auto")
    return args


@asynccontextmanager
async def launch_browser(cfg: AppConfig, *, headless: bool) -> AsyncIterator[BrowserContext]:
    """Persistent context = your ATS logins and cookies survive between runs.

    Deliberately no fingerprint spoofing or "stealth" patches: the tool behaves like
    you in a normal browser, at human pace, and hands any bot check to you.
    """
    from playwright.async_api import async_playwright

    user_data = cfg.resolve(cfg.browser.user_data_dir)
    user_data.mkdir(parents=True, exist_ok=True)
    kwargs: dict = {
        "headless": headless,
        "slow_mo": cfg.browser.slow_mo_ms,
        "args": chromium_args(headless),
        "accept_downloads": False,
        "viewport": None if not headless else {"width": 1366, "height": 900},
    }
    if cfg.browser.channel:
        kwargs["channel"] = cfg.browser.channel
    if cfg.browser.executable_path:
        kwargs["executable_path"] = str(cfg.resolve(cfg.browser.executable_path))

    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(str(user_data), **kwargs)
        ctx.set_default_timeout(cfg.browser.default_timeout_ms)
        try:
            yield ctx
        finally:
            await ctx.close()
