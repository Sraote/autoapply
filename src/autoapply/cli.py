"""autoapply command-line interface."""

from __future__ import annotations

import asyncio
import os
import platform
import shutil
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from .config import AppConfig, find_project_root, load_config
from .store.db import JobStatus, JobStore

app = typer.Typer(no_args_is_help=True, help="Human-in-the-loop job application assistant.")
profile_app = typer.Typer(no_args_is_help=True, help="Build and inspect your profile.")
jobs_app = typer.Typer(no_args_is_help=True, help="Discover, add and queue postings.")
app.add_typer(profile_app, name="profile")
app.add_typer(jobs_app, name="jobs")
console = Console()

ConfigOpt = typer.Option(None, "--config", "-c", help="Path to config.yaml")


def _cfg(path: Path | None) -> AppConfig:
    return load_config(path)


def _store(cfg: AppConfig) -> JobStore:
    return JobStore(cfg.resolve(cfg.paths.database))


# ---------------------------------------------------------------- setup


@app.command()
def init() -> None:
    """Create config/config.yaml and config/profile_data.yaml from the examples."""
    root = find_project_root()
    for name in ("config", "profile_data"):
        src, dst = root / "config" / f"{name}.example.yaml", root / "config" / f"{name}.yaml"
        if dst.exists():
            console.print(f"[dim]exists[/] {dst.relative_to(root)}")
        else:
            shutil.copyfile(src, dst)
            console.print(f"[green]created[/] {dst.relative_to(root)}")
    (root / "data" / "private").mkdir(parents=True, exist_ok=True)
    console.print(
        "\nNext:\n  1. Edit config/profile_data.yaml and config/config.yaml\n"
        "  2. Put your resume at data/private/resume.pdf\n"
        "  3. autoapply profile build  →  autoapply jobs fetch  →  autoapply jobs select  →  autoapply run"
    )


@app.command()
def doctor(config: Path | None = ConfigOpt) -> None:
    """Check the environment: OS, display server, browser, files."""
    cfg = _cfg(config)
    t = Table("Check", "Result", show_header=False)
    t.add_row("Python", sys.version.split()[0])
    t.add_row("Platform", f"{platform.system()} {platform.release()}")
    if sys.platform.startswith("linux"):
        session = (
            "Wayland"
            if os.environ.get("WAYLAND_DISPLAY")
            else ("X11" if os.environ.get("DISPLAY") else "[yellow]none (headless only)[/]")
        )
        t.add_row("Display", session)
        t.add_row("notify-send", "found" if shutil.which("notify-send") else "[yellow]missing[/]")
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            exe = cfg.browser.executable_path or Path(pw.chromium.executable_path)
        ok = cfg.resolve(exe).exists() if cfg.browser.executable_path else Path(exe).exists()
        t.add_row(
            "Chromium", f"{exe}" if ok else "[red]missing[/] — run `uv run playwright install chromium`"
        )
    except Exception as exc:  # noqa: BLE001
        t.add_row("Playwright", f"[red]{escape(str(exc).splitlines()[0])}[/]")
    for label, p in (
        ("config.yaml", find_project_root() / "config" / "config.yaml"),
        ("profile_data.yaml", cfg.resolve(cfg.paths.profile)),
        ("resume.pdf", cfg.resolve(cfg.paths.resume_pdf)),
        ("user_profile.json", cfg.resolve(cfg.paths.user_profile_out)),
    ):
        t.add_row(label, "[green]ok[/]" if p.exists() else f"[yellow]missing[/] {p}")
    console.print(t)


# ---------------------------------------------------------------- profile


@profile_app.command("build")
def profile_build(
    config: Path | None = ConfigOpt,
    no_github: bool = typer.Option(False, "--no-github", help="Skip the GitHub API call"),
) -> None:
    """Merge profile_data.yaml, resume, LinkedIn export and GitHub into user_profile.json."""
    from .profile.parser import build_profile

    cfg = _cfg(config)
    profile = build_profile(cfg, fetch_github=not no_github, log=console.print)
    edu = profile.primary_education
    console.print(
        f"[green]Built[/] {cfg.resolve(cfg.paths.user_profile_out)}\n"
        f"  {profile.personal.full_name} · {edu.school if edu else 'no education'}\n"
        f"  {len(profile.experience)} roles · {len(profile.projects)} projects · "
        f"{len(profile.skills)} skills · sources: {', '.join(profile.sources)}"
    )


@profile_app.command("show")
def profile_show(config: Path | None = ConfigOpt) -> None:
    """Print the normalized profile."""
    from .profile.parser import load_profile

    console.print_json(load_profile(_cfg(config)).model_dump_json())


# ---------------------------------------------------------------- jobs


@jobs_app.command("fetch")
def jobs_fetch(config: Path | None = ConfigOpt) -> None:
    """Pull postings from configured Greenhouse/Lever boards and apply your filters."""
    from .jobs.filters import filter_postings
    from .jobs.sources import build_sources, fetch_all

    cfg = _cfg(config)
    sources = build_sources(cfg.search)
    if not sources:
        console.print("[yellow]No sources configured — add boards under search.sources.[/]")
        raise typer.Exit(1)
    postings, errors = asyncio.run(fetch_all(sources))
    for name, err in errors.items():
        console.print(f"[red]{name}[/]: {escape(err)}")
    matched = filter_postings(postings, cfg.search)
    with _store(cfg) as store:
        new = sum(store.upsert(p.to_job()) for p in matched)
    console.print(f"Fetched {len(postings)} · matched filters {len(matched)} · [green]new {new}[/]")


@jobs_app.command("add")
def jobs_add(
    url: str,
    company: str = typer.Option(..., "--company"),
    role: str = typer.Option(..., "--role"),
    location: str = typer.Option("", "--location"),
    config: Path | None = ConfigOpt,
) -> None:
    """Add a posting by URL and queue it."""
    from .jobs.sources import manual_job

    job = manual_job(url, company, role, location)
    with _store(_cfg(config)) as store:
        store.upsert(job)
        store.set_status(job.job_id, JobStatus.QUEUED, "added manually")
    console.print(f"Queued {job.job_id} [dim]({job.portal_type})[/]")


@jobs_app.command("select")
def jobs_select(config: Path | None = ConfigOpt) -> None:
    """Interactive checklist to choose which postings to apply to."""
    from .tui.queue import run_selection

    with _store(_cfg(config)) as store:
        jobs = store.list([JobStatus.DISCOVERED, JobStatus.QUEUED])
        if not jobs:
            console.print("No postings yet — run `autoapply jobs fetch`.")
            return
        selected = run_selection(jobs)
        if selected is None:
            console.print("No changes.")
            return
        chosen = set(selected)
        for job in jobs:
            if job.job_id in chosen and job.status != JobStatus.QUEUED:
                store.set_status(job.job_id, JobStatus.QUEUED)
            elif job.job_id not in chosen and job.status == JobStatus.QUEUED:
                store.set_status(job.job_id, JobStatus.DISCOVERED)
    console.print(f"[green]{len(chosen)} posting(s) queued.[/]")


@jobs_app.command("list")
def jobs_list(
    status: list[JobStatus] | None = typer.Option(None, "--status", "-s"),
    config: Path | None = ConfigOpt,
) -> None:
    """Show the tracker."""
    with _store(_cfg(config)) as store:
        jobs = store.list(status) if status else store.list()
    t = Table("Status", "Company", "Role", "Portal", "Updated", "Notes")
    for j in jobs:
        t.add_row(
            str(j.status),
            escape(j.company),
            escape(j.role[:50]),
            j.portal_type,
            j.updated_at[:16],
            escape(j.notes[:40]),
        )
    console.print(t)


@app.command("log")
def show_log(job_id: str, config: Path | None = ConfigOpt) -> None:
    """Show the application log for one job."""
    with _store(_cfg(config)) as store:
        for row in store.logs(job_id):
            console.print(
                f"[dim]{row['timestamp']}[/] {row['level']:<7} {row['event']:<16} {escape(row['detail'])}"
            )


# ---------------------------------------------------------------- run


def _run(cfg: AppConfig, headless: bool, limit: int | None, statuses) -> None:
    from .automation.runner import Runner
    from .profile.parser import load_profile

    profile = load_profile(cfg)
    with _store(cfg) as store:
        summary = asyncio.run(
            Runner(cfg, profile, store, console, headless=headless, limit=limit, statuses=statuses).run()
        )
    if summary:
        console.rule("summary")
        console.print("  ".join(f"{k}: {v}" for k, v in summary.items()))


@app.command()
def run(
    config: Path | None = ConfigOpt,
    headless: bool | None = typer.Option(None, "--headless/--headed", help="Override config"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Fill forms but never submit"),
    limit: int | None = typer.Option(None, "--limit", "-n"),
) -> None:
    """Apply to queued postings."""
    cfg = _cfg(config)
    if dry_run:
        cfg.automation.dry_run = True
    _run(cfg, cfg.browser.headless if headless is None else headless, limit, [JobStatus.QUEUED])


@app.command()
def resume(config: Path | None = ConfigOpt, limit: int | None = typer.Option(None, "-n")) -> None:
    """Re-run jobs parked as pending_input, always headed."""
    _run(_cfg(config), False, limit, [JobStatus.PENDING_INPUT])


if __name__ == "__main__":
    app()
