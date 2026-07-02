"""External CDP watcher: feeds a running Chromium's telemetry into a recorder.

Attaches to every page target, injects the step observer, and continuously
persists state to ``.tripwire/session.json`` so ``status``/``save`` can read it
from other processes — even after the browser dies. Every CDP touch point is
exception-suppressed: broken telemetry must never break the browsing session.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib.resources
import json
import os
import signal
import sys
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

from tripwire.cli import state
from tripwire.cli.cdp import CDPClient, discover_ws_url
from tripwire.models import ReportStep
from tripwire.recorder import BODY_UNAVAILABLE, TelemetryRecorder, decode_body

BINDING = "__tripwire_binding"

_RECORDER_HANDLERS = {
    "Runtime.consoleAPICalled": "on_console_api_called",
    "Runtime.exceptionThrown": "on_exception_thrown",
    "Network.requestWillBeSent": "on_request_will_be_sent",
    "Network.responseReceived": "on_response_received",
    "Network.loadingFinished": "on_loading_finished",
    "Network.loadingFailed": "on_loading_failed",
}

_STEP_DESCRIPTIONS = {
    "click": "click {target}",
    "input": "type into {target}",
    "submit": "submit {target}",
}

# requestId → session_id for Network.getResponseBody routing (failed requests only).
_REQUEST_SESSION_CAP = 4096

_OBSERVER_JS = importlib.resources.files("tripwire.cli").joinpath("observer.js").read_text()

_PERSIST_INTERVAL = 2.0


class Watcher:
    """One Watcher per ``tripwire watch`` run; ``attach()`` per browser connection.

    The browser may not exist yet when the watcher starts, and may die and be
    relaunched mid-session — the recorder and on-disk state live across
    reconnects, per-connection bookkeeping does not.
    """

    def __init__(
        self,
        recorder: TelemetryRecorder,
        state_dir: Path,
        cdp_http_url: str,
    ) -> None:
        self.recorder = recorder
        self.client: CDPClient | None = None
        self.state_dir = state_dir
        self.cdp_http_url = cdp_http_url
        self.started_at = time.time()
        self.stop = asyncio.Event()
        self._sessions: set[str] = set()
        self._request_sessions: OrderedDict[str, str | None] = OrderedDict()
        self._dirty = False

    async def attach(self, client: CDPClient) -> None:
        self.client = client
        self._sessions.clear()
        self._request_sessions.clear()
        self.client.on_event(self.handle_event)
        await self.client.send(
            "Target.setAutoAttach",
            {"autoAttach": True, "waitForDebuggerOnStart": True, "flatten": True},
        )
        # Belt-and-braces for tabs that existed before setAutoAttach.
        with contextlib.suppress(Exception):
            targets = await self.client.send("Target.getTargets")
            for info in targets.get("targetInfos", []):
                if info.get("type") == "page" and not info.get("attached"):
                    with contextlib.suppress(Exception):
                        await self.client.send(
                            "Target.attachToTarget",
                            {"targetId": info["targetId"], "flatten": True},
                        )
        with contextlib.suppress(Exception):
            version = await self.client.send("Browser.getVersion")
            self.recorder.set_environment(
                user_agent=version.get("userAgent", ""),
                browser_version=version.get("product", ""),
            )
        self.persist()

    # ------------------------------------------------------------ event feed

    def handle_event(self, method: str, params: dict[str, Any], session_id: str | None) -> None:
        try:
            if method == "Target.attachedToTarget":
                self._on_attached(params)
            elif method == "Target.detachedFromTarget":
                self._sessions.discard(str(params.get("sessionId", "")))
            elif method == "Runtime.bindingCalled":
                self._on_binding(params)
            elif method == "Page.frameNavigated":
                self._on_frame_navigated(params)
            elif method in _RECORDER_HANDLERS:
                self._on_telemetry(method, params, session_id)
        except Exception:
            pass

    def _on_attached(self, params: dict[str, Any]) -> None:
        session_id = str(params.get("sessionId", ""))
        target_type = (params.get("targetInfo") or {}).get("type", "")
        if target_type != "page" or not session_id or session_id in self._sessions:
            return
        self._sessions.add(session_id)
        # Bind the current client: a task outliving a reconnect must talk to the
        # dead connection (harmless, suppressed), not the new one.
        asyncio.ensure_future(self._setup_session(self.client, session_id))

    async def _setup_session(self, client: CDPClient, session_id: str) -> None:
        send = client.send
        for method, params in (
            ("Runtime.enable", {}),
            ("Network.enable", {}),
            ("Page.enable", {}),
            ("Runtime.addBinding", {"name": BINDING}),
            ("Page.addScriptToEvaluateOnNewDocument", {"source": _OBSERVER_JS}),
            ("Runtime.evaluate", {"expression": _OBSERVER_JS}),  # current document
            ("Runtime.runIfWaitingForDebugger", {}),
        ):
            with contextlib.suppress(Exception):
                await send(method, params, session_id=session_id)

    def _on_binding(self, params: dict[str, Any]) -> None:
        if params.get("name") != BINDING:
            return
        message = json.loads(str(params.get("payload", "")))
        kind = message.get("kind", "")
        if kind == "navigate":
            self._navigate_step(str(message.get("url", "")))
        elif kind in _STEP_DESCRIPTIONS:
            self._open_step(_STEP_DESCRIPTIONS[kind].format(target=message.get("target", "?")))

    def _on_frame_navigated(self, params: dict[str, Any]) -> None:
        frame = params.get("frame") or {}
        if frame.get("parentId"):
            return
        self._navigate_step(str(frame.get("url", "")))

    def _navigate_step(self, url: str) -> None:
        if not url or url == "about:blank":
            return
        description = f"navigate to {url}"
        # Dedupe: an SPA navigate from the binding and Page.frameNavigated (or
        # repeated history events) may describe the same navigation.
        if self.recorder.steps and self.recorder.steps[-1].description == description:
            return
        self._open_step(description)
        self.recorder.set_environment(final_url=url)

    def _open_step(self, description: str) -> None:
        step = ReportStep(order=len(self.recorder.steps), description=description)
        self.recorder.steps.append(step)
        self.recorder.current_step_index = step.order
        self._dirty = True

    def _on_telemetry(self, method: str, params: dict[str, Any], session_id: str | None) -> None:
        if method == "Network.requestWillBeSent":
            request_id = params.get("requestId")
            if request_id:
                self._request_sessions[request_id] = session_id
                while len(self._request_sessions) > _REQUEST_SESSION_CAP:
                    self._request_sessions.popitem(last=False)
        getattr(self.recorder, _RECORDER_HANDLERS[method])(params)
        self._dirty = True
        if method in ("Network.loadingFinished", "Network.loadingFailed"):
            if self.recorder.pending_failed_request_ids():
                asyncio.ensure_future(self._fetch_failed_bodies(self.client))
        elif method == "Runtime.exceptionThrown":
            self.persist()  # anomalies hit disk immediately; the browser may die next

    async def _fetch_failed_bodies(self, client: CDPClient) -> None:
        for request_id in self.recorder.pending_failed_request_ids():
            try:
                result = await client.send(
                    "Network.getResponseBody",
                    {"requestId": request_id},
                    session_id=self._request_sessions.get(request_id),
                )
                self.recorder.set_response_body(request_id, decode_body(result))
            except Exception:
                self.recorder.set_response_body(request_id, BODY_UNAVAILABLE)
        self.persist()

    # ----------------------------------------------------------- persistence

    def persist(self) -> None:
        with contextlib.suppress(Exception):
            state.write_atomic(
                self.state_dir / state.SESSION_FILE,
                state.SessionState(
                    pid=os.getpid(),
                    cdp_http_url=self.cdp_http_url,
                    started_at=self.started_at,
                    updated_at=time.time(),
                    steps=self.recorder.steps,
                    snapshot=self.recorder.snapshot(flush_pending=False),
                ),
            )
        self._dirty = False

    async def run_until_disconnect(self) -> None:
        """Persist loop; returns when stopped or the CDP connection dies."""
        while not self.stop.is_set() and not (self.client is None or self.client.closed):
            with contextlib.suppress(asyncio.TimeoutError, TimeoutError):
                await asyncio.wait_for(self.stop.wait(), timeout=_PERSIST_INTERVAL)
            if self._dirty:
                self.persist()
        self.persist()


# ------------------------------------------------------------- entry point


def run_watch(args: Any) -> int:
    existing = state.read_session()
    if existing and state.pid_alive(existing.pid):
        print(f"another tripwire watcher is running (pid {existing.pid})", file=sys.stderr)
        return 1
    try:
        return asyncio.run(_watch(args))
    except KeyboardInterrupt:
        return 0


async def _watch(args: Any) -> int:
    browser = None
    if args.launch:
        from tripwire.cli.launch import LaunchedBrowser

        browser = LaunchedBrowser.launch(port=args.port, browser_path=args.browser_path)
        http_url = browser.http_url
    else:
        http_url = args.cdp

    watcher = Watcher(TelemetryRecorder(), state.STATE_DIR, http_url)
    print(f"tripwire: watching {http_url} — state in {state.STATE_DIR}/", flush=True)
    if browser is not None:
        print(
            f"tripwire: launched browser; connect agents via --cdp-endpoint {http_url}",
            flush=True,
        )

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, watcher.stop.set)

    # Claim the session file right away so concurrent starts (e.g. a hook
    # firing on parallel tool calls) see a live watcher and bail.
    watcher.persist()

    # The browser may not be up yet (a hook can start the watcher before the
    # agent's first browser tool runs) and may die and relaunch mid-session:
    # wait, attach, record until the connection drops, repeat.
    waiting_logged = False
    try:
        while not watcher.stop.is_set():
            try:
                ws_url = browser.ws_url if browser is not None else discover_ws_url(http_url)
                client = await CDPClient.connect(ws_url)
            except Exception:
                if not waiting_logged:
                    print(f"tripwire: waiting for a browser at {http_url}", flush=True)
                    waiting_logged = True
                await _wait(watcher.stop, _PERSIST_INTERVAL)
                continue
            waiting_logged = False
            await watcher.attach(client)
            await watcher.run_until_disconnect()
            with contextlib.suppress(Exception):
                await client.close()
            if browser is not None:
                break  # we own the launched browser; if it's gone, we're done
    finally:
        if browser is not None:
            browser.cleanup()
    return 0


async def _wait(stop: asyncio.Event, seconds: float) -> None:
    with contextlib.suppress(asyncio.TimeoutError, TimeoutError):
        await asyncio.wait_for(stop.wait(), timeout=seconds)
