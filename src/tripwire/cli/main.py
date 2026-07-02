"""``tripwire`` command line: watch / status / save.

``status`` and ``save`` are stdlib+pydantic only; ``watch`` needs the ``cli``
extra (websockets) and imports it lazily so the other commands work without it.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

from tripwire.cli import state
from tripwire.report import render_report

_INSTALL_HINT = "tripwire watch needs the cli extra: pip install 'tripwire[cli]'"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tripwire", description="Flight recorder for browsers.")
    sub = parser.add_subparsers(dest="command", required=True)

    watch = sub.add_parser("watch", help="attach to a Chromium over CDP and record telemetry")
    watch.add_argument("--cdp", default="", help="CDP http endpoint, e.g. http://127.0.0.1:9222")
    watch.add_argument("--launch", action="store_true", help="launch a Chromium to watch")
    watch.add_argument("--port", type=int, default=9222, help="debug port for --launch")
    watch.add_argument("--browser-path", default="", help="Chromium binary for --launch")

    sub.add_parser("status", help="new anomalies since the last status check")

    save = sub.add_parser("save", help="write the bug report to .tripwire/reports/")
    save.add_argument("--summary", required=True, help="short bug description")

    args = parser.parse_args(argv)
    if args.command == "watch":
        return _watch(args)
    if args.command == "status":
        return _status()
    return _save(args.summary)


def _watch(args: argparse.Namespace) -> int:
    if not args.cdp and not args.launch:
        print("watch needs --cdp URL or --launch", file=sys.stderr)
        return 2
    try:
        from tripwire.cli.watcher import run_watch
    except ImportError:
        print(_INSTALL_HINT, file=sys.stderr)
        return 1
    return run_watch(args)


def _status() -> int:
    session = state.read_session()
    if session is None:
        print("no tripwire session found — start `tripwire watch` first")
        return 1
    if not state.pid_alive(session.pid):
        print(f"warning: watcher (pid {session.pid}) is not running; data may be stale")
    marker = state.read_marker()
    errors = [
        e for e in state.new_console_entries(session, marker) if e.level in ("error", "warning")
    ]
    failures = [e for e in state.new_network_entries(session, marker) if e.failed]
    state.write_atomic(
        state.STATE_DIR / state.MARKER_FILE,
        state.StatusMarker(
            checked_at=time.time(),
            console_seen=len(session.snapshot.console) + session.snapshot.console_dropped,
            network_seen=len(session.snapshot.network) + session.snapshot.network_dropped,
        ),
    )
    if not errors and not failures:
        print("no new anomalies since last check")
        return 0
    print(
        f"{len(errors)} new console error(s)/warning(s), "
        f"{len(failures)} new failed request(s) since last check:"
    )
    for entry in errors:
        suffix = f" ({entry.source_url})" if entry.source_url else ""
        print(f"  [{entry.level}] {entry.text[:200]}{suffix}")
    for entry in failures:
        status = entry.status or entry.error_text or "failed"
        print(f"  {entry.method} {entry.url} → {status}")
    print("run `tripwire save --summary '...'` to capture a report")
    return 0


def _save(summary: str) -> int:
    session = state.read_session()
    if session is None:
        print("no tripwire session found — nothing to save", file=sys.stderr)
        return 1
    report = f"# {summary}\n\n" + render_report(session.steps, session.snapshot)
    reports_dir = state.STATE_DIR / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    name = f"{time.strftime('%Y%m%d-%H%M%S')}-{_slug(summary)}"
    md_path = reports_dir / f"{name}.md"
    md_path.write_text(report, encoding="utf-8")
    Path(reports_dir / f"{name}.json").write_text(
        session.model_dump_json(exclude={"pid", "cdp_http_url"}), encoding="utf-8"
    )
    print(md_path)
    return 0


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "report"


if __name__ == "__main__":
    sys.exit(main())
