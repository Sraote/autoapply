from __future__ import annotations

import re
from datetime import UTC, datetime


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "x"


def normalize_label(text: str) -> str:
    """Strip required markers and whitespace noise from a form label."""
    text = text.replace("*", " ").replace("✱", " ")
    text = re.sub(r"\(\s*(required|optional)\s*\)", " ", text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip()
