"""autoapply desktop app: a local server + a chromeless app window."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import secrets
import socket
import sys
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

from ..config import find_project_root, load_config
from ..workspace import create_workspace, is_initialized


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _ensure_stdio() -> None:
    """Started from a shortcut (autoapply-gui.exe / .desktop) there may be no console at all."""
    if sys.stdout is None or sys.stderr is None:
        log_dir = find_project_root() / "data" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        stream = open(log_dir / "gui.log", "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        sys.stdout = sys.stdout or stream
        sys.stderr = sys.stderr or stream


def _say(text: str) -> None:
    with contextlib.suppress(Exception):
        print(text, flush=True)


async def _self_test(base: str, token: str) -> int:
    def get(path: str, with_token: bool = True) -> tuple[int, bytes]:
        req = urllib.request.Request(base + path)
        if with_token:
            req.add_header("X-Autoapply-Token", token)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, b""

    results = []
    status, body = await asyncio.to_thread(get, "/")
    results.append(("UI page", status == 200 and b"autoapply" in body))
    status, body = await asyncio.to_thread(get, "/static/app.js")
    results.append(("UI script", status == 200 and len(body) > 1000))
    status, body = await asyncio.to_thread(get, "/api/state")
    results.append(("API with token", status == 200 and "version" in json.loads(body or b"{}")))
    status, _ = await asyncio.to_thread(get, "/api/state", False)
    results.append(("API rejects missing token", status == 403))
    for name, ok in results:
        _say(f"{'ok  ' if ok else 'FAIL'} {name}")
    return 0 if all(ok for _, ok in results) else 1


async def _serve(config: Path | None, open_window: bool, port: int, self_test: bool) -> int:
    import uvicorn

    from .bus import EventBus
    from .server import GuiState, create_app
    from .window import open_app_window

    bus = EventBus()
    bus.loop = asyncio.get_running_loop()
    state = GuiState(config, secrets.token_urlsafe(24), bus)

    cfg = state.cfg()
    if not is_initialized(cfg.project_root):
        for path in create_workspace(cfg.project_root):
            bus.log(f"Created {path}")

    port = port or _free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(state),
            host="127.0.0.1",
            port=port,
            log_level="warning",
            lifespan="off",
            timeout_graceful_shutdown=2,
            access_log=False,
        )
    )
    server_task = asyncio.create_task(server.serve())
    while not server.started:
        if server_task.done():
            _say(f"Could not start the local server on port {port}.")
            return 1
        await asyncio.sleep(0.05)

    base = f"http://127.0.0.1:{port}"
    url = f"{base}/?token={state.token}"
    window = None
    rc = 0
    try:
        if self_test:
            rc = await _self_test(base, state.token)
        else:
            if open_window:
                window = await open_app_window(url, state.cfg())
                if window is not None:
                    # A window that dies at once (sandbox/GPU/profile errors) must not quit the app.
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(asyncio.shield(window.wait()), timeout=3)
                        _say(f"The app window closed immediately (exit code {window.returncode}).")
                        window = None
                if window is None:
                    _say("Opening autoapply in your default browser instead.")
                    webbrowser.open(url)
            if window is None:
                _say(f"autoapply is running at {url}\nPress Ctrl+C (or Quit in the app) to stop.")
            waiters = [asyncio.create_task(state.should_exit.wait())]
            if window is not None:
                waiters.append(asyncio.create_task(window.wait()))
            _, pending = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
    finally:
        if state.run_task and not state.run_task.done():
            state.run_task.cancel()
            with contextlib.suppress(BaseException):
                await state.run_task
        state.closing = True
        server.should_exit = True
        with contextlib.suppress(BaseException):
            await server_task
        if window is not None and window.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                window.terminate()
    return rc


def launch(
    config: Path | None = None, open_window: bool = True, port: int = 0, self_test: bool = False
) -> int:
    _ensure_stdio()
    if config is not None:
        load_config(config)  # fail fast on a bad path/config
    try:
        return asyncio.run(_serve(config, open_window, port, self_test))
    except KeyboardInterrupt:
        return 0


def main() -> None:
    """Entry point for the `autoapply-gui` launcher (no console window on Windows)."""
    parser = argparse.ArgumentParser(prog="autoapply-gui", description="Open the autoapply app.")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--no-window", action="store_true")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    sys.exit(launch(args.config, not args.no_window, args.port, args.self_test))
