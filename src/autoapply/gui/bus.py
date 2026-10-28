"""In-process event bus: server → browser via Server-Sent Events."""

from __future__ import annotations

import asyncio
import io
import re
from collections import deque
from typing import Any

from ..utils import now_iso

_MARKUP = re.compile(r"\[/?[a-z #_]*\]")


class EventBus:
    def __init__(self, history: int = 800):
        self._subs: set[asyncio.Queue[dict[str, Any]]] = set()
        self.log_history: deque[dict[str, Any]] = deque(maxlen=history)
        self.loop: asyncio.AbstractEventLoop | None = None

    def publish(self, event: dict[str, Any]) -> None:
        event.setdefault("ts", now_iso())
        if event.get("type") == "log":
            self.log_history.append(event)
        for q in list(self._subs):
            q.put_nowait(event)

    def publish_threadsafe(self, event: dict[str, Any]) -> None:
        """For worker threads (profile build, browser download)."""
        if self.loop:
            self.loop.call_soon_threadsafe(self.publish, event)

    def log(self, text: str, level: str = "info") -> None:
        self.publish({"type": "log", "text": text, "level": level})

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        self._subs.discard(q)


class BusWriter(io.TextIOBase):
    """File-like target for a Rich Console: every finished line becomes a log event."""

    def __init__(self, bus: EventBus):
        self.bus = bus
        self._buf = ""

    def writable(self) -> bool:
        return True

    def write(self, s: str) -> int:
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = _MARKUP.sub("", line).rstrip()
            if set(line.strip()) <= set("─━-"):
                continue  # bare horizontal rule
            if line.lstrip().startswith(("─", "━")):
                line = line.strip("─━ ")  # Rich rule with a title
            if line.strip():
                level = "warning" if line.lstrip().startswith(("warning", "challenge")) else "info"
                self.bus.log(line, level)
        return len(s)

    def flush(self) -> None:
        if self._buf.strip():
            self.bus.log(_MARKUP.sub("", self._buf).rstrip())
        self._buf = ""
