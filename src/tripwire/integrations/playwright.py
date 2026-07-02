"""One-line Playwright wiring: ``attach(recorder, page)``.

The adapter is duck-typed — it never imports playwright, it only needs an
object shaped like a Playwright ``Page`` (``page.context.new_cdp_session``,
``page.url``, ``page.viewport_size``). That keeps playwright out of tripwire's
dependencies and makes the adapter trivially testable with stubs.
"""

from __future__ import annotations

import contextlib
from typing import Any

from tripwire.recorder import BODY_UNAVAILABLE, TelemetryRecorder
from tripwire.recorder import decode_body as _decode_body

_EVENT_HANDLERS = {
    "Runtime.consoleAPICalled": "on_console_api_called",
    "Runtime.exceptionThrown": "on_exception_thrown",
    "Network.requestWillBeSent": "on_request_will_be_sent",
    "Network.responseReceived": "on_response_received",
    "Network.loadingFinished": "on_loading_finished",
    "Network.loadingFailed": "on_loading_failed",
}


def attach(recorder: TelemetryRecorder, page: Any) -> Any:
    """Wire ``recorder`` to a **sync** Playwright page. Returns the CDP session.

    Does everything the raw-handler path does by hand: opens a CDP session,
    enables the Runtime and Network domains, registers all six handlers,
    captures environment info, and arranges for failed-request response bodies
    to be fetched automatically when you call ``snapshot()`` / ``report()``.

    Attach *before* the bug happens — history is not retroactive.
    """
    cdp = page.context.new_cdp_session(page)
    cdp.send("Runtime.enable")
    cdp.send("Network.enable")
    _register(recorder, cdp)

    with contextlib.suppress(Exception):
        version = cdp.send("Browser.getVersion")
        recorder.set_environment(
            user_agent=version.get("userAgent", ""),
            browser_version=version.get("product", ""),
        )
    _capture_page_environment(recorder, page, target=True)

    def _on_snapshot(rec: TelemetryRecorder) -> None:
        _capture_page_environment(rec, page, target=False)
        if not rec.config.capture_bodies_on_failure:
            return
        for request_id in rec.pending_failed_request_ids():
            rec.set_response_body(request_id, _fetch_body_sync(cdp, request_id))

    recorder.snapshot_hooks.append(_on_snapshot)
    return cdp


async def attach_async(recorder: TelemetryRecorder, page: Any) -> Any:
    """Wire ``recorder`` to an **async** Playwright page. Returns the CDP session.

    Same as :func:`attach`, except failed-request bodies cannot be fetched from
    inside the synchronous ``snapshot()`` — call ``await fetch_failed_bodies(
    recorder, cdp)`` before ``report()`` / ``snapshot()``.
    """
    cdp = await page.context.new_cdp_session(page)
    await cdp.send("Runtime.enable")
    await cdp.send("Network.enable")
    _register(recorder, cdp)

    with contextlib.suppress(Exception):
        version = await cdp.send("Browser.getVersion")
        recorder.set_environment(
            user_agent=version.get("userAgent", ""),
            browser_version=version.get("product", ""),
        )
    _capture_page_environment(recorder, page, target=True)
    recorder.snapshot_hooks.append(
        lambda rec: _capture_page_environment(rec, page, target=False)
    )
    return cdp


async def fetch_failed_bodies(recorder: TelemetryRecorder, cdp: Any) -> None:
    """Async counterpart of the automatic body fetch done by :func:`attach`."""
    for request_id in recorder.pending_failed_request_ids():
        try:
            result = await cdp.send("Network.getResponseBody", {"requestId": request_id})
        except Exception:
            recorder.set_response_body(request_id, BODY_UNAVAILABLE)
            continue
        recorder.set_response_body(request_id, _decode_body(result))


def _register(recorder: TelemetryRecorder, cdp: Any) -> None:
    for event, handler in _EVENT_HANDLERS.items():
        cdp.on(event, getattr(recorder, handler))


def _capture_page_environment(recorder: TelemetryRecorder, page: Any, *, target: bool) -> None:
    with contextlib.suppress(Exception):
        fields: dict[str, Any] = {}
        # attach() usually runs before the first navigation, when page.url is
        # still about:blank — leave target_url unset and let the recorder derive
        # it from the first document request instead.
        if page.url != "about:blank":
            fields["target_url" if target else "final_url"] = page.url
        viewport = page.viewport_size
        if viewport:
            fields["viewport_width"] = viewport["width"]
            fields["viewport_height"] = viewport["height"]
        if fields:
            recorder.set_environment(**fields)


def _fetch_body_sync(cdp: Any, request_id: str) -> str:
    try:
        result = cdp.send("Network.getResponseBody", {"requestId": request_id})
    except Exception:
        return BODY_UNAVAILABLE
    return _decode_body(result)
