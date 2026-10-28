"""Typed configuration loaded from config/config.yaml."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator


class BrowserConfig(BaseModel):
    headless: bool = False
    channel: str | None = None
    executable_path: Path | None = None
    slow_mo_ms: int = 75
    default_timeout_ms: int = 20_000
    user_data_dir: Path = Path("data/browser-profile")


class RateLimitConfig(BaseModel):
    max_applications_per_run: int = 10
    min_delay_seconds: float = 45
    max_delay_seconds: float = 120
    max_per_company_per_day: int = 2

    @model_validator(mode="after")
    def _check(self) -> RateLimitConfig:
        if self.min_delay_seconds > self.max_delay_seconds:
            raise ValueError("rate_limits.min_delay_seconds must be <= max_delay_seconds")
        return self


class NotificationConfig(BaseModel):
    desktop: bool = True
    sound: bool = True


class SearchSources(BaseModel):
    greenhouse_boards: list[str] = Field(default_factory=list)
    lever_companies: list[str] = Field(default_factory=list)


class SearchConfig(BaseModel):
    include_titles: list[str] = Field(default_factory=lambda: ["intern", "new grad", "junior"])
    exclude_titles: list[str] = Field(default_factory=lambda: ["senior", "staff", "principal"])
    locations: list[str] = Field(default_factory=list)
    allow_remote: bool = True
    max_posting_age_days: int = 30
    sources: SearchSources = Field(default_factory=SearchSources)


class AutomationConfig(BaseModel):
    require_submit_confirmation: bool = True
    dry_run: bool = False
    ask_on_optional_unknown: bool = False
    hitl_timeout_seconds: int = 0
    screenshot_on_failure: bool = True
    headless_challenge_policy: Literal["defer"] = "defer"


class PathsConfig(BaseModel):
    profile: Path = Path("config/profile_data.yaml")
    resume_pdf: Path = Path("data/private/resume.pdf")
    resume_json: Path | None = None
    linkedin_export: Path | None = None
    answers_learned: Path = Path("data/answers_learned.yaml")
    user_profile_out: Path = Path("data/user_profile.json")
    database: Path = Path("data/autoapply.db")
    logs_dir: Path = Path("data/logs")


class AppConfig(BaseModel):
    search: SearchConfig = Field(default_factory=SearchConfig)
    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    rate_limits: RateLimitConfig = Field(default_factory=RateLimitConfig)
    automation: AutomationConfig = Field(default_factory=AutomationConfig)
    notifications: NotificationConfig = Field(default_factory=NotificationConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    project_root: Path = Field(default_factory=Path.cwd, exclude=True)

    def resolve(self, p: Path | str) -> Path:
        """Resolve a config path (supports ~) relative to the project root, on any OS."""
        path = Path(p).expanduser()
        return path if path.is_absolute() else (self.project_root / path).resolve()


def find_project_root(start: Path | None = None) -> Path:
    start = (start or Path.cwd()).resolve()
    for candidate in (start, *start.parents):
        if (candidate / "pyproject.toml").is_file() or (candidate / ".git").exists():
            return candidate
    return Path.cwd().resolve()


def load_config(path: Path | None = None) -> AppConfig:
    root = find_project_root(path.parent if path else None)
    path = path or root / "config" / "config.yaml"
    data: dict = {}
    if path.is_file():
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cfg = AppConfig.model_validate(data)
    cfg.project_root = root
    return cfg
