"""Data shapes for captured telemetry and rendered reports."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ConsoleLogEntry(BaseModel):
    ts: float = 0.0  # epoch seconds
    level: str = "log"  # log | debug | info | warning | error
    text: str = ""
    source_url: str = ""
    step_index: int = 0


class NetworkLogEntry(BaseModel):
    ts: float = 0.0  # epoch seconds (wall clock at request start)
    method: str = ""
    url: str = ""
    status: int = 0  # 0 = never completed
    resource_type: str = ""
    duration_ms: float = 0.0
    failed: bool = False  # loadingFailed or status >= 400
    error_text: str = ""
    request_body: str = ""  # failures only, redacted, capped
    response_body: str = ""  # failures only, redacted, capped
    step_index: int = 0


class Environment(BaseModel):
    user_agent: str = ""
    browser_version: str = ""
    viewport_width: int = 0
    viewport_height: int = 0
    target_url: str = ""
    final_url: str = ""
    extra: dict[str, str] = Field(default_factory=dict)


class TelemetrySnapshot(BaseModel):
    console: list[ConsoleLogEntry] = Field(default_factory=list)
    network: list[NetworkLogEntry] = Field(default_factory=list)
    environment: Environment = Field(default_factory=Environment)
    console_dropped: int = 0
    network_dropped: int = 0


class ReportStep(BaseModel):
    order: int
    description: str
    status: str = ""  # passed | failed | ""
    note: str = ""


class RecorderConfig(BaseModel):
    max_console_entries: int = 1000
    max_network_entries: int = 2000
    console_text_limit: int = 2000
    body_limit: int = 4096
    redact_key_pattern: str = r"token|key|secret|password|code|session|auth"
    extra_secret_values: list[str] = Field(default_factory=list)
    capture_bodies_on_failure: bool = True
