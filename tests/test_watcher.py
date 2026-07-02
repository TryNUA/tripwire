import asyncio
import json

import pytest

pytest.importorskip("websockets")

from tripwire.cli import state  # noqa: E402
from tripwire.cli.watcher import BINDING, Watcher  # noqa: E402
from tripwire.recorder import BODY_UNAVAILABLE, TelemetryRecorder  # noqa: E402


class FakeCDPClient:
    """Same surface as cdp.CDPClient, records sends, hand-fed events."""

    def __init__(self):
        self.sent = []
        self.responders = {}
        self.handler = None

    def on_event(self, handler):
        self.handler = handler

    async def send(self, method, params=None, session_id=None, timeout=10.0):
        self.sent.append((method, params or {}, session_id))
        result = self.responders.get(method, {})
        if isinstance(result, Exception):
            raise result
        return result

    def emit(self, method, params, session_id=None):
        self.handler(method, params, session_id)

    def calls(self, method):
        return [c for c in self.sent if c[0] == method]


@pytest.fixture
async def watcher(tmp_path):
    client = FakeCDPClient()
    w = Watcher(TelemetryRecorder(), client, tmp_path, "http://127.0.0.1:9222")
    await w.start()
    return w


def attach_page(client, session_id="s1"):
    client.emit(
        "Target.attachedToTarget",
        {"sessionId": session_id, "targetInfo": {"type": "page", "targetId": "t1"}},
    )


def binding(client, message, session_id="s1"):
    client.emit(
        "Runtime.bindingCalled",
        {"name": BINDING, "payload": json.dumps(message)},
        session_id,
    )


async def settle():
    for _ in range(3):
        await asyncio.sleep(0)


class TestAttach:
    async def test_start_sets_auto_attach_and_environment(self, watcher):
        client = watcher.client
        client.responders["Browser.getVersion"] = {"product": "Chrome/138", "userAgent": "UA"}
        method, params, _ = client.calls("Target.setAutoAttach")[0]
        assert params == {"autoAttach": True, "waitForDebuggerOnStart": True, "flatten": True}

    async def test_page_target_gets_domains_binding_and_observer(self, watcher):
        client = watcher.client
        attach_page(client)
        await settle()
        methods = [m for m, _, s in client.sent if s == "s1"]
        assert methods == [
            "Runtime.enable",
            "Network.enable",
            "Page.enable",
            "Runtime.addBinding",
            "Page.addScriptToEvaluateOnNewDocument",
            "Runtime.evaluate",
            "Runtime.runIfWaitingForDebugger",
        ]
        assert client.calls("Runtime.addBinding")[0][1] == {"name": BINDING}
        assert "__tripwireObserver" in client.calls("Runtime.evaluate")[0][1]["expression"]

    async def test_non_page_targets_ignored(self, watcher):
        client = watcher.client
        client.emit(
            "Target.attachedToTarget",
            {"sessionId": "sw", "targetInfo": {"type": "service_worker", "targetId": "t2"}},
        )
        await settle()
        assert [c for c in client.sent if c[2] == "sw"] == []

    async def test_duplicate_attach_sets_up_once(self, watcher):
        client = watcher.client
        attach_page(client)
        attach_page(client)
        await settle()
        assert len(client.calls("Runtime.addBinding")) == 1

    async def test_preexisting_unattached_pages_swept(self, tmp_path):
        client = FakeCDPClient()
        client.responders["Target.getTargets"] = {
            "targetInfos": [
                {"type": "page", "targetId": "old", "attached": False},
                {"type": "browser", "targetId": "b", "attached": True},
            ]
        }
        await Watcher(TelemetryRecorder(), client, tmp_path, "").start()
        assert client.calls("Target.attachToTarget")[0][1] == {"targetId": "old", "flatten": True}


class TestSteps:
    async def test_binding_actions_become_steps(self, watcher):
        client = watcher.client
        attach_page(client)
        binding(client, {"kind": "click", "target": "button#pay \"Pay now\""})
        binding(client, {"kind": "input", "target": 'input[name="email"]'})
        binding(client, {"kind": "submit", "target": "form#checkout"})
        assert [s.description for s in watcher.recorder.steps] == [
            'click button#pay "Pay now"',
            'type into input[name="email"]',
            "submit form#checkout",
        ]
        assert watcher.recorder.current_step_index == 2

    async def test_telemetry_attributes_to_current_step(self, watcher):
        client = watcher.client
        binding(client, {"kind": "click", "target": "button#pay"})
        client.emit(
            "Runtime.consoleAPICalled",
            {"type": "error", "timestamp": 0, "args": [{"type": "string", "value": "boom"}]},
            "s1",
        )
        entry = watcher.recorder.snapshot(flush_pending=False).console[0]
        assert entry.step_index == 0

    async def test_wrong_binding_name_and_garbage_payload_ignored(self, watcher):
        client = watcher.client
        client.emit("Runtime.bindingCalled", {"name": "other", "payload": '{"kind":"click"}'}, "s1")
        client.emit("Runtime.bindingCalled", {"name": BINDING, "payload": "not json"}, "s1")
        assert watcher.recorder.steps == []

    async def test_main_frame_navigation_becomes_step_and_final_url(self, watcher):
        client = watcher.client
        client.emit(
            "Page.frameNavigated", {"frame": {"url": "https://x.dev/checkout"}}, "s1"
        )
        assert watcher.recorder.steps[0].description == "navigate to https://x.dev/checkout"
        env = watcher.recorder.snapshot(flush_pending=False).environment
        assert env.final_url == "https://x.dev/checkout"

    async def test_subframe_and_blank_navigations_ignored(self, watcher):
        client = watcher.client
        client.emit(
            "Page.frameNavigated",
            {"frame": {"url": "https://ads.example/frame", "parentId": "f0"}},
            "s1",
        )
        client.emit("Page.frameNavigated", {"frame": {"url": "about:blank"}}, "s1")
        assert watcher.recorder.steps == []

    async def test_spa_navigate_deduped_against_frame_navigated(self, watcher):
        client = watcher.client
        binding(client, {"kind": "navigate", "url": "https://x.dev/cart"})
        client.emit("Page.frameNavigated", {"frame": {"url": "https://x.dev/cart"}}, "s1")
        assert len(watcher.recorder.steps) == 1


class TestFailureBodies:
    def fail_request(self, client, request_id="r1", session_id="s1", status=500):
        client.emit(
            "Network.requestWillBeSent",
            {
                "requestId": request_id,
                "wallTime": 1.0,
                "timestamp": 1.0,
                "type": "XHR",
                "request": {"url": "https://x.dev/api", "method": "POST"},
            },
            session_id,
        )
        client.emit(
            "Network.responseReceived",
            {"requestId": request_id, "response": {"status": status}},
            session_id,
        )
        client.emit(
            "Network.loadingFinished", {"requestId": request_id, "timestamp": 2.0}, session_id
        )

    async def test_failed_body_fetched_on_originating_session(self, watcher):
        client = watcher.client
        client.responders["Network.getResponseBody"] = {"body": '{"error":"missing cart_id"}'}
        self.fail_request(client, session_id="s2")
        await settle()
        method, params, session_id = client.calls("Network.getResponseBody")[0]
        assert params == {"requestId": "r1"}
        assert session_id == "s2"
        entry = watcher.recorder.snapshot(flush_pending=False).network[0]
        assert "missing cart_id" in entry.response_body

    async def test_body_fetch_error_marks_unavailable(self, watcher):
        client = watcher.client
        client.responders["Network.getResponseBody"] = RuntimeError("target gone")
        self.fail_request(client)
        await settle()
        entry = watcher.recorder.snapshot(flush_pending=False).network[0]
        assert entry.response_body == BODY_UNAVAILABLE

    async def test_successful_requests_fetch_nothing(self, watcher):
        client = watcher.client
        self.fail_request(client, status=200)
        await settle()
        assert client.calls("Network.getResponseBody") == []


class TestPersistence:
    async def test_state_file_written_and_readable(self, watcher, tmp_path):
        client = watcher.client
        binding(client, {"kind": "click", "target": "button#go"})
        client.emit(
            "Runtime.exceptionThrown",
            {"timestamp": 0, "exceptionDetails": {"text": "Uncaught TypeError"}},
            "s1",
        )
        session = state.read_session(tmp_path)
        assert session.steps[0].description == "click button#go"
        assert session.snapshot.console[0].text == "Uncaught TypeError"
        assert state.pid_alive(session.pid)

    async def test_exception_persists_immediately(self, watcher, tmp_path):
        watcher.client.emit(
            "Runtime.exceptionThrown",
            {"timestamp": 0, "exceptionDetails": {"text": "boom"}},
            "s1",
        )
        assert state.read_session(tmp_path).snapshot.console[0].text == "boom"

    async def test_persist_does_not_flush_in_flight_requests(self, watcher, tmp_path):
        client = watcher.client
        client.emit(
            "Network.requestWillBeSent",
            {"requestId": "r9", "wallTime": 1.0, "timestamp": 1.0, "type": "XHR",
             "request": {"url": "https://x.dev/slow", "method": "GET"}},
            "s1",
        )
        watcher.persist()
        assert state.read_session(tmp_path).snapshot.network == []
        client.emit(
            "Network.responseReceived", {"requestId": "r9", "response": {"status": 200}}, "s1"
        )
        client.emit("Network.loadingFinished", {"requestId": "r9", "timestamp": 2.0}, "s1")
        watcher.persist()
        assert state.read_session(tmp_path).snapshot.network[0].status == 200
