"""autoapply command-line interface."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from . import __version__
from .config import AppConfig, find_project_root, load_config
from .store.db import JobStatus, JobStore


def _utf8_stdio() -> None:
    """Windows uses cp1252 for piped output (scripts, CI, redirects), which can't encode the
    arrows and box characters Rich prints. Force UTF-8 so output never crashes the CLI."""
    for stream in (sys.stdout, sys.stderr):
        enc = (getattr(stream, "encoding", "") or "").lower().replace("-", "")
        if hasattr(stream, "reconfigure") and enc != "utf8":
            stream.reconfigure(encoding="utf-8", errors="replace")


_utf8_stdio()

app = typer.Typer(no_args_is_help=True, help="Human-in-the-loop job application assistant.")
profile_app = typer.Typer(no_args_is_help=True, help="Build and inspect your profile.")
jobs_app = typer.Typer(no_args_is_help=True, help="Discover, add and queue postings.")
app.add_typer(profile_app, name="profile")
app.add_typer(jobs_app, name="jobs")
console = Console()

ConfigOpt = typer.Option(None, "--config", "-c", help="Path to config.yaml")


def _version(value: bool) -> None:
    if value:
        console.print(f"autoapply {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(False, "--version", callback=_version, is_eager=True),
) -> None:
    """Human-in-the-loop job application assistant."""


def _cfg(path: Path | None) -> AppConfig:
    return load_config(path)


def _store(cfg: AppConfig) -> JobStore:
    return JobStore(cfg.resolve(cfg.paths.database))


# ---------------------------------------------------------------- setup


@app.command()
def init(
    here: bool = typer.Option(False, "--here", help="Create the workspace in the current directory"),
) -> None:
    """Create a workspace: config/config.yaml, config/profile_data.yaml and data/."""
    from .workspace import create_workspace

    root = Path.cwd().resolve() if here else find_project_root()
    created = create_workspace(root)
    for path in created:
        console.print(f"[green]created[/] {path}")
    if not created:
        console.print("[dim]Workspace already set up; nothing overwritten.[/]")
    console.print(
        f"\nWorkspace: [bold]{root}[/]\nNext:\n"
        "  1. Edit config/profile_data.yaml and config/config.yaml (or use `autoapply gui`)\n"
        "  2. Put your resume at data/private/resume.pdf\n"
        "  3. autoapply profile build → jobs fetch → jobs select → run --dry-run"
    )


@app.command("install-browser")
def install_browser() -> None:
    """Download the Chromium build this version of Playwright expects."""
    from .diagnostics import install_browser as _install

    raise typer.Exit(_install())


@app.command()
def doctor(config: Path | None = ConfigOpt) -> None:
    """Check the environment: OS, display server, browser, files."""
    from .diagnostics import run_checks

    t = Table("Check", "Result", show_header=False)
    for c in run_checks(_cfg(config)):
        color = "green" if c.ok else ("yellow" if c.warn_only else "red")
        t.add_row(c.name, f"[{color}]{escape(c.detail)}[/]")
    console.print(t)


@app.command()
def gui(
    config: Path | None = ConfigOpt,
    no_window: bool = typer.Option(False, "--no-window", help="Only start the server; print its URL"),
    port: int = typer.Option(0, "--port", help="Local port (default: any free port)"),
    self_test: bool = typer.Option(False, "--self-test", help="Start, check the UI responds, exit"),
) -> None:
    """Open the desktop app."""
    from .gui import launch

    raise typer.Exit(launch(config=config, open_window=not no_window, port=port, self_test=self_test))


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
