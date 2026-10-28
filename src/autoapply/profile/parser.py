"""Build the normalized UserProfile from profile_data.yaml + optional secondary sources.

Precedence: profile_data.yaml > JSON Resume > LinkedIn export > resume PDF > GitHub.
Secondary sources only fill gaps; they never overwrite what you wrote in YAML.
LinkedIn is read from your official data export only — this tool never scrapes LinkedIn.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import zipfile
from pathlib import Path
from typing import Any

import httpx
import yaml

from ..config import AppConfig
from .answers import load_learned
from .models import UserProfile

# ---------------------------------------------------------------- merge helpers


def _empty(v: Any) -> bool:
    return v is None or v == "" or v == [] or v == {}


def fill_missing(base: dict, extra: dict) -> dict:
    """Recursively copy keys from `extra` into `base` where base has no value."""
    for key, value in extra.items():
        if _empty(value):
            continue
        if key not in base or _empty(base[key]):
            base[key] = value
        elif isinstance(base[key], dict) and isinstance(value, dict):
            fill_missing(base[key], value)
    return base


def _union(a: list[str], b: list[str]) -> list[str]:
    seen = {x.lower() for x in a}
    return a + [x for x in b if x.lower() not in seen and not seen.add(x.lower())]


# ---------------------------------------------------------------- JSON Resume


def from_json_resume(jr: dict) -> dict:
    basics = jr.get("basics", {})
    name = (basics.get("name") or "").split()
    loc = basics.get("location", {})
    links: dict[str, Any] = {"portfolio": basics.get("url")}
    for prof in basics.get("profiles", []):
        net = (prof.get("network") or "").lower()
        if net in ("linkedin", "github"):
            links[net] = prof.get("url")
    return {
        "personal": {
            "first_name": name[0] if name else "",
            "last_name": " ".join(name[1:]),
            "email": basics.get("email", ""),
            "phone": basics.get("phone", ""),
            "location": {
                "city": loc.get("city", ""),
                "state": loc.get("region", ""),
                "postal_code": loc.get("postalCode", ""),
            },
        },
        "links": links,
        "education": [
            {
                "school": e.get("institution", ""),
                "degree": e.get("studyType", ""),
                "major": e.get("area", ""),
                "gpa": _to_float(e.get("score")),
                "start": (e.get("startDate") or "")[:7] or None,
                "graduation": (e.get("endDate") or "")[:7] or None,
                "coursework": e.get("courses", []),
            }
            for e in jr.get("education", [])
        ],
        "experience": [
            {
                "company": w.get("name", ""),
                "title": w.get("position", ""),
                "start": (w.get("startDate") or "")[:7] or None,
                "end": (w.get("endDate") or "")[:7] or None,
                "current": not w.get("endDate"),
                "highlights": w.get("highlights", []),
            }
            for w in jr.get("work", [])
        ],
        "projects": [
            {
                "name": p.get("name", ""),
                "url": p.get("url"),
                "description": p.get("description", ""),
                "technologies": p.get("keywords", []),
            }
            for p in jr.get("projects", [])
        ],
        "skills": [
            kw for s in jr.get("skills", []) for kw in [s.get("name", "")] + s.get("keywords", []) if kw
        ],
    }


def _to_float(v: Any) -> float | None:
    try:
        return float(str(v).split("/")[0])
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- LinkedIn export


def _read_export_csv(export: Path, name: str) -> list[dict[str, str]]:
    if export.suffix.lower() == ".zip":
        with zipfile.ZipFile(export) as zf:
            match = next((n for n in zf.namelist() if n.rsplit("/", 1)[-1] == name), None)
            if not match:
                return []
            text = zf.read(match).decode("utf-8-sig")
    else:
        path = export / name
        if not path.is_file():
            return []
        text = path.read_text(encoding="utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


def _li_date(s: str) -> str | None:
    """LinkedIn exports dates like 'Jun 2025' or '2025'."""
    m = re.match(r"([A-Za-z]{3})\s+(\d{4})", s or "")
    months = "jan feb mar apr may jun jul aug sep oct nov dec".split()
    if m and m.group(1).lower() in months:
        return f"{m.group(2)}-{months.index(m.group(1).lower()) + 1:02d}"
    m = re.match(r"(\d{4})", s or "")
    return m.group(1) if m else None


def from_linkedin_export(export: Path) -> dict:
    positions = _read_export_csv(export, "Positions.csv")
    education = _read_export_csv(export, "Education.csv")
    skills = _read_export_csv(export, "Skills.csv")
    return {
        "experience": [
            {
                "company": p.get("Company Name", ""),
                "title": p.get("Title", ""),
                "location": p.get("Location", ""),
                "start": _li_date(p.get("Started On", "")),
                "end": _li_date(p.get("Finished On", "")),
                "current": not p.get("Finished On"),
                "highlights": [p["Description"]] if p.get("Description") else [],
            }
            for p in positions
        ],
        "education": [
            {
                "school": e.get("School Name", ""),
                "degree": e.get("Degree Name", ""),
                "start": _li_date(e.get("Start Date", "")),
                "graduation": _li_date(e.get("End Date", "")),
            }
            for e in education
        ],
        "skills": [s["Name"] for s in skills if s.get("Name")],
    }


# ---------------------------------------------------------------- resume PDF


def extract_pdf_text(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def from_resume_text(text: str) -> dict:
    """Conservative regex extraction: contact info and profile links only."""
    email = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", text)
    phone = re.search(r"(\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}", text)
    linkedin = re.search(r"(https?://)?(www\.)?linkedin\.com/in/[\w-]+/?", text)
    github = re.search(r"(https?://)?(www\.)?github\.com/[\w-]+/?", text)

    def url(m: re.Match | None) -> str | None:
        if not m:
            return None
        s = m.group(0).rstrip("/")
        return s if s.startswith("http") else "https://" + s

    return {
        "personal": {
            "email": email.group(0) if email else "",
            "phone": phone.group(0) if phone else "",
        },
        "links": {"linkedin": url(linkedin), "github": url(github)},
    }


# ---------------------------------------------------------------- GitHub


def github_username(url: str | None) -> str | None:
    m = re.search(r"github\.com/([\w-]+)", url or "")
    return m.group(1) if m else None


def fetch_github_projects(username: str, limit: int = 6) -> list[dict]:
    headers = {"Accept": "application/vnd.github+json"}
    if token := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    resp = httpx.get(
        f"https://api.github.com/users/{username}/repos",
        params={"sort": "updated", "per_page": 50},
        headers=headers,
        timeout=15,
    )
    resp.raise_for_status()
    repos = [r for r in resp.json() if not r.get("fork") and not r.get("archived")]
    repos.sort(key=lambda r: (r.get("stargazers_count", 0), r.get("pushed_at") or ""), reverse=True)
    return [
        {
            "name": r["name"],
            "url": r["html_url"],
            "description": r.get("description") or "",
            "technologies": [t for t in [r.get("language"), *r.get("topics", [])] if t],
        }
        for r in repos[:limit]
    ]


# ---------------------------------------------------------------- build


def build_profile(cfg: AppConfig, *, fetch_github: bool = True, log=print) -> UserProfile:
    profile_path = cfg.resolve(cfg.paths.profile)
    if not profile_path.is_file():
        raise FileNotFoundError(f"{profile_path} not found — run `autoapply init` first.")
    data: dict = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
    sources = ["profile_data.yaml"]
    yaml_skills = list(data.get("skills") or [])

    secondary: list[tuple[str, dict]] = []
    if cfg.paths.resume_json and (p := cfg.resolve(cfg.paths.resume_json)).is_file():
        secondary.append(("json_resume", from_json_resume(json.loads(p.read_text(encoding="utf-8")))))
    if cfg.paths.linkedin_export and (p := cfg.resolve(cfg.paths.linkedin_export)).exists():
        secondary.append(("linkedin_export", from_linkedin_export(p)))
    if (p := cfg.resolve(cfg.paths.resume_pdf)).is_file():
        try:
            secondary.append(("resume_pdf", from_resume_text(extract_pdf_text(p))))
        except Exception as exc:  # noqa: BLE001 - a bad PDF shouldn't block the build
            log(f"[yellow]Could not read resume PDF: {exc}[/]")
    else:
        log(f"[yellow]Resume PDF not found at {p} — uploads will fail until you add it.[/]")

    for name, extra in secondary:
        skills = extra.pop("skills", [])
        fill_missing(data, extra)
        data["skills"] = _union(data.get("skills") or [], skills)
        sources.append(name)

    if fetch_github and (user := github_username((data.get("links") or {}).get("github"))):
        try:
            gh = fetch_github_projects(user)
            known = {p.get("name", "").lower() for p in data.get("projects") or []}
            data["projects"] = (data.get("projects") or []) + [
                p for p in gh if p["name"].lower() not in known
            ]
            sources.append("github")
        except httpx.HTTPError as exc:
            log(f"[yellow]GitHub fetch failed ({exc}); continuing without it.[/]")

    data["skills"] = _union(yaml_skills, data.get("skills") or [])
    profile = UserProfile.model_validate(data)
    profile.sources = sources

    out = cfg.resolve(cfg.paths.user_profile_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(profile.model_dump_json(indent=2), encoding="utf-8")
    return profile


def load_profile(cfg: AppConfig) -> UserProfile:
    """Load user_profile.json (building it if needed) plus answers learned in earlier runs."""
    out = cfg.resolve(cfg.paths.user_profile_out)
    if out.is_file():
        profile = UserProfile.model_validate_json(out.read_text(encoding="utf-8"))
    else:
        profile = build_profile(cfg, fetch_github=False)
    learned = load_learned(cfg.resolve(cfg.paths.answers_learned))
    profile.answers = learned + profile.answers  # exact learned answers take priority
    return profile
