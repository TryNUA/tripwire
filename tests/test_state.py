import os

from tripwire.cli import state
from tripwire.models import ConsoleLogEntry, NetworkLogEntry, TelemetrySnapshot


def session_with(console=0, network=0, console_dropped=0, network_dropped=0, pid=0):
    return state.SessionState(
        pid=pid,
        snapshot=TelemetrySnapshot(
            console=[ConsoleLogEntry(text=f"c{i}", level="error") for i in range(console)],
            network=[
                NetworkLogEntry(url=f"https://x.dev/{i}", failed=True) for i in range(network)
            ],
            console_dropped=console_dropped,
            network_dropped=network_dropped,
        ),
    )


class TestAtomicIO:
    def test_round_trip(self, tmp_path):
        path = tmp_path / "session.json"
        state.write_atomic(path, session_with(console=2, pid=42))
        loaded = state.SessionState.model_validate_json(path.read_text())
        assert loaded.pid == 42
        assert [e.text for e in loaded.snapshot.console] == ["c0", "c1"]

    def test_write_creates_parent_dirs_and_leaves_no_tmp(self, tmp_path):
        path = tmp_path / "nested" / "session.json"
        state.write_atomic(path, state.StatusMarker())
        assert path.exists()
        assert list(path.parent.glob("*.tmp")) == []

    def test_read_missing_files(self, tmp_path):
        assert state.read_session(tmp_path) is None
        assert state.read_marker(tmp_path) == state.StatusMarker()


class TestAnomalyDiff:
    def test_unseen_entries_are_returned(self):
        session = session_with(console=3, network=2)
        marker = state.StatusMarker(console_seen=1, network_seen=0)
        assert [e.text for e in state.new_console_entries(session, marker)] == ["c1", "c2"]
        assert len(state.new_network_entries(session, marker)) == 2

    def test_nothing_new(self):
        session = session_with(console=2)
        marker = state.StatusMarker(console_seen=2)
        assert state.new_console_entries(session, marker) == []

    def test_dropped_entries_count_toward_totals(self):
        # 5 total ever seen (2 dropped + 3 buffered); 3 seen at last check → 2 new.
        session = session_with(console=3, console_dropped=2)
        marker = state.StatusMarker(console_seen=3)
        assert [e.text for e in state.new_console_entries(session, marker)] == ["c1", "c2"]

    def test_more_new_than_buffered_clamps_to_buffer(self):
        session = session_with(console=2, console_dropped=10)
        marker = state.StatusMarker(console_seen=0)
        assert len(state.new_console_entries(session, marker)) == 2


class TestPidAlive:
    def test_own_pid_is_alive(self):
        assert state.pid_alive(os.getpid()) is True

    def test_bogus_pid_is_dead(self):
        assert state.pid_alive(2**22 + 12345) is False
        assert state.pid_alive(0) is False
