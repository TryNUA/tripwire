"""Adapter tests against stub Page/CDP objects — no browser required."""

import asyncio

from tripwire import TelemetryRecorder
from tripwire.integrations.playwright import attach, attach_async, fetch_failed_bodies


class StubCDP:
    def __init__(self, responses=None, fail_body_fetch=False):
        self.sent = []
        self.handlers = {}
        self.responses = responses or {}
        self.fail_body_fetch = fail_body_fetch

    def send(self, method, params=None):
        self.sent.append((method, params))
        if method == "Network.getResponseBody" and self.fail_body_fetch:
            raise RuntimeError("No resource with given identifier")
        return self.responses.get(method, {})

    def on(self, event, handler):
        self.handlers[event] = handler

    def emit(self, event, payload):
        self.handlers[event](payload)


class AsyncStubCDP(StubCDP):
    async def send(self, method, params=None):  # type: ignore[override]
        return StubCDP.send(self, method, params)


class StubContext:
    def __init__(self, cdp):
        self._cdp = cdp

    def new_cdp_session(self, page):
        return self._cdp


class AsyncStubContext(StubContext):
    async def new_cdp_session(self, page):  # type: ignore[override]
        return self._cdp


class StubPage:
    def __init__(self, cdp, context_cls=StubContext):
        self.context = context_cls(cdp)
        self.url = "https://myapp.dev/start"
        self.viewport_size = {"width": 1280, "height": 720}


VERSION_RESPONSE = {"Browser.getVersion": {"userAgent": "UA", "product": "Chrome/138.0"}}


def fail_request(cdp, request_id="r1"):
    cdp.emit(
        "Network.requestWillBeSent",
        {
            "requestId": request_id,
            "wallTime": 1.0,
            "timestamp": 1.0,
            "type": "Fetch",
            "request": {"url": "https://myapp.dev/api", "method": "POST"},
        },
    )
    cdp.emit("Network.responseReceived", {"requestId": request_id, "response": {"status": 500}})
    cdp.emit("Network.loadingFinished", {"requestId": request_id, "timestamp": 1.2})


class TestAttachSync:
    def test_enables_domains_and_registers_all_handlers(self):
        cdp = StubCDP(VERSION_RESPONSE)
        attach(TelemetryRecorder(), StubPage(cdp))
        assert ("Runtime.enable", None) in cdp.sent
        assert ("Network.enable", None) in cdp.sent
        assert set(cdp.handlers) == {
            "Runtime.consoleAPICalled",
            "Runtime.exceptionThrown",
            "Network.requestWillBeSent",
            "Network.responseReceived",
            "Network.loadingFinished",
            "Network.loadingFailed",
        }

    def test_environment_is_captured(self):
        cdp = StubCDP(VERSION_RESPONSE)
        recorder = TelemetryRecorder()
        page = StubPage(cdp)
        attach(recorder, page)
        page.url = "https://myapp.dev/end"
        env = recorder.snapshot().environment
        assert env.user_agent == "UA"
        assert env.browser_version == "Chrome/138.0"
        assert env.viewport_width == 1280
        assert env.target_url == "https://myapp.dev/start"
        assert env.final_url == "https://myapp.dev/end"

    def test_events_flow_into_recorder(self):
        cdp = StubCDP(VERSION_RESPONSE)
        recorder = TelemetryRecorder()
        attach(recorder, StubPage(cdp))
        cdp.emit(
            "Runtime.consoleAPICalled",
            {"type": "error", "timestamp": 0, "args": [{"type": "string", "value": "boom"}]},
        )
        assert recorder.snapshot().console[0].text == "boom"

    def test_failed_bodies_are_fetched_on_snapshot(self):
        cdp = StubCDP({**VERSION_RESPONSE, "Network.getResponseBody": {"body": '{"error": "x"}'}})
        recorder = TelemetryRecorder()
        attach(recorder, StubPage(cdp))
        fail_request(cdp)
        entry = recorder.snapshot().network[0]
        assert entry.response_body == '{"error": "x"}'

    def test_evicted_body_becomes_unavailable_sentinel(self):
        cdp = StubCDP(VERSION_RESPONSE, fail_body_fetch=True)
        recorder = TelemetryRecorder()
        attach(recorder, StubPage(cdp))
        fail_request(cdp)
        assert recorder.snapshot().network[0].response_body == "body_unavailable"

    def test_base64_body_is_omitted(self):
        cdp = StubCDP(
            {**VERSION_RESPONSE, "Network.getResponseBody": {"body": "aGk=", "base64Encoded": True}}
        )
        recorder = TelemetryRecorder()
        attach(recorder, StubPage(cdp))
        fail_request(cdp)
        assert recorder.snapshot().network[0].response_body == "<binary body omitted>"


class TestAttachAsync:
    def test_async_attach_and_body_fetch(self):
        async def scenario():
            cdp = AsyncStubCDP(
                {**VERSION_RESPONSE, "Network.getResponseBody": {"body": '{"error": "x"}'}}
            )
            recorder = TelemetryRecorder()
            page = StubPage(cdp, context_cls=AsyncStubContext)
            await attach_async(recorder, page)
            fail_request(cdp)
            await fetch_failed_bodies(recorder, cdp)
            return recorder.snapshot()

        snapshot = asyncio.run(scenario())
        assert snapshot.network[0].response_body == '{"error": "x"}'
        assert snapshot.environment.user_agent == "UA"
