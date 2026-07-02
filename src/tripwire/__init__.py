"""tripwire — rich bug reports for AI browser agents."""

from tripwire.models import (
    ConsoleLogEntry,
    Environment,
    NetworkLogEntry,
    RecorderConfig,
    ReportStep,
    TelemetrySnapshot,
)
from tripwire.recorder import BODY_UNAVAILABLE, TelemetryRecorder
from tripwire.report import render_report

__version__ = "0.1.0"

__all__ = [
    "BODY_UNAVAILABLE",
    "ConsoleLogEntry",
    "Environment",
    "NetworkLogEntry",
    "RecorderConfig",
    "ReportStep",
    "TelemetryRecorder",
    "TelemetrySnapshot",
    "render_report",
]
