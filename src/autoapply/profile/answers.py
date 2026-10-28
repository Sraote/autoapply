"""Answers saved during human-in-the-loop prompts, reused on later applications."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from ..utils import normalize_label
from .models import SavedAnswer


def load_learned(path: Path) -> list[SavedAnswer]:
    if not path.is_file():
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    return [SavedAnswer.model_validate(item) for item in raw]


def save_learned(path: Path, label: str, answer: str) -> SavedAnswer:
    """Persist an answer keyed on the exact (normalized) question text."""
    pattern = "^" + re.escape(normalize_label(label)) + "$"
    entry = SavedAnswer(pattern=pattern, answer=answer)
    existing = [a for a in load_learned(path) if a.pattern != pattern]
    existing.append(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump([a.model_dump() for a in existing], sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return entry
