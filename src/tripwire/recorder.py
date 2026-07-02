"""Transport-agnostic telemetry recorder.

The handlers consume raw Chrome DevTools Protocol event payloads (plain dicts),
so the recorder works with any CDP client: Playwright CDP sessions, cdp-use,
pychrome, a raw websocket. Attach the handlers *before* the bug happens —
console and network history cannot be reconstructed retroactively.
"""

from __future__ import annotations

import contextlib
import json
from collections import OrderedDict
from collections.abc import Callable, Iterator
from typing import Any

from tripwire.models import (
    ConsoleLogEntry,
    Environment,
    NetworkLogEntry,
    RecorderConfig,
    ReportStep,
    TelemetrySnapshot,
)
from tripwire.redaction import redact_body, redact_text, redact_url
from tripwire.report import render_report

BODY_UNAVAILABLE = "body_unavailable"

_CONSOLE_LEVELS = {"log", "debug", "info", "warning", "error"}


class TelemetryRecorder:
    """Collects console and network telemetry into bounded, redacted buffers."""

    def __init__(self, config: RecorderConfig | None = None) -> None:
        self.config = config or RecorderConfig()
        self.current_step_index = 0
        self.steps: list[ReportStep] = []
        # Hooks run at the start of snapshot(); integrations use them to fetch
        # failure bodies and finalize environment info. Exceptions are suppressed —
        # a broken hook must never cost the telemetry itself.
        self.snapshot_hooks: list[Callable[[TelemetryRecorder], None]] = []
        self._console: list[ConsoleLogEntry] = []
        self._network: list[NetworkLogEntry] = []
        self._console_dropped = 0
        self._network_dropped = 0
        self._pending: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._awaiting_body: OrderedDict[str, NetworkLogEntry] = OrderedDict()
        self._environment = Environment()

    # ------------------------------------------------------------------ steps

    @contextlib.contextmanager
    def step(self, description: str) -> Iterator[ReportStep]:
        """Record one agent action; telemetry emitted inside is attributed to it."""
        step = ReportStep(order=len(self.steps), description=description)
        self.steps.append(step)
        self.current_step_index = step.order
        try:
            yield step
        except Exception as exc:
            step.status = "failed"
            if not step.note:
                step.note = str(exc)
            raise
        else:
            if not step.status:
                step.status = "passed"

    # -------------------------------------------------------- console (Runtime)

    def on_console_api_called(self, event: dict[str, Any]) -> None:
        """Handle ``Runtime.consoleAPICalled``."""
        text = " ".join(_remote_object_text(arg) for arg in event.get("args", []))
        self._push_console(
            ConsoleLogEntry(
                ts=float(event.get("timestamp", 0.0)) / 1000.0,
                level=_console_level(str(event.get("type", "log"))),
                text=redact_text(
                    text, self.config.extra_secret_values, self.config.console_text_limit
                ),
                source_url=_top_frame_url(event.get("stackTrace")),
                step_index=self.current_step_index,
            )
        )

    def on_exception_thrown(self, event: dict[str, Any]) -> None:
        """Handle ``Runtime.exceptionThrown`` (uncaught JS errors)."""
        details = event.get("exceptionDetails") or {}
        exception = details.get("exception") or {}
        text = exception.get("description") or details.get("text") or ""
        self._push_console(
            ConsoleLogEntry(
                ts=float(event.get("timestamp", 0.0)) / 1000.0,
                level="error",
                text=redact_text(
                    text, self.config.extra_secret_values, self.config.console_text_limit
                ),
                source_url=details.get("url") or _top_frame_url(details.get("stackTrace")),
                step_index=self.current_step_index,
            )
        )

    # -------------------------------------------------------- network (Network)

    def on_request_will_be_sent(self, event: dict[str, Any]) -> None:
        """Handle ``Network.requestWillBeSent``."""
        request_id = event.get("requestId")
        if not request_id:
            return
        request = event.get("request") or {}
        self._pending[request_id] = {
            "wall_ts": float(event.get("wallTime", 0.0)),
            "mono_ts": float(event.get("timestamp", 0.0)),
            "method": str(request.get("method", "")),
            "url": redact_url(str(request.get("url", "")), self.config.redact_key_pattern),
            "resource_type": str(event.get("type", "")),
            "post_data": str(request.get("postData", "")),
            "status": 0,
            "step_index": self.current_step_index,
        }

    def on_response_received(self, event: dict[str, Any]) -> None:
        """Handle ``Network.responseReceived``."""
        pending = self._pending.get(event.get("requestId", ""))
        if pending is None:
            return
        response = event.get("response") or {}
        pending["status"] = int(response.get("status") or 0)

    def on_loading_finished(self, event: dict[str, Any]) -> None:
        """Handle ``Network.loadingFinished``."""
        request_id = str(event.get("requestId", ""))
        pending = self._pending.pop(request_id, None)
        if pending is None:
            return
        failed = pending["status"] >= 400
        self._flush_network(request_id, pending, float(event.get("timestamp", 0.0)), failed, "")

    def on_loading_failed(self, event: dict[str, Any]) -> None:
        """Handle ``Network.loadingFailed``."""
        request_id = str(event.get("requestId", ""))
        pending = self._pending.pop(request_id, None)
        if pending is None:
            return
        error_text = str(event.get("errorText", ""))
        end_ts = float(event.get("timestamp", 0.0))
        self._flush_network(request_id, pending, end_ts, True, error_text)

    # ---------------------------------------------------------- failure bodies

    def pending_failed_request_ids(self) -> list[str]:
        """Request ids of failed requests whose response body has not been supplied."""
        return list(self._awaiting_body)

    def set_response_body(self, request_id: str, body: str) -> None:
        """Attach the (consumer-fetched) response body of a failed request."""
        entry = self._awaiting_body.pop(request_id, None)
        if entry is None:
            return
        if body == BODY_UNAVAILABLE:
            entry.response_body = BODY_UNAVAILABLE
            return
        entry.response_body = redact_body(
            body,
            self.config.redact_key_pattern,
            self.config.extra_secret_values,
            self.config.body_limit,
        )

    # ------------------------------------------------------------- environment

    def set_environment(self, **fields: Any) -> None:
        """Update environment info; unknown keys land in ``environment.extra``."""
        data = self._environment.model_dump()
        extra: dict[str, str] = dict(data.pop("extra"))
        extra_update = fields.pop("extra", None)
        for key, value in fields.items():
            if key in data:
                data[key] = value
            else:
                extra[key] = str(value)
        if extra_update:
            extra.update({key: str(value) for key, value in extra_update.items()})
        self._environment = Environment(**data, extra=extra)

    # ------------------------------------------------------------------ output

    def snapshot(self) -> TelemetrySnapshot:
        """The full captured record. Runs hooks, then flushes unfinished requests."""
        for hook in list(self.snapshot_hooks):
            with contextlib.suppress(Exception):
                hook(self)
        while self._pending:
            request_id, pending = self._pending.popitem(last=False)
            self._flush_network(request_id, pending, 0.0, False, "")
        if not self._environment.target_url:
            first_document = next(
                (e for e in self._network if e.resource_type.lower() == "document"), None
            )
            if first_document is not None:
                self._environment.target_url = first_document.url
        return TelemetrySnapshot(
            console=list(self._console),
            network=list(self._network),
            environment=self._environment.model_copy(deep=True),
            console_dropped=self._console_dropped,
            network_dropped=self._network_dropped,
        )

    def report(self, *, max_console_highlights: int = 10, max_failed_requests: int = 15) -> str:
        """Render the ready-to-file markdown issue body."""
        return render_report(
            self.steps,
            self.snapshot(),
            max_console_highlights=max_console_highlights,
            max_failed_requests=max_failed_requests,
        )

    # --------------------------------------------------------------- internals

    def _push_console(self, entry: ConsoleLogEntry) -> None:
        self._console.append(entry)
        if len(self._console) > self.config.max_console_entries:
            del self._console[0]
            self._console_dropped += 1

    def _flush_network(
        self,
        request_id: str,
        pending: dict[str, Any],
        end_mono_ts: float,
        failed: bool,
        error_text: str,
    ) -> None:
        duration_ms = 0.0
        if end_mono_ts > 0 and pending["mono_ts"] > 0:
            duration_ms = round((end_mono_ts - pending["mono_ts"]) * 1000.0, 1)
        entry = NetworkLogEntry(
            ts=pending["wall_ts"],
            method=pending["method"],
            url=pending["url"],
            status=pending["status"],
            resource_type=pending["resource_type"],
            duration_ms=duration_ms,
            failed=failed,
            error_text=error_text,
            step_index=pending["step_index"],
        )
        if failed:
            if pending["post_data"]:
                entry.request_body = redact_body(
                    pending["post_data"],
                    self.config.redact_key_pattern,
                    self.config.extra_secret_values,
                    self.config.body_limit,
                )
            if self.config.capture_bodies_on_failure:
                self._awaiting_body[request_id] = entry
        self._network.append(entry)
        if len(self._network) > self.config.max_network_entries:
            dropped = self._network[0]
            del self._network[0]
            self._network_dropped += 1
            self._awaiting_body = OrderedDict(
                (rid, e) for rid, e in self._awaiting_body.items() if e is not dropped
            )


def _console_level(cdp_type: str) -> str:
    if cdp_type in _CONSOLE_LEVELS:
        return cdp_type
    if cdp_type == "assert":
        return "error"
    return "log"


def _remote_object_text(obj: dict[str, Any]) -> str:
    if "value" in obj:
        value = obj["value"]
        if isinstance(value, str):
            return value
        return json.dumps(value, default=str)
    return str(obj.get("description", ""))


def _top_frame_url(stack_trace: dict[str, Any] | None) -> str:
    frames = (stack_trace or {}).get("callFrames") or []
    if frames:
        return str(frames[0].get("url", ""))
    return ""
