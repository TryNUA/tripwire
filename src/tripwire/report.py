"""Render captured telemetry into a ready-to-file markdown issue body."""

from __future__ import annotations

from tripwire.models import NetworkLogEntry, ReportStep, TelemetrySnapshot

_HIGHLIGHT_LEVELS = {"error", "warning"}


def render_report(
    steps: list[ReportStep],
    snapshot: TelemetrySnapshot,
    *,
    max_console_highlights: int = 10,
    max_failed_requests: int = 15,
) -> str:
    """Compose the report. Sections with nothing to say are omitted."""
    sections = [
        _steps_section(steps),
        _environment_section(snapshot),
        _console_section(snapshot, max_console_highlights),
        _network_section(snapshot, max_failed_requests),
    ]
    body = "\n\n".join(section for section in sections if section)
    return body + "\n" if body else ""


def _steps_section(steps: list[ReportStep]) -> str:
    if not steps:
        return ""
    lines = ["## Steps to reproduce"]
    for step in steps:
        if step.status == "failed":
            line = f"{step.order + 1}. **{step.description} — FAILED**"
            if step.note:
                line += f" — {step.note}"
        else:
            line = f"{step.order + 1}. {step.description}"
        lines.append(line)
    return "\n".join(lines)


def _environment_section(snapshot: TelemetrySnapshot) -> str:
    env = snapshot.environment
    rows: list[tuple[str, str]] = []
    if env.browser_version:
        rows.append(("Browser", env.browser_version))
    if env.user_agent:
        rows.append(("User agent", env.user_agent))
    if env.viewport_width and env.viewport_height:
        rows.append(("Viewport", f"{env.viewport_width}×{env.viewport_height}"))
    if env.target_url:
        rows.append(("Started at", env.target_url))
    if env.final_url:
        rows.append(("Ended at", env.final_url))
    rows.extend(sorted(env.extra.items()))
    if not rows:
        return ""
    lines = ["## Environment", "| | |", "|---|---|"]
    lines.extend(f"| {name} | {value} |" for name, value in rows)
    return "\n".join(lines)


def _console_section(snapshot: TelemetrySnapshot, max_highlights: int) -> str:
    highlights = [entry for entry in snapshot.console if entry.level in _HIGHLIGHT_LEVELS]
    if not highlights and not snapshot.console_dropped:
        return ""
    shown = highlights[-max_highlights:]
    lines = ["## Console errors"]
    if len(highlights) > len(shown):
        lines.append(f"_{len(highlights) - len(shown)} earlier errors/warnings not shown._")
    if snapshot.console_dropped:
        lines.append(f"_+{snapshot.console_dropped} earlier console entries dropped._")
    if shown:
        lines.append("```")
        for entry in shown:
            suffix = f" ({entry.source_url})" if entry.source_url else ""
            lines.append(f"[{entry.level}] {entry.text}{suffix}")
        lines.append("```")
    return "\n".join(lines)


def _network_section(snapshot: TelemetrySnapshot, max_requests: int) -> str:
    failures = [entry for entry in snapshot.network if entry.failed]
    if not failures:
        return ""
    shown = failures[:max_requests]
    lines = [
        "## Failed network requests",
        "| Method | URL | Status | Duration |",
        "|---|---|---|---|",
    ]
    for entry in shown:
        status = str(entry.status) if entry.status else (entry.error_text or "—")
        duration = f"{entry.duration_ms:g}ms" if entry.duration_ms else "—"
        lines.append(f"| {entry.method} | {entry.url} | {status} | {duration} |")
    if len(failures) > len(shown):
        lines.append(f"_{len(failures) - len(shown)} more failed requests not shown._")
    for entry in shown:
        details = _body_details(entry)
        if details:
            lines.append(details)
    return "\n".join(lines)


def _body_details(entry: NetworkLogEntry) -> str:
    parts: list[str] = []
    if entry.request_body:
        parts.append(f"Request body:\n\n```\n{entry.request_body}\n```")
    if entry.response_body:
        parts.append(f"Response body:\n\n```\n{entry.response_body}\n```")
    if not parts:
        return ""
    inner = "\n\n".join(parts)
    return (
        f"<details><summary>{entry.method} {entry.url}</summary>\n\n{inner}\n\n</details>"
    )
