from tripwire import ConsoleLogEntry, NetworkLogEntry, TelemetrySnapshot


def test_snapshot_json_round_trip():
    snapshot = TelemetrySnapshot(
        console=[ConsoleLogEntry(level="error", text="boom", step_index=2)],
        network=[NetworkLogEntry(method="GET", url="https://x.dev", status=404, failed=True)],
        console_dropped=3,
    )
    restored = TelemetrySnapshot.model_validate_json(snapshot.model_dump_json())
    assert restored == snapshot
