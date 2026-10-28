"""Environment checks shared by `autoapply doctor` and the GUI's Setup page."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from . import __version__
from .config import AppConfig


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    warn_only: bool = False


def playwright_chromium_path() -> Path | None:
    """Where Playwright's own Chromium lives (may not be downloaded yet). Sync API: call off-loop."""
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            return Path(pw.chromium.executable_path)
    except Exception:  # noqa: BLE001
        return None


def run_checks(cfg: AppConfig) -> list[Check]:
    checks = [
        Check("autoapply", True, __version__),
        Check("Python", True, sys.version.split()[0]),
        Check("Platform", True, f"{platform.system()} {platform.release()}"),
        Check("Workspace", cfg.project_root.exists(), str(cfg.project_root)),
    ]
    if sys.platform.startswith("linux"):
        if os.environ.get("WAYLAND_DISPLAY"):
            display = "Wayland"
        elif os.environ.get("DISPLAY"):
            display = "X11"
        else:
            display = "none (headless runs only)"
        checks.append(Check("Display", display != "none (headless runs only)", display, warn_only=True))
        checks.append(
            Check(
                "notify-send",
                bool(shutil.which("notify-send")),
                "found" if shutil.which("notify-send") else "missing (install libnotify)",
                warn_only=True,
            )
        )
    if cfg.browser.executable_path:
        exe = cfg.resolve(cfg.browser.executable_path)
        checks.append(Check("Browser", exe.exists(), str(exe)))
    elif cfg.browser.channel:
        checks.append(Check("Browser", True, f"channel: {cfg.browser.channel}"))
    else:
        exe = playwright_chromium_path()
        ok = bool(exe and exe.exists())
        checks.append(Check("Chromium", ok, str(exe) if ok else "not downloaded — use Install browser"))
    for label, p in (
        ("config.yaml", cfg.project_root / "config" / "config.yaml"),
        ("profile_data.yaml", cfg.resolve(cfg.paths.profile)),
        ("resume.pdf", cfg.resolve(cfg.paths.resume_pdf)),
        ("user_profile.json", cfg.resolve(cfg.paths.user_profile_out)),
    ):
        checks.append(Check(label, p.exists(), str(p)))
    return checks


def install_browser_cmd() -> list[str]:
    cmd = [sys.executable, "-m", "playwright", "install", "chromium"]
    if sys.platform.startswith("linux") and shutil.which("apt-get") and not shutil.which("pacman"):
        cmd.append("--with-deps")  # Debian/Ubuntu only; Arch gets libraries from pacman
    return cmd


def install_browser(stream=None) -> int:
    """Download Playwright's Chromium. `stream(line)` receives progress output if given."""
    if stream is None:
        return subprocess.call(install_browser_cmd())
    proc = subprocess.Popen(
        install_browser_cmd(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        if line.strip():
            stream(line.rstrip())
    return proc.wait()
