"""On-disk session state shared between ``watch`` (writer) and ``status``/``save`` (readers).

Single writer per file, no locking: the watcher owns ``session.json``, the
``status`` command owns ``last_status.json``. "Since last check" diffing uses
monotonic totals (buffer length + dropped count), which survive ring-buffer
drops.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field

from tripwire.models import ConsoleLogEntry, NetworkLogEntry, ReportStep, TelemetrySnapshot

STATE_DIR = Path(".tripwire")
SESSION_FILE = "session.json"
MARKER_FILE = "last_status.json"


class SessionState(BaseModel):
    pid: int = 0
    cdp_http_url: str = ""
    started_at: float = 0.0
    updated_at: float = 0.0
    steps: list[ReportStep] = Field(default_factory=list)
    snapshot: TelemetrySnapshot = Field(default_factory=TelemetrySnapshot)


class StatusMarker(BaseModel):
    checked_at: float = 0.0
    console_seen: int = 0  # len(console) + console_dropped at last check
    network_seen: int = 0


def write_atomic(path: Path, model: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(model.model_dump_json(), encoding="utf-8")
    os.replace(tmp, path)


def read_session(state_dir: Path = STATE_DIR) -> SessionState | None:
    path = state_dir / SESSION_FILE
    if not path.exists():
        return None
    return SessionState.model_validate_json(path.read_text(encoding="utf-8"))


def read_marker(state_dir: Path = STATE_DIR) -> StatusMarker:
    path = state_dir / MARKER_FILE
    if not path.exists():
        return StatusMarker()
    return StatusMarker.model_validate_json(path.read_text(encoding="utf-8"))


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def new_console_entries(session: SessionState, marker: StatusMarker) -> list[ConsoleLogEntry]:
    return _tail(session.snapshot.console, session.snapshot.console_dropped, marker.console_seen)


def new_network_entries(session: SessionState, marker: StatusMarker) -> list[NetworkLogEntry]:
    return _tail(session.snapshot.network, session.snapshot.network_dropped, marker.network_seen)


def _tail(buffer: list, dropped: int, seen: int) -> list:
    new = len(buffer) + dropped - seen
    if new <= 0:
        return []
    return buffer[-min(new, len(buffer)) :]
