import os

import pytest

from tripwire.cli import state
from tripwire.cli.main import main
from tripwire.models import (
    ConsoleLogEntry,
    Environment,
    NetworkLogEntry,
    ReportStep,
    TelemetrySnapshot,
)


@pytest.fixture(autouse=True)
def in_tmp_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


def write_session(pid=None):
    session = state.SessionState(
        pid=pid if pid is not None else os.getpid(),
        steps=[ReportStep(order=0, description="click button#pay 'Pay now'")],
        snapshot=TelemetrySnapshot(
            console=[
                ConsoleLogEntry(level="error", text="Uncaught TypeError", source_url="app.js")
            ],
            network=[
                NetworkLogEntry(
                    method="POST", url="https://x.dev/api/orders", status=500, failed=True
                ),
                NetworkLogEntry(method="GET", url="https://x.dev/ok", status=200),
            ],
            environment=Environment(browser_version="Chrome/138"),
        ),
    )
    state.write_atomic(state.STATE_DIR / state.SESSION_FILE, session)


class TestStatus:
    def test_no_session(self, capsys):
        assert main(["status"]) == 1
        assert "no tripwire session" in capsys.readouterr().out

    def test_reports_new_anomalies_then_goes_quiet(self, capsys):
        write_session()
        assert main(["status"]) == 0
        out = capsys.readouterr().out
        assert "1 new console error(s)/warning(s), 1 new failed request(s)" in out
        assert "Uncaught TypeError" in out
        assert "POST https://x.dev/api/orders → 500" in out
        assert "https://x.dev/ok" not in out
        assert main(["status"]) == 0
        assert "no new anomalies" in capsys.readouterr().out

    def test_warns_when_watcher_dead(self, capsys):
        write_session(pid=2**22 + 12345)
        main(["status"])
        assert "not running" in capsys.readouterr().out


class TestSave:
    def test_no_session(self, capsys):
        assert main(["save", "--summary", "boom"]) == 1

    def test_writes_report_and_snapshot(self, capsys):
        write_session()
        assert main(["save", "--summary", "Checkout 500 on confirm"]) == 0
        md_path = state.STATE_DIR / "reports" / capsys.readouterr().out.strip().split("/")[-1]
        report = md_path.read_text()
        assert report.startswith("# Checkout 500 on confirm\n")
        assert "click button#pay 'Pay now'" in report
        assert "POST | https://x.dev/api/orders | 500" in report
        json_path = md_path.with_suffix(".json")
        assert '"Uncaught TypeError"' in json_path.read_text()
        assert "checkout-500-on-confirm" in md_path.name


class TestWatchWithoutExtra:
    def test_needs_cdp_or_launch(self, capsys):
        assert main(["watch"]) == 2

    def test_missing_websockets_prints_hint(self, capsys, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name.startswith("tripwire.cli.watcher") or name == "websockets":
                raise ImportError(name)
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        assert main(["watch", "--launch"]) == 1
        assert "pip install 'tripwire[cli]'" in capsys.readouterr().err
