import pytest

from tripwire import BODY_UNAVAILABLE, RecorderConfig, TelemetryRecorder


def console_event(text="hello", level="log", ts_ms=1700000000000.0, url="app.js"):
    return {
        "type": level,
        "timestamp": ts_ms,
        "args": [{"type": "string", "value": text}],
        "stackTrace": {"callFrames": [{"url": url}]},
    }


def request_event(request_id, url="https://x.dev/api", method="GET", post_data=None):
    request = {"url": url, "method": method}
    if post_data is not None:
        request["postData"] = post_data
    return {
        "requestId": request_id,
        "wallTime": 1700000000.5,
        "timestamp": 100.0,
        "type": "XHR",
        "request": request,
    }


def response_event(request_id, status):
    return {"requestId": request_id, "response": {"status": status}}


def finished_event(request_id, ts=100.25):
    return {"requestId": request_id, "timestamp": ts}


class TestConsole:
    def test_console_event_is_recorded(self):
        recorder = TelemetryRecorder()
        recorder.on_console_api_called(console_event("boom", "error"))
        snapshot = recorder.snapshot()
        entry = snapshot.console[0]
        assert entry.text == "boom"
        assert entry.level == "error"
        assert entry.ts == 1700000000.0  # ms -> s
        assert entry.source_url == "app.js"

    def test_multiple_args_are_joined_and_non_strings_serialized(self):
        recorder = TelemetryRecorder()
        recorder.on_console_api_called(
            {"type": "log", "timestamp": 0, "args": [
                {"type": "string", "value": "count:"},
                {"type": "number", "value": 3},
                {"type": "object", "description": "Object"},
            ]}
        )
        assert recorder.snapshot().console[0].text == "count: 3 Object"

    def test_unknown_level_maps_to_log_and_assert_to_error(self):
        recorder = TelemetryRecorder()
        recorder.on_console_api_called(console_event(level="startGroup"))
        recorder.on_console_api_called(console_event(level="assert"))
        snapshot = recorder.snapshot()
        assert snapshot.console[0].level == "log"
        assert snapshot.console[1].level == "error"

    def test_uncaught_exception_is_an_error_entry(self):
        recorder = TelemetryRecorder()
        recorder.on_exception_thrown(
            {
                "timestamp": 1000.0,
                "exceptionDetails": {
                    "text": "Uncaught",
                    "exception": {"description": "TypeError: x is undefined"},
                    "url": "checkout.js",
                },
            }
        )
        entry = recorder.snapshot().console[0]
        assert entry.level == "error"
        assert "TypeError" in entry.text
        assert entry.source_url == "checkout.js"

    def test_ring_buffer_drops_oldest_and_counts(self):
        recorder = TelemetryRecorder(RecorderConfig(max_console_entries=3))
        for i in range(5):
            recorder.on_console_api_called(console_event(text=f"m{i}"))
        snapshot = recorder.snapshot()
        assert [e.text for e in snapshot.console] == ["m2", "m3", "m4"]
        assert snapshot.console_dropped == 2

    def test_secret_values_are_scrubbed_from_console_text(self):
        recorder = TelemetryRecorder(RecorderConfig(extra_secret_values=["hunter2"]))
        recorder.on_console_api_called(console_event(text="pw is hunter2"))
        assert "hunter2" not in recorder.snapshot().console[0].text


class TestNetwork:
    def test_successful_request_lifecycle(self):
        recorder = TelemetryRecorder()
        recorder.on_request_will_be_sent(request_event("r1"))
        recorder.on_response_received(response_event("r1", 200))
        recorder.on_loading_finished(finished_event("r1"))
        entry = recorder.snapshot().network[0]
        assert entry.status == 200
        assert entry.failed is False
        assert entry.duration_ms == 250.0
        assert entry.ts == 1700000000.5
        assert entry.resource_type == "XHR"

    def test_http_error_is_failed_and_awaits_body(self):
        recorder = TelemetryRecorder()
        recorder.on_request_will_be_sent(request_event("r1", post_data='{"api_key": "sk-1"}'))
        recorder.on_response_received(response_event("r1", 500))
        recorder.on_loading_finished(finished_event("r1"))
        assert recorder.pending_failed_request_ids() == ["r1"]
        recorder.set_response_body("r1", '{"error": "boom", "session": "s-9"}')
        entry = recorder.snapshot().network[0]
        assert entry.failed is True
        assert "sk-1" not in entry.request_body
        assert "s-9" not in entry.response_body
        assert "boom" in entry.response_body

    def test_network_level_failure_records_error_text(self):
        recorder = TelemetryRecorder()
        recorder.on_request_will_be_sent(request_event("r1"))
        recorder.on_loading_failed(
            {"requestId": "r1", "timestamp": 100.1, "errorText": "net::ERR_CONNECTION_REFUSED"}
        )
        entry = recorder.snapshot().network[0]
        assert entry.failed is True
        assert entry.error_text == "net::ERR_CONNECTION_REFUSED"
        assert entry.status == 0

    def test_request_body_not_stored_for_successful_requests(self):
        recorder = TelemetryRecorder()
        recorder.on_request_will_be_sent(request_event("r1", post_data='{"password": "p"}'))
        recorder.on_response_received(response_event("r1", 200))
        recorder.on_loading_finished(finished_event("r1"))
        entry = recorder.snapshot().network[0]
        assert entry.request_body == ""

    def test_url_query_secrets_redacted_at_capture_time(self):
        recorder = TelemetryRecorder()
        recorder.on_request_will_be_sent(request_event("r1", url="https://x.dev/?token=t-1&a=b"))
        assert "t-1" not in recorder._pending["r1"]["url"]

    def test_unfinished_requests_flush_as_status_zero_on_snapshot(self):
        recorder = TelemetryRecorder()
        recorder.on_request_will_be_sent(request_event("r1"))
        snapshot = recorder.snapshot()
        assert snapshot.network[0].status == 0
        assert snapshot.network[0].failed is False

    def test_body_unavailable_sentinel(self):
        recorder = TelemetryRecorder()
        recorder.on_request_will_be_sent(request_event("r1"))
        recorder.on_response_received(response_event("r1", 404))
        recorder.on_loading_finished(finished_event("r1"))
        recorder.set_response_body("r1", BODY_UNAVAILABLE)
        assert recorder.snapshot().network[0].response_body == BODY_UNAVAILABLE

    def test_ring_buffer_drops_oldest_and_counts(self):
        recorder = TelemetryRecorder(RecorderConfig(max_network_entries=2))
        for i in range(4):
            recorder.on_request_will_be_sent(request_event(f"r{i}", url=f"https://x.dev/{i}"))
            recorder.on_response_received(response_event(f"r{i}", 200))
            recorder.on_loading_finished(finished_event(f"r{i}"))
        snapshot = recorder.snapshot()
        assert [e.url for e in snapshot.network] == ["https://x.dev/2", "https://x.dev/3"]
        assert snapshot.network_dropped == 2


class TestSteps:
    def test_step_context_manager_attributes_telemetry(self):
        recorder = TelemetryRecorder()
        with recorder.step("navigate"):
            recorder.on_console_api_called(console_event("first"))
        with recorder.step("click"):
            recorder.on_console_api_called(console_event("second"))
        snapshot = recorder.snapshot()
        assert snapshot.console[0].step_index == 0
        assert snapshot.console[1].step_index == 1
        assert [s.status for s in recorder.steps] == ["passed", "passed"]

    def test_step_failure_marks_step_and_reraises(self):
        recorder = TelemetryRecorder()
        with pytest.raises(ValueError, match="nope"), recorder.step("click"):
            raise ValueError("nope")
        assert recorder.steps[0].status == "failed"
        assert recorder.steps[0].note == "nope"

    def test_manual_step_index_still_works(self):
        recorder = TelemetryRecorder()
        recorder.current_step_index = 7
        recorder.on_console_api_called(console_event())
        assert recorder.snapshot().console[0].step_index == 7


class TestEnvironmentAndHooks:
    def test_set_environment_known_and_unknown_fields(self):
        recorder = TelemetryRecorder()
        recorder.set_environment(target_url="https://x.dev", run_id="run-1")
        env = recorder.snapshot().environment
        assert env.target_url == "https://x.dev"
        assert env.extra == {"run_id": "run-1"}

    def test_target_url_falls_back_to_first_document_request(self):
        recorder = TelemetryRecorder()
        event = request_event("r1", url="https://myapp.dev/checkout")
        event["type"] = "Document"
        recorder.on_request_will_be_sent(event)
        recorder.on_response_received(response_event("r1", 200))
        recorder.on_loading_finished(finished_event("r1"))
        assert recorder.snapshot().environment.target_url == "https://myapp.dev/checkout"

    def test_explicit_target_url_wins_over_document_fallback(self):
        recorder = TelemetryRecorder()
        recorder.set_environment(target_url="https://explicit.dev")
        event = request_event("r1", url="https://myapp.dev/")
        event["type"] = "Document"
        recorder.on_request_will_be_sent(event)
        assert recorder.snapshot().environment.target_url == "https://explicit.dev"

    def test_snapshot_hooks_run_and_exceptions_are_suppressed(self):
        recorder = TelemetryRecorder()
        calls = []
        recorder.snapshot_hooks.append(lambda rec: calls.append(1))

        def broken(rec):
            raise RuntimeError("hook broke")

        recorder.snapshot_hooks.append(broken)
        recorder.snapshot()
        assert calls == [1]
