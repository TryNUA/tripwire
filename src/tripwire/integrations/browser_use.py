"""One-call browser-use wiring: ``await attach(recorder, browser_session)``.

Like the Playwright adapter, this is duck-typed — it never imports browser_use
or cdp_use. It only needs an object shaped like a browser-use
``BrowserSession`` (``.cdp_client`` with cdp-use's chained ``register`` /
``send`` API, ``get_or_create_cdp_session()``), which keeps browser-use out of
tripwire's dependencies and makes the adapter testable with stubs.

Written against browser-use ≥ 0.5 (the CDP-native rewrite). Every touch point
is guarded: browser-use awaits step hooks inside its agent loop and cdp-use
calls event handlers on its websocket reader task, so an exception here would
abort the user's agent run — broken telemetry must never cost the run itself.
"""

from __future__ import annotations

import contextlib
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any

from tripwire.models import ReportStep
from tripwire.recorder import BODY_UNAVAILABLE, TelemetryRecorder
from tripwire.recorder import decode_body as _decode_body

_EVENT_HANDLERS = {
    ("Runtime", "consoleAPICalled"): "on_console_api_called",
    ("Runtime", "exceptionThrown"): "on_exception_thrown",
    ("Network", "requestWillBeSent"): "on_request_will_be_sent",
    ("Network", "responseReceived"): "on_response_received",
    ("Network", "loadingFinished"): "on_loading_finished",
    ("Network", "loadingFailed"): "on_loading_failed",
}

# requestId → session_id entries kept for Network.getResponseBody routing.
# Only failed requests are ever looked up, so a generous cap is plenty.
_REQUEST_SESSION_CAP = 4096


async def attach(recorder: TelemetryRecorder, browser_session: Any) -> BrowserUseAttachment:
    """Wire ``recorder`` to a browser-use ``BrowserSession``.

    Registers all six CDP handlers on the root cdp-use client (registration is
    client-global, so events from every tab flow in), enables the Runtime and
    Network domains on the focused target, and captures environment info.

    Attach *before* the bug happens — history is not retroactive. Call
    ``await attachment.fetch_failed_bodies()`` before ``recorder.report()``.
    """
    attachment = BrowserUseAttachment(recorder, browser_session)
    register = browser_session.cdp_client.register
    for (domain, event), handler_name in _EVENT_HANDLERS.items():
        getattr(getattr(register, domain), event)(
            attachment._make_handler(domain, event, getattr(recorder, handler_name))
        )

    # browser-use enables these on targets it attaches for its own use; enable
    # explicitly on the focused target so we don't depend on that.
    with contextlib.suppress(Exception):
        session = await browser_session.get_or_create_cdp_session()
        send = session.cdp_client.send
        with contextlib.suppress(Exception):
            await send.Runtime.enable(session_id=session.session_id)
        with contextlib.suppress(Exception):
            await send.Network.enable(session_id=session.session_id)

    with contextlib.suppress(Exception):
        version = await browser_session.cdp_client.send.Browser.getVersion()
        recorder.set_environment(
            user_agent=version.get("userAgent", ""),
            browser_version=version.get("product", ""),
        )
    with contextlib.suppress(Exception):
        viewport = browser_session.browser_profile.viewport
        if viewport:
            recorder.set_environment(
                viewport_width=viewport["width"], viewport_height=viewport["height"]
            )
    # target_url is left unset — the recorder derives it from the first
    # document request, since the agent usually hasn't navigated yet.
    return attachment


class BrowserUseAttachment:
    """Handle returned by :func:`attach`; routes failure-body fetches."""

    def __init__(self, recorder: TelemetryRecorder, browser_session: Any) -> None:
        self.recorder = recorder
        self.browser_session = browser_session
        # Network.getResponseBody is session-scoped in cdp-use, so remember
        # which session each request arrived on.
        self._request_sessions: OrderedDict[str, Any] = OrderedDict()

    async def fetch_failed_bodies(self) -> None:
        """Fetch response bodies for failed requests; finalize environment.

        Async counterpart of the automatic fetch the Playwright adapter does
        inside ``snapshot()``. Call before ``report()`` — and before the
        browser session is stopped (``agent.run()`` tears the browser down on
        completion, and CDP response bodies die with it). Safe to call after
        every step; already-fetched bodies are never re-requested.
        """
        pending = self.recorder.pending_failed_request_ids()
        if pending:
            try:
                send = self.browser_session.cdp_client.send
            except Exception:
                send = None
            for request_id in pending:
                if send is None:
                    self.recorder.set_response_body(request_id, BODY_UNAVAILABLE)
                    continue
                try:
                    result = await send.Network.getResponseBody(
                        params={"requestId": request_id},
                        session_id=self._request_sessions.get(request_id),
                    )
                except Exception:
                    self.recorder.set_response_body(request_id, BODY_UNAVAILABLE)
                    continue
                self.recorder.set_response_body(request_id, _decode_body(result))
        with contextlib.suppress(Exception):
            url = await self.browser_session.get_current_page_url()
            if url and url != "about:blank":
                self.recorder.set_environment(final_url=url)

    def _make_handler(
        self, domain: str, event: str, recorder_handler: Callable[[dict[str, Any]], None]
    ) -> Callable[..., None]:
        track_session = (domain, event) == ("Network", "requestWillBeSent")

        def handle(
            payload: dict[str, Any], session_id: Any = None, *args: Any, **kwargs: Any
        ) -> None:
            try:
                if track_session:
                    request_id = payload.get("requestId")
                    if request_id:
                        self._request_sessions[request_id] = session_id
                        while len(self._request_sessions) > _REQUEST_SESSION_CAP:
                            self._request_sessions.popitem(last=False)
                recorder_handler(payload)
            except Exception:
                pass

        return handle


def step_hooks(
    recorder: TelemetryRecorder,
) -> tuple[Callable[[Any], Awaitable[None]], Callable[[Any], Awaitable[None]]]:
    """Build ``(on_step_start, on_step_end)`` for ``Agent.run(...)``.

    ``on_step_start`` opens a repro step so telemetry is attributed to it;
    ``on_step_end`` rewrites its description from what the agent actually did
    (action names + goal from ``agent.history``) and marks it failed if any
    action result carried an error.
    """
    open_step: list[ReportStep] = []

    async def on_step_start(agent: Any) -> None:
        with contextlib.suppress(Exception):
            step = ReportStep(
                order=len(recorder.steps), description=f"step {len(recorder.steps) + 1}"
            )
            recorder.steps.append(step)
            recorder.current_step_index = step.order
            open_step[:] = [step]

    async def on_step_end(agent: Any) -> None:
        with contextlib.suppress(Exception):
            if not open_step:
                return
            _finish_step(open_step.pop(), agent)

    return on_step_start, on_step_end


def _finish_step(step: ReportStep, agent: Any) -> None:
    history = getattr(getattr(agent, "history", None), "history", None) or []
    if not history:
        return
    item = history[-1]
    model_output = getattr(item, "model_output", None)
    if model_output is not None:
        description = _describe(model_output)
        if description:
            step.description = description
    errors = [
        error
        for error in (getattr(r, "error", None) for r in getattr(item, "result", None) or [])
        if error
    ]
    if errors:
        step.status = "failed"
        if not step.note:
            step.note = str(errors[0])
    elif not step.status:
        step.status = "passed"


def _describe(model_output: Any) -> str:
    actions: list[str] = []
    for action in getattr(model_output, "action", None) or []:
        with contextlib.suppress(Exception):
            actions.extend(str(name) for name in action.model_dump(exclude_unset=True))
    goal = str(getattr(model_output, "next_goal", None) or "")
    if actions and goal:
        return f"{', '.join(actions)} — {goal}"
    return ", ".join(actions) or goal

