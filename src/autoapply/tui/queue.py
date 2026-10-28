"""Textual selection queue: check/uncheck postings before a run."""

from __future__ import annotations

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, SelectionList, Static
from textual.widgets.selection_list import Selection

from ..store.db import Job, JobStatus

SUPPORTED = {"greenhouse", "lever"}


def _label(job: Job) -> Text:
    portal_style = "green" if job.portal_type in SUPPORTED else "yellow"
    t = Text()
    t.append(f"{job.company[:18]:<18} ", style="bold")
    t.append(f"{job.role[:58]:<58} ")
    t.append(f"{(job.location or '-')[:22]:<22} ", style="dim")
    t.append(f"[{job.portal_type}]", style=portal_style)
    return t


class QueueApp(App[list[str] | None]):
    TITLE = "autoapply · select postings"
    CSS = """
    #help { padding: 0 1; color: $text-muted; }
    SelectionList { height: 1fr; border: round $accent; }
    """
    BINDINGS = [
        Binding("a", "select_all", "Select all"),
        Binding("n", "deselect_all", "Clear"),
        Binding("c", "confirm", "Confirm queue"),
        Binding("q", "cancel", "Quit (no changes)"),
    ]

    def __init__(self, jobs: list[Job]):
        super().__init__()
        self.jobs = jobs

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(
            "Space toggles · green portals are automated, yellow open for you to fill manually",
            id="help",
        )
        yield SelectionList[str](
            *(Selection(_label(j), j.job_id, initial_state=j.status == JobStatus.QUEUED) for j in self.jobs)
        )
        yield Footer()

    def action_select_all(self) -> None:
        self.query_one(SelectionList).select_all()

    def action_deselect_all(self) -> None:
        self.query_one(SelectionList).deselect_all()

    def action_confirm(self) -> None:
        self.exit(list(self.query_one(SelectionList).selected))

    def action_cancel(self) -> None:
        self.exit(None)


def run_selection(jobs: list[Job]) -> list[str] | None:
    """Returns the selected job_ids, or None if the user quit without confirming."""
    return QueueApp(jobs).run()
