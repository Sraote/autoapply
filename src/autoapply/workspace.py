"""Workspace creation (config/ + data/), shared by `autoapply init` and the GUI's first run."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from .config import WORKSPACE_MARKER


def template_text(name: str) -> str:
    """name: 'config' or 'profile_data'."""
    return (files("autoapply") / "templates" / f"{name}.example.yaml").read_text(encoding="utf-8")


def create_workspace(root: Path) -> list[Path]:
    """Create any missing workspace files. Never overwrites. Returns the files created."""
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "data" / "private").mkdir(parents=True, exist_ok=True)
    (root / WORKSPACE_MARKER).touch()
    created = []
    for name in ("config", "profile_data"):
        dst = root / "config" / f"{name}.yaml"
        if not dst.exists():
            dst.write_text(template_text(name), encoding="utf-8")
            created.append(dst)
    return created


def is_initialized(root: Path) -> bool:
    return (root / "config" / "config.yaml").is_file()
