"""Cross-platform desktop notifications + sound ping, with graceful fallbacks."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from .config import NotificationConfig

_LINUX_SOUNDS = (
    "/usr/share/sounds/freedesktop/stereo/dialog-warning.oga",
    "/usr/share/sounds/freedesktop/stereo/bell.oga",
    "/usr/share/sounds/freedesktop/stereo/complete.oga",
)


class Notifier:
    def __init__(self, cfg: NotificationConfig, app_name: str = "autoapply"):
        self.cfg = cfg
        self.app_name = app_name
        self._dn = None

    async def alert(self, title: str, message: str) -> None:
        if self.cfg.sound:
            self._beep()
        if self.cfg.desktop:
            await self._desktop(title, message)

    async def _desktop(self, title: str, message: str) -> None:
        try:
            from desktop_notifier import DesktopNotifier

            if self._dn is None:
                self._dn = DesktopNotifier(app_name=self.app_name)
            await self._dn.send(title=title, message=message)
            return
        except Exception:  # noqa: BLE001 - no DBus session, missing WinRT, etc.
            pass
        if sys.platform.startswith("linux") and shutil.which("notify-send"):
            subprocess.Popen(
                ["notify-send", "-a", self.app_name, "-u", "critical", title, message],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

    @staticmethod
    def _beep() -> None:
        try:
            if sys.platform == "win32":
                import winsound

                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
                return
            if sys.platform.startswith("linux"):
                player = shutil.which("pw-play") or shutil.which("paplay")
                sound = next((s for s in _LINUX_SOUNDS if Path(s).is_file()), None)
                if player and sound:
                    subprocess.Popen([player, sound], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return
        except Exception:  # noqa: BLE001
            pass
        sys.stdout.write("\a")
        sys.stdout.flush()
