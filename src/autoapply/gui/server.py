"""Local HTTP API + static UI for the desktop app.

Bound to 127.0.0.1 only. Every /api call must carry the per-launch random token (header, or
query string for the event stream), and the Host header must be a loopback name, so other
websites open in your browser can't drive the app (CSRF / DNS rebinding).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import secrets
from dataclasses import asdict
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError
from rich.console import Console
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from .. import __version__
from ..automation.adapters import ADAPTERS
from ..config import AppConfig, load_config
from ..notify import Notifier
from ..store.db import JobStatus, JobStore
from ..workspace import is_initialized, template_text
from .bus import BusWriter, EventBus
from .hitl import GuiHumanInTheLoop

STATIC = Path(__file__).parent / "static"
STATIC_FILES = {"app.js": "text/javascript", "app.css": "text/css", "icon.svg": "image/svg+xml"}
AUTOMATED = {a.portal_type for a in ADAPTERS}
_MARKUP = re.compile(r"\[/?[a-z #_]*\]")
MAX_RESUME_BYTES = 15 * 1024 * 1024


class GuiState:
    def __init__(self, config_path: Path | None, token: str, bus: EventBus):
        self.config_path = config_path
        self.token = token
        self.bus = bus
        self.run_task: asyncio.Task[None] | None = None
        self.hitl: GuiHumanInTheLoop | None = None
        self.run_info: dict[str, Any] = {"state": "idle"}
        self.busy: set[str] = set()
        self.should_exit = asyncio.Event()
        self.closing = False

    def cfg(self) -> AppConfig:
        return load_config(self.config_path)

    def store(self, cfg: AppConfig) -> JobStore:
        store = JobStore(cfg.resolve(cfg.paths.database))
        store.on_change = lambda job_id, status: self.bus.publish(
            {"type": "job", "id": job_id, "status": str(status)}
        )
        return store

    @property
    def running(self) -> bool:
        return self.run_task is not None and not self.run_task.done()


# ---------------------------------------------------------------- helpers


def err(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def _job_dict(job) -> dict[str, Any]:
    d = asdict(job)
    d["status"] = str(job.status)
    d["automated"] = job.portal_type in AUTOMATED
    return d


def _validation_message(exc: ValidationError) -> str:
    lines = []
    for e in exc.errors()[:6]:
        loc = ".".join(str(p) for p in e["loc"]) or "(root)"
        lines.append(f"{loc}: {e['msg']}")
    return "\n".join(lines)


async def _json(request: Request) -> dict[str, Any]:
    try:
        data = await request.json()
    except (json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def create_app(state: GuiState) -> Starlette:
    bus = state.bus

    def guarded(handler):
        async def wrapper(request: Request) -> Response:
            host = (request.headers.get("host") or "").rsplit(":", 1)[0]
            if host not in ("127.0.0.1", "localhost"):
                return err("forbidden host", 403)
            supplied = request.headers.get("x-autoapply-token") or request.query_params.get("token", "")
            if not secrets.compare_digest(supplied, state.token):
                return err("invalid token", 403)
            return await handler(request)

        return wrapper

    # ------------------------------------------------------------ static

    async def index(request: Request) -> Response:
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})

    async def static(request: Request) -> Response:
        name = request.path_params["name"]
        if name not in STATIC_FILES:
            return Response(status_code=404)
        return FileResponse(
            STATIC / name, media_type=STATIC_FILES[name], headers={"Cache-Control": "no-store"}
        )

    # ------------------------------------------------------------ state & events

    async def get_state(request: Request) -> Response:
        cfg = state.cfg()
        return JSONResponse(
            {
                "version": __version__,
                "workspace": str(cfg.project_root),
                "initialized": is_initialized(cfg.project_root),
                "run": state.run_info,
                "prompts": list(state.hitl.pending.values()) if state.hitl else [],
                "log": list(bus.log_history),
                "defaults": {
                    "headless": cfg.browser.headless,
                    "dry_run": cfg.automation.dry_run,
                    "limit": cfg.rate_limits.max_applications_per_run,
                },
                "busy": sorted(state.busy),
            }
        )

    async def events(request: Request) -> Response:
        queue = bus.subscribe()

        async def stream():
            try:
                yield ": connected\n\n"
                while not state.closing:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15)
                    except TimeoutError:
                        yield ": ping\n\n"
                        continue
                    yield f"data: {json.dumps(event)}\n\n"
            finally:
                bus.unsubscribe(queue)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    # ------------------------------------------------------------ jobs

    async def list_jobs(request: Request) -> Response:
        cfg = state.cfg()
        with state.store(cfg) as store:
            jobs = [_job_dict(j) for j in store.list()]
        return JSONResponse({"jobs": jobs})

    async def set_job_status(request: Request) -> Response:
        data = await _json(request)
        ids = data.get("ids") or []
        try:
            target = JobStatus(data.get("status"))
        except ValueError:
            return err("unknown status")
        if target not in (JobStatus.QUEUED, JobStatus.DISCOVERED, JobStatus.SKIPPED):
            return err("status can only be set to queued, discovered or skipped")
        changed = 0
        with state.store(state.cfg()) as store:
            for job_id in ids:
                job = store.get(str(job_id))
                if not job or job.status in (JobStatus.IN_PROGRESS, JobStatus.APPLIED):
                    continue
                if job.status != target:
                    store.set_status(job.job_id, target)
                    changed += 1
        return JSONResponse({"changed": changed})

    async def add_job(request: Request) -> Response:
        from ..jobs.sources import manual_job

        data = await _json(request)
        url = str(data.get("url", "")).strip()
        company = str(data.get("company", "")).strip()
        role = str(data.get("role", "")).strip()
        if not re.match(r"^https?://", url):
            return err("Enter the posting's full URL (https://…)")
        if not company or not role:
            return err("Company and role are required")
        job = manual_job(url, company, role, str(data.get("location", "")).strip())
        with state.store(state.cfg()) as store:
            store.upsert(job)
            store.set_status(job.job_id, JobStatus.QUEUED, "added manually")
        bus.publish({"type": "jobs"})
        return JSONResponse({"job": _job_dict(job)})

    async def fetch_jobs(request: Request) -> Response:
        from ..jobs.filters import filter_postings
        from ..jobs.sources import build_sources, fetch_all

        if "fetch" in state.busy:
            return err("Already fetching", 409)
        cfg = state.cfg()
        sources = build_sources(cfg.search)
        if not sources:
            return err(
                "No job boards configured yet. Add Greenhouse board tokens or Lever company "
                "slugs under search → sources in Settings → config.yaml."
            )
        state.busy.add("fetch")
        try:
            bus.log(f"Fetching postings from {len(sources)} board(s)…")
            postings, errors = await fetch_all(sources)
            for name, message in errors.items():
                bus.log(f"{name}: {message}", "warning")
            matched = filter_postings(postings, cfg.search)
            with state.store(cfg) as store:
                new = sum(store.upsert(p.to_job()) for p in matched)
        finally:
            state.busy.discard("fetch")
        bus.log(f"Fetched {len(postings)} · matched your filters {len(matched)} · new {new}")
        bus.publish({"type": "jobs"})
        return JSONResponse({"fetched": len(postings), "matched": len(matched), "new": new, "errors": errors})

    async def job_log(request: Request) -> Response:
        job_id = request.query_params.get("id", "")
        with state.store(state.cfg()) as store:
            rows = [dict(r) for r in store.logs(job_id)]
        return JSONResponse({"log": rows})

    # ------------------------------------------------------------ run

    async def start_run(request: Request) -> Response:
        from ..automation.runner import Runner
        from ..profile.parser import load_profile

        if state.running:
            return err("A run is already in progress", 409)
        data = await _json(request)
        cfg = state.cfg()
        resume = data.get("mode") == "resume"
        headless = bool(data.get("headless", cfg.browser.headless)) and not resume
        if data.get("dry_run"):
            cfg.automation.dry_run = True
        limit = data.get("limit")
        limit = int(limit) if str(limit or "").isdigit() and int(limit) > 0 else None
        try:
            profile = await asyncio.to_thread(load_profile, cfg)
        except (FileNotFoundError, ValidationError, yaml.YAMLError) as exc:
            return err(f"Your profile isn't ready: {exc}")

        console = Console(
            file=BusWriter(bus), force_terminal=False, color_system=None, width=120, highlight=False
        )
        hitl = GuiHumanInTheLoop(Notifier(cfg.notifications), console, headless=headless, bus=bus)
        store = state.store(cfg)
        runner = Runner(
            cfg,
            profile,
            store,
            console,
            headless=headless,
            limit=limit,
            hitl=hitl,
            statuses=[JobStatus.PENDING_INPUT] if resume else [JobStatus.QUEUED],
        )
        state.hitl = hitl
        state.run_info = {
            "state": "running",
            "headless": headless,
            "dry_run": cfg.automation.dry_run,
            "mode": "resume" if resume else "queued",
        }
        bus.publish({"type": "run", "run": state.run_info})

        async def execute() -> None:
            summary: dict[str, int] = {}
            try:
                summary = dict(await runner.run())
            except asyncio.CancelledError:
                bus.log("Run stopped.", "warning")
            except Exception as exc:  # noqa: BLE001 - report, don't crash the server
                bus.log(f"Run failed: {type(exc).__name__}: {exc}", "error")
            finally:
                with contextlib.suppress(Exception):
                    console.file.flush()
                store.close()
                state.hitl = None
                state.run_info = {"state": "idle", "summary": summary}
                bus.publish({"type": "run", "run": state.run_info})
                bus.publish({"type": "jobs"})

        state.run_task = asyncio.create_task(execute())
        return JSONResponse({"run": state.run_info})

    async def stop_run(request: Request) -> Response:
        if not state.running:
            return err("Nothing is running", 409)
        assert state.run_task is not None
        state.run_task.cancel()
        return JSONResponse({"ok": True})

    async def answer_prompt(request: Request) -> Response:
        pid = request.path_params["pid"]
        if not state.hitl or not state.hitl.answer(pid, await _json(request)):
            return err("That prompt is no longer waiting", 404)
        return JSONResponse({"ok": True})

    # ------------------------------------------------------------ profile & settings

    def _file_path(cfg: AppConfig, name: str) -> Path | None:
        if name == "config":
            return cfg.project_root / "config" / "config.yaml"
        if name == "profile":
            return cfg.resolve(cfg.paths.profile)
        return None

    async def get_file(request: Request) -> Response:
        cfg = state.cfg()
        name = request.path_params["name"]
        path = _file_path(cfg, name)
        if path is None:
            return err("unknown file", 404)
        if path.is_file():
            text = path.read_text(encoding="utf-8")
        else:
            text = template_text("config" if name == "config" else "profile_data")
        return JSONResponse({"path": str(path), "text": text})

    async def put_file(request: Request) -> Response:
        from ..profile.models import UserProfile

        cfg = state.cfg()
        name = request.path_params["name"]
        path = _file_path(cfg, name)
        if path is None:
            return err("unknown file", 404)
        text = str((await _json(request)).get("text", ""))
        try:
            data = yaml.safe_load(text) or {}
        except yaml.YAMLError as exc:
            return err(f"YAML syntax error:\n{exc}")
        if not isinstance(data, dict):
            return err("The file must be a YAML mapping (key: value pairs).")
        try:
            (AppConfig if name == "config" else UserProfile).model_validate(data)
        except ValidationError as exc:
            return err("Not saved — please fix:\n" + _validation_message(exc))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        bus.log(f"Saved {path.name}")
        return JSONResponse({"ok": True, "path": str(path)})

    async def resume_info(request: Request) -> Response:
        cfg = state.cfg()
        path = cfg.resolve(cfg.paths.resume_pdf)
        if not path.is_file():
            return JSONResponse({"exists": False, "path": str(path)})
        st = path.stat()
        return JSONResponse({"exists": True, "path": str(path), "size": st.st_size, "mtime": st.st_mtime})

    async def upload_resume(request: Request) -> Response:
        body = await request.body()
        if len(body) > MAX_RESUME_BYTES:
            return err("That file is larger than 15 MB.")
        if not body.startswith(b"%PDF"):
            return err("Please choose a PDF file.")
        cfg = state.cfg()
        path = cfg.resolve(cfg.paths.resume_pdf)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        bus.log(f"Resume saved to {path}")
        return JSONResponse({"exists": True, "path": str(path), "size": len(body)})

    async def build_profile_route(request: Request) -> Response:
        from ..profile.parser import build_profile

        if "build" in state.busy:
            return err("Already building", 409)
        data = await _json(request)
        cfg = state.cfg()
        state.busy.add("build")

        def log(message: str) -> None:
            bus.publish_threadsafe({"type": "log", "text": _MARKUP.sub("", message), "level": "info"})

        try:
            profile = await asyncio.to_thread(
                build_profile, cfg, fetch_github=bool(data.get("github", True)), log=log
            )
        except (FileNotFoundError, ValidationError, yaml.YAMLError) as exc:
            message = _validation_message(exc) if isinstance(exc, ValidationError) else str(exc)
            return err(f"Couldn't build your profile:\n{message}")
        finally:
            state.busy.discard("build")
        edu = profile.primary_education
        summary = {
            "name": profile.personal.full_name,
            "email": profile.personal.email,
            "school": edu.school if edu else "",
            "experience": len(profile.experience),
            "projects": len(profile.projects),
            "skills": len(profile.skills),
            "sources": profile.sources,
        }
        bus.log(f"Profile built from {', '.join(profile.sources)}")
        return JSONResponse({"profile": summary})

    async def doctor(request: Request) -> Response:
        from ..diagnostics import run_checks

        checks = await asyncio.to_thread(run_checks, state.cfg())
        return JSONResponse({"checks": [asdict(c) for c in checks]})

    async def install_browser_route(request: Request) -> Response:
        from ..diagnostics import install_browser

        if "install" in state.busy:
            return err("Already installing", 409)
        state.busy.add("install")
        bus.log("Downloading Chromium (about 150 MB)…")
        try:
            rc = await asyncio.to_thread(
                install_browser,
                lambda line: bus.publish_threadsafe({"type": "log", "text": line, "level": "info"}),
            )
        finally:
            state.busy.discard("install")
        bus.log(
            "Chromium is ready." if rc == 0 else "Chromium download failed.", "info" if rc == 0 else "error"
        )
        return JSONResponse({"ok": rc == 0})

    async def quit_app(request: Request) -> Response:
        state.should_exit.set()
        return JSONResponse({"ok": True})

    g = guarded
    routes = [
        Route("/", index),
        Route("/static/{name}", static),
        Route("/api/state", g(get_state)),
        Route("/api/events", g(events)),
        Route("/api/jobs", g(list_jobs)),
        Route("/api/jobs/status", g(set_job_status), methods=["POST"]),
        Route("/api/jobs/add", g(add_job), methods=["POST"]),
        Route("/api/jobs/fetch", g(fetch_jobs), methods=["POST"]),
        Route("/api/jobs/log", g(job_log)),
        Route("/api/run", g(start_run), methods=["POST"]),
        Route("/api/run/stop", g(stop_run), methods=["POST"]),
        Route("/api/prompt/{pid}", g(answer_prompt), methods=["POST"]),
        Route("/api/files/{name}", g(get_file)),
        Route("/api/files/{name}", g(put_file), methods=["PUT"]),
        Route("/api/resume", g(resume_info)),
        Route("/api/resume", g(upload_resume), methods=["PUT"]),
        Route("/api/profile/build", g(build_profile_route), methods=["POST"]),
        Route("/api/doctor", g(doctor)),
        Route("/api/install-browser", g(install_browser_route), methods=["POST"]),
        Route("/api/quit", g(quit_app), methods=["POST"]),
    ]
    return Starlette(routes=routes)
