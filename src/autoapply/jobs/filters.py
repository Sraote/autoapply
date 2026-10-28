"""Title / location / recency filters for discovered postings."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from ..config import SearchConfig
from .sources import Posting

_REMOTE = re.compile(r"\bremote\b|anywhere|work from home", re.I)


def title_matches(title: str, include: list[str], exclude: list[str]) -> bool:
    t = title.lower()
    for word in exclude:
        if re.search(rf"\b{re.escape(word.lower())}\b", t):
            return False
    if not include:
        return True
    return any(phrase.lower() in t for phrase in include)


def location_matches(location: str, wanted: list[str], allow_remote: bool) -> bool:
    if allow_remote and _REMOTE.search(location or ""):
        return True
    if not wanted:
        return True
    loc = (location or "").lower()
    return any(
        (w.lower() == "remote" and allow_remote and _REMOTE.search(loc))
        or re.search(rf"\b{re.escape(w.lower())}\b", loc)
        for w in wanted
    )


def is_recent(posted_at: datetime | None, max_age_days: int) -> bool:
    if posted_at is None or max_age_days <= 0:
        return True
    if posted_at.tzinfo is None:
        posted_at = posted_at.replace(tzinfo=UTC)
    return datetime.now(UTC) - posted_at <= timedelta(days=max_age_days)


def filter_postings(postings: list[Posting], search: SearchConfig) -> list[Posting]:
    return [
        p
        for p in postings
        if title_matches(p.title, search.include_titles, search.exclude_titles)
        and location_matches(p.location, search.locations, search.allow_remote)
        and is_recent(p.posted_at, search.max_posting_age_days)
    ]
