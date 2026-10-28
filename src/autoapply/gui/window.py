"""Open the UI in a chromeless app window (Edge/Chrome/Chromium `--app` mode).

Uses a dedicated profile so the window is its own process: closing it quits autoapply.
Falls back to the default browser if no Chromium-family browser is found.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from pathlib import Path

from ..config import AppConfig
from ..diagnostics import playwright_chromium_path


def _windows_candidates() -> list[Path]:
    roots = [os.environ.get(k) for k in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA")]
    rel = (
        r"Microsoft\Edge\Application\msedge.exe",  # ships with every Windows 11 install
        r"Google\Chrome\Application\chrome.exe",
        r"BraveSoftware\Brave-Browser\Application\brave.exe",
    )
    return [Path(r) / p for p in rel for r in roots if r]


def _linux_candidates() -> list[Path]:
    names = (
        "chromium",
        "chromium-browser",
        "google-chrome-stable",
        "google-chrome",
        "brave",
        "brave-browser",
        "microsoft-edge-stable",
        "vivaldi-stable",
    )
    return [Path(p) for n in names if (p := shutil.which(n))]


def find_app_browser(cfg: AppConfig) -> Path | None:
    candidates: list[Path] = []
    if cfg.browser.executable_path:
        candidates.append(cfg.resolve(cfg.browser.executable_path))
    if sys.platform == "win32":
        candidates += _windows_candidates()
    elif sys.platform.startswith("linux"):
        candidates += _linux_candidates()
    elif sys.platform == "darwin":
        candidates += [
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
        ]
    if pw := playwright_chromium_path():
        candidates.append(pw)
    return next((c for c in candidates if c.is_file()), None)


def app_window_args(exe: Path, url: str, profile_dir: Path) -> list[str]:
    args = [
        str(exe),
        f"--app={url}",
        f"--user-data-dir={profile_dir}",
        "--window-size=1280,860",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-mode",
        "--disable-sync",
        "--disable-features=Translate,AutofillServerCommunication,MediaRouter",
        "--class=autoapply",  # WM_CLASS on Linux, matches StartupWMClass in the .desktop file
    ]
    if sys.platform.startswith("linux") and os.environ.get("WAYLAND_DISPLAY"):
        args.append("--ozone-platform-hint=auto")
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        args.append("--no-sandbox")  # Chromium refuses to start as root otherwise
    return args


async def open_app_window(url: str, cfg: AppConfig) -> asyncio.subprocess.Process | None:
    exe = await asyncio.to_thread(find_app_browser, cfg)
    if exe is None:
        return None
    profile_dir = cfg.project_root / "data" / "app-window"
    profile_dir.mkdir(parents=True, exist_ok=True)
    return await asyncio.create_subprocess_exec(
        *app_window_args(exe, url, profile_dir),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
