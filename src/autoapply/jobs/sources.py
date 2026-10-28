"""Job discovery from public ATS job-board APIs (no scraping, no login)."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import httpx

from ..config import SearchConfig
from ..store.db import Job

PORTAL_HINTS: tuple[tuple[str, str], ...] = (
    ("greenhouse.io", "greenhouse"),
    ("lever.co", "lever"),
    ("myworkdayjobs.com", "workday"),
    ("myworkdaysite.com", "workday"),
    ("ashbyhq.com", "ashby"),
    ("taleo.net", "taleo"),
    ("smartrecruiters.com", "smartrecruiters"),
    ("icims.com", "icims"),
)


def detect_portal(url: str) -> str:
    u = url.lower()
    return next((portal for hint, portal in PORTAL_HINTS if hint in u), "unknown")


@dataclass
class Posting:
    external_id: str
    company: str
    title: str
    location: str
    url: str
    portal_type: str
    source: str
    posted_at: datetime | None = None

    @property
    def job_id(self) -> str:
        return f"{self.portal_type}:{self.company.lower()}:{self.external_id}"

    def to_job(self) -> Job:
        return Job(
            job_id=self.job_id,
            company=self.company,
            role=self.title,
            location=self.location,
            url=self.url,
            portal_type=self.portal_type,
            source=self.source,
            posted_at=self.posted_at.isoformat() if self.posted_at else None,
        )


def manual_job(url: str, company: str, role: str, location: str = "") -> Job:
    digest = hashlib.sha1(url.encode()).hexdigest()[:12]
    portal = detect_portal(url)
    return Job(
        job_id=f"{portal}:{company.lower()}:{digest}",
        company=company,
        role=role,
        location=location,
        url=url,
        portal_type=portal,
        source="manual",
    )


class JobSource(Protocol):
    name: str

    async def fetch(self, client: httpx.AsyncClient) -> list[Posting]: ...


class GreenhouseSource:
    """https://developers.greenhouse.io/job-board.html (public, unauthenticated)."""

    def __init__(self, board_token: str):
        self.board_token = board_token
        self.name = f"greenhouse:{board_token}"

    async def fetch(self, client: httpx.AsyncClient) -> list[Posting]:
        resp = await client.get(f"https://boards-api.greenhouse.io/v1/boards/{self.board_token}/jobs")
        resp.raise_for_status()
        return self.parse(resp.json())

    def parse(self, payload: dict) -> list[Posting]:
        out = []
        for j in payload.get("jobs", []):
            ts = j.get("updated_at") or j.get("first_published")
            out.append(
                Posting(
                    external_id=str(j["id"]),
                    company=j.get("company_name") or self.board_token,
                    title=j.get("title", ""),
                    location=(j.get("location") or {}).get("name", ""),
                    url=j.get("absolute_url", ""),
                    portal_type="greenhouse",
                    source=self.name,
                    posted_at=datetime.fromisoformat(ts) if ts else None,
                )
            )
        return out


class LeverSource:
    """https://github.com/lever/postings-api (public, unauthenticated)."""

    def __init__(self, company: str):
        self.company = company
        self.name = f"lever:{company}"

    async def fetch(self, client: httpx.AsyncClient) -> list[Posting]:
        resp = await client.get(f"https://api.lever.co/v0/postings/{self.company}", params={"mode": "json"})
        resp.raise_for_status()
        return self.parse(resp.json())

    def parse(self, payload: list[dict]) -> list[Posting]:
        out = []
        for j in payload:
            created = j.get("createdAt")
            cats = j.get("categories") or {}
            out.append(
                Posting(
                    external_id=j["id"],
                    company=self.company,
                    title=j.get("text", ""),
                    location=cats.get("location", "") or ", ".join(cats.get("allLocations", [])),
                    url=j.get("applyUrl") or (j.get("hostedUrl", "").rstrip("/") + "/apply"),
                    portal_type="lever",
                    source=self.name,
                    posted_at=datetime.fromtimestamp(created / 1000, UTC) if created else None,
                )
            )
        return out


def build_sources(search: SearchConfig) -> list[JobSource]:
    return [GreenhouseSource(t) for t in search.sources.greenhouse_boards] + [
        LeverSource(c) for c in search.sources.lever_companies
    ]


async def fetch_all(sources: list[JobSource], timeout: float = 20) -> tuple[list[Posting], dict[str, str]]:
    """Fetch every source concurrently. Returns (postings, {source_name: error})."""
    headers = {"User-Agent": "autoapply/0.1 (+https://github.com)"}
    async with httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=True) as c:
        results = await asyncio.gather(*(s.fetch(c) for s in sources), return_exceptions=True)
    postings: list[Posting] = []
    errors: dict[str, str] = {}
    for src, res in zip(sources, results, strict=True):
        if isinstance(res, BaseException):
            errors[src.name] = str(res) or type(res).__name__
        else:
            postings.extend(res)
    return postings, errors
