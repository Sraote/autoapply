"""SQLite job tracker: one row per posting plus an append-only application log."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from ..utils import now_iso


class JobStatus(StrEnum):
    DISCOVERED = "discovered"  # fetched, not yet selected
    QUEUED = "queued"  # selected for the next run
    IN_PROGRESS = "in_progress"
    PENDING_INPUT = "pending_input"  # needs you (challenge in headless, timeout, no adapter)
    APPLIED = "applied"
    FAILED = "failed"
    SKIPPED = "skipped"


SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id      TEXT PRIMARY KEY,
    company     TEXT NOT NULL,
    role        TEXT NOT NULL,
    location    TEXT NOT NULL DEFAULT '',
    url         TEXT NOT NULL,
    portal_type TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'discovered',
    source      TEXT NOT NULL DEFAULT '',
    posted_at   TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    applied_at  TEXT,
    notes       TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);

CREATE TABLE IF NOT EXISTS application_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id    TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    timestamp TEXT NOT NULL,
    level     TEXT NOT NULL,
    event     TEXT NOT NULL,
    detail    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_log_job ON application_log(job_id);
"""


@dataclass
class Job:
    job_id: str
    company: str
    role: str
    url: str
    portal_type: str
    location: str = ""
    status: JobStatus = JobStatus.DISCOVERED
    source: str = ""
    posted_at: str | None = None
    created_at: str = ""
    updated_at: str = ""
    applied_at: str | None = None
    notes: str = ""

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> Job:
        data = dict(row)
        data["status"] = JobStatus(data["status"])
        return cls(**data)


class JobStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.executescript(SCHEMA)

    def __enter__(self) -> JobStore:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self.conn.close()

    # ---------------------------------------------------------------- jobs

    def upsert(self, job: Job) -> bool:
        """Insert or refresh posting metadata. Never touches status. Returns True if new."""
        now = now_iso()
        exists = self.get(job.job_id) is not None
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO jobs (job_id, company, role, location, url, portal_type, status,
                                  source, posted_at, created_at, updated_at, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    role = excluded.role, location = excluded.location, url = excluded.url,
                    posted_at = excluded.posted_at, updated_at = excluded.updated_at
                """,
                (
                    job.job_id,
                    job.company,
                    job.role,
                    job.location,
                    job.url,
                    job.portal_type,
                    str(job.status),
                    job.source,
                    job.posted_at,
                    now,
                    now,
                    job.notes,
                ),
            )
        return not exists

    def get(self, job_id: str) -> Job | None:
        row = self.conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        return Job.from_row(row) if row else None

    def list(self, statuses: Iterable[JobStatus] | None = None) -> list[Job]:
        if statuses is None:
            rows = self.conn.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall()
        else:
            values = [str(s) for s in statuses]
            marks = ",".join("?" * len(values))
            rows = self.conn.execute(
                f"SELECT * FROM jobs WHERE status IN ({marks}) ORDER BY created_at", values
            ).fetchall()
        return [Job.from_row(r) for r in rows]

    def set_status(self, job_id: str, status: JobStatus, note: str | None = None) -> None:
        now = now_iso()
        with self.conn:
            self.conn.execute(
                """UPDATE jobs SET status = ?, updated_at = ?,
                       applied_at = CASE WHEN ? = 'applied' THEN ? ELSE applied_at END,
                       notes = COALESCE(?, notes)
                   WHERE job_id = ?""",
                (str(status), now, str(status), now, note, job_id),
            )
        self.log(job_id, "status", str(status) + (f": {note}" if note else ""))

    def count_applied_today(self, company: str) -> int:
        today = datetime.now(UTC).date().isoformat()
        row = self.conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE company = ? AND status = 'applied' "
            "AND substr(applied_at, 1, 10) = ?",
            (company, today),
        ).fetchone()
        return int(row[0])

    # ---------------------------------------------------------------- log

    def log(self, job_id: str, event: str, detail: str = "", level: str = "info") -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO application_log (job_id, timestamp, level, event, detail) "
                "VALUES (?, ?, ?, ?, ?)",
                (job_id, now_iso(), level, event, detail),
            )

    def logs(self, job_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM application_log WHERE job_id = ? ORDER BY id", (job_id,)
        ).fetchall()
