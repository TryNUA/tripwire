"""browser-use adapter tests against stub BrowserSession/cdp-use objects.

The stubs emulate cdp-use's chained ``register.Domain.event(handler)`` /
``await send.Domain.method(params=..., session_id=...)`` API — no browser-use
install required.
"""

import asyncio
from types import SimpleNamespace

from tripwire import TelemetryRecorder
from tripwire.integrations.browser_use import attach, step_hooks

# --------------------------------------------------------------------- stubs


class StubRegisterDomain:
    def __init__(self, client, domain):
        self._client = client
        self._domain = domain

    def __getattr__(self, event):
        def register(handler):
            self._client.handlers[f"{self._domain}.{event}"] = handler

        return register


class StubRegister:
    def __init__(self, client):
        self._client = client

    def __getattr__(self, domain):
        return StubRegisterDomain(self._client, domain)


class StubSendDomain:
    def __init__(self, client, domain):
        self._client = client
        self._domain = domain

    def __getattr__(self, method):
        name = f"{self._domain}.{method}"

        async def call(params=None, session_id=None):
            self._client.sent.append((name, params, session_id))
            if name in self._client.raises:
                raise RuntimeError(f"{name} failed")
            response = self._client.responses.get(name, {})
            if callable(response):
                return response(params)
            return response

        return call


class StubSend:
    def __init__(self, client):
        self._client = client

    def __getattr__(self, domain):
        return StubSendDomain(self._client, domain)


class StubCDPClient:
    """Chained-API stub: records sent commands, lets tests emit events."""

    def __init__(self, responses=None, raises=()):
        self.sent = []
        self.handlers = {}
        self.responses = responses or {}
        self.raises = set(raises)
        self.register = StubRegister(self)
        self.send = StubSend(self)

    def emit(self, event, payload, session_id=None):
        self.handlers[event](payload, session_id)

    def sent_named(self, name):
        return [entry for entry in self.sent if entry[0] == name]


class StubBrowserSession:
    def __init__(self, cdp_client, session_id="sess-1", viewport=None, url="https://myapp.dev/end"):
        self.cdp_client = cdp_client
        self._session_id = session_id
        self._url = url
        self.browser_profile = SimpleNamespace(
            viewport={"width": 1280, "height": 720} if viewport is None else viewport
        )

    async def get_or_create_cdp_session(self):
        return SimpleNamespace(cdp_client=self.cdp_client, session_id=self._session_id)

    async def get_current_page_url(self):
        if isinstance(self._url, Exception):
            raise self._url
        return self._url


VERSION_RESPONSE = {"Browser.getVersion": {"userAgent": "UA", "product": "Chrome/138.0"}}

ALL_EVENTS = {
    "Runtime.consoleAPICalled",
    "Runtime.exceptionThrown",
    "Network.requestWillBeSent",
    "Network.responseReceived",
    "Network.loadingFinished",
    "Network.loadingFailed",
}


def make_attached(responses=None, raises=(), **session_kwargs):
    client = StubCDPClient({**VERSION_RESPONSE, **(responses or {})}, raises)
    browser_session = StubBrowserSession(client, **session_kwargs)
    recorder = TelemetryRecorder()
    attachment = asyncio.run(attach(recorder, browser_session))
    return recorder, client, attachment


def fail_request(client, request_id="r1", session_id="sess-1", url="https://myapp.dev/api"):
    client.emit(
        "Network.requestWillBeSent",
        {
            "requestId": request_id,
            "wallTime": 1.0,
            "timestamp": 1.0,
            "type": "Fetch",
            "request": {"url": url, "method": "POST"},
        },
        session_id,
    )
    client.emit(
        "Network.responseReceived",
        {"requestId": request_id, "response": {"status": 500}},
        session_id,
    )
    client.emit("Network.loadingFinished", {"requestId": request_id, "timestamp": 1.2}, session_id)


# ----------------------------------------------------- attach and event flow


class TestAttach:
    def test_registers_all_handlers_and_enables_domains(self):
        _, client, _ = make_attached()
        assert set(client.handlers) == ALL_EVENTS
        assert ("Runtime.enable", None, "sess-1") in client.sent
        assert ("Network.enable", None, "sess-1") in client.sent

    def test_console_event_flows_into_recorder(self):
        recorder, client, _ = make_attached()
        client.emit(
            "Runtime.consoleAPICalled",
            {"type": "error", "timestamp": 0, "args": [{"type": "string", "value": "boom"}]},
            "sess-1",
        )
        entry = recorder.snapshot().console[0]
        assert entry.text == "boom"
        assert entry.level == "error"

    def test_exception_event_becomes_error_entry(self):
        recorder, client, _ = make_attached()
        client.emit(
            "Runtime.exceptionThrown",
            {"timestamp": 0, "exceptionDetails": {"text": "Uncaught TypeError: nope"}},
            "sess-1",
        )
        entry = recorder.snapshot().console[0]
        assert entry.level == "error"
        assert "Uncaught TypeError" in entry.text

    def test_network_lifecycle_produces_completed_entry(self):
        recorder, client, _ = make_attached()
        client.emit(
            "Network.requestWillBeSent",
            {
                "requestId": "ok1",
                "wallTime": 5.0,
                "timestamp": 10.0,
                "type": "Fetch",
                "request": {"url": "https://myapp.dev/api", "method": "GET"},
            },
            "sess-1",
        )
        client.emit(
            "Network.responseReceived", {"requestId": "ok1", "response": {"status": 200}}, "sess-1"
        )
        client.emit("Network.loadingFinished", {"requestId": "ok1", "timestamp": 10.5}, "sess-1")
        entry = recorder.snapshot().network[0]
        assert entry.status == 200
        assert entry.duration_ms == 500.0
        assert not entry.failed

    def test_500_marks_entry_failed(self):
        recorder, client, _ = make_attached()
        fail_request(client)
        assert recorder.snapshot().network[0].failed

    def test_loading_failed_produces_failed_entry_with_error_text(self):
        recorder, client, _ = make_attached()
        client.emit(
            "Network.requestWillBeSent",
            {
                "requestId": "r1",
                "wallTime": 1.0,
                "timestamp": 1.0,
                "type": "Fetch",
                "request": {"url": "https://myapp.dev/api", "method": "GET"},
            },
            "sess-1",
        )
        client.emit(
            "Network.loadingFailed",
            {"requestId": "r1", "timestamp": 1.1, "errorText": "net::ERR_CONNECTION_REFUSED"},
            "sess-1",
        )
        entry = recorder.snapshot().network[0]
        assert entry.failed
        assert entry.error_text == "net::ERR_CONNECTION_REFUSED"

    def test_telemetry_is_attributed_to_active_step(self):
        recorder, client, _ = make_attached()
        with recorder.step("click pay"):
            client.emit(
                "Runtime.consoleAPICalled",
                {"type": "log", "timestamp": 0, "args": [{"type": "string", "value": "inside"}]},
                "sess-1",
            )
        assert recorder.snapshot().console[0].step_index == 0


# --------------------------------------------- fetch_failed_bodies and environment


class TestFetchFailedBodies:
    def test_body_fetched_with_originating_session_id(self):
        recorder, client, attachment = make_attached(
            {"Network.getResponseBody": {"body": '{"error": "x"}'}}
        )
        fail_request(client, session_id="sess-tab-2")
        asyncio.run(attachment.fetch_failed_bodies())
        assert client.sent_named("Network.getResponseBody") == [
            ("Network.getResponseBody", {"requestId": "r1"}, "sess-tab-2")
        ]
        assert recorder.snapshot().network[0].response_body == '{"error": "x"}'

    def test_body_is_redacted(self):
        recorder, client, attachment = make_attached(
            {"Network.getResponseBody": {"body": '{"password": "hunter2", "detail": "bad"}'}}
        )
        fail_request(client)
        asyncio.run(attachment.fetch_failed_bodies())
        body = recorder.snapshot().network[0].response_body
        assert "hunter2" not in body
        assert '"password": "<redacted>"' in body
        assert '"detail": "bad"' in body

    def test_multi_tab_requests_fetch_with_their_own_session(self):
        bodies = {"r1": '{"tab": 1}', "r2": '{"tab": 2}'}
        recorder, client, attachment = make_attached(
            {"Network.getResponseBody": lambda params: {"body": bodies[params["requestId"]]}}
        )
        fail_request(client, request_id="r1", session_id="sess-tab-1")
        fail_request(client, request_id="r2", session_id="sess-tab-2")
        asyncio.run(attachment.fetch_failed_bodies())
        fetches = client.sent_named("Network.getResponseBody")
        assert ("Network.getResponseBody", {"requestId": "r1"}, "sess-tab-1") in fetches
        assert ("Network.getResponseBody", {"requestId": "r2"}, "sess-tab-2") in fetches
        network = recorder.snapshot().network
        assert network[0].response_body == '{"tab": 1}'
        assert network[1].response_body == '{"tab": 2}'

    def test_environment_is_captured(self):
        recorder, client, attachment = make_attached()
        fail_request(client, url="https://myapp.dev/start")
        asyncio.run(attachment.fetch_failed_bodies())
        env = recorder.snapshot().environment
        assert env.user_agent == "UA"
        assert env.browser_version == "Chrome/138.0"
        assert env.viewport_width == 1280
        assert env.viewport_height == 720
        assert env.final_url == "https://myapp.dev/end"

    def test_target_url_derived_from_first_document_request(self):
        recorder, client, _ = make_attached()
        client.emit(
            "Network.requestWillBeSent",
            {
                "requestId": "doc1",
                "wallTime": 1.0,
                "timestamp": 1.0,
                "type": "Document",
                "request": {"url": "https://myapp.dev/checkout", "method": "GET"},
            },
            "sess-1",
        )
        client.emit("Network.loadingFinished", {"requestId": "doc1", "timestamp": 1.1}, "sess-1")
        assert recorder.snapshot().environment.target_url == "https://myapp.dev/checkout"

    def test_end_to_end_report_renders(self):
        recorder, client, attachment = make_attached(
            {"Network.getResponseBody": {"body": '{"error": "kaput"}'}}
        )
        on_step_start, on_step_end = step_hooks(recorder)
        agent = make_agent(
            step(
                actions=[StubAction("click_element_by_index", {"index": 3})],
                next_goal="Click the pay button",
                errors=["Element not found"],
            )
        )
        asyncio.run(on_step_start(agent))
        client.emit(
            "Runtime.consoleAPICalled",
            {"type": "error", "timestamp": 0, "args": [{"type": "string", "value": "boom"}]},
            "sess-1",
        )
        fail_request(client)
        asyncio.run(on_step_end(agent))
        asyncio.run(attachment.fetch_failed_bodies())
        report = recorder.report()
        assert "click_element_by_index — Click the pay button" in report
        assert "boom" in report
        assert "/api" in report


class TestCDPFailures:
    def test_enable_failures_do_not_break_attach(self):
        recorder, client, _ = make_attached(raises={"Runtime.enable", "Network.enable"})
        assert set(client.handlers) == ALL_EVENTS
        client.emit(
            "Runtime.consoleAPICalled",
            {"type": "log", "timestamp": 0, "args": [{"type": "string", "value": "still works"}]},
            "sess-1",
        )
        assert recorder.snapshot().console[0].text == "still works"

    def test_get_version_failure_leaves_environment_empty(self):
        recorder, _, _ = make_attached(raises={"Browser.getVersion"})
        env = recorder.snapshot().environment
        assert env.user_agent == ""
        assert env.browser_version == ""

    def test_body_fetch_error_becomes_unavailable_sentinel(self):
        recorder, client, attachment = make_attached(raises={"Network.getResponseBody"})
        fail_request(client)
        asyncio.run(attachment.fetch_failed_bodies())
        assert recorder.snapshot().network[0].response_body == "body_unavailable"

    def test_base64_body_is_omitted(self):
        recorder, client, attachment = make_attached(
            {"Network.getResponseBody": {"body": "aGk=", "base64Encoded": True}}
        )
        fail_request(client)
        asyncio.run(attachment.fetch_failed_bodies())
        assert recorder.snapshot().network[0].response_body == "<binary body omitted>"

    def test_current_url_failure_leaves_final_url_empty(self):
        recorder, client, attachment = make_attached(
            {"Network.getResponseBody": {"body": "x"}}, url=RuntimeError("no page")
        )
        fail_request(client)
        asyncio.run(attachment.fetch_failed_bodies())
        assert recorder.snapshot().environment.final_url == ""
        assert recorder.snapshot().network[0].response_body == "x"

    def test_unknown_session_id_falls_back_to_none(self):
        recorder, client, attachment = make_attached(raises={"Network.getResponseBody"})
        fail_request(client)
        attachment._request_sessions.clear()
        asyncio.run(attachment.fetch_failed_bodies())
        assert client.sent_named("Network.getResponseBody") == [
            ("Network.getResponseBody", {"requestId": "r1"}, None)
        ]
        assert recorder.snapshot().network[0].response_body == "body_unavailable"

    def test_no_pending_failures_is_a_noop(self):
        _, client, attachment = make_attached()
        asyncio.run(attachment.fetch_failed_bodies())
        assert client.sent_named("Network.getResponseBody") == []

    def test_fetch_twice_is_idempotent(self):
        recorder, client, attachment = make_attached({"Network.getResponseBody": {"body": "x"}})
        fail_request(client)
        asyncio.run(attachment.fetch_failed_bodies())
        asyncio.run(attachment.fetch_failed_bodies())
        assert len(client.sent_named("Network.getResponseBody")) == 1
        assert recorder.snapshot().network[0].response_body == "x"


class TestEventPayloadEdgeCases:
    def test_session_id_may_be_omitted(self):
        recorder, client, _ = make_attached()
        client.handlers["Runtime.consoleAPICalled"](
            {"type": "log", "timestamp": 0, "args": [{"type": "string", "value": "root"}]}
        )
        assert recorder.snapshot().console[0].text == "root"

    def test_minimal_event_payloads_do_not_raise(self):
        recorder, client, _ = make_attached()
        client.emit("Runtime.consoleAPICalled", {}, "sess-1")
        client.emit("Runtime.exceptionThrown", {}, "sess-1")
        client.emit("Network.requestWillBeSent", {}, "sess-1")
        snapshot = recorder.snapshot()
        assert len(snapshot.console) == 2  # console + exception entries, both empty-ish
        assert snapshot.network == []

    def test_lifecycle_events_for_unknown_request_are_ignored(self):
        recorder, client, _ = make_attached()
        client.emit(
            "Network.responseReceived",
            {"requestId": "pre-attach", "response": {"status": 200}},
            "sess-1",
        )
        client.emit(
            "Network.loadingFinished", {"requestId": "pre-attach", "timestamp": 1.0}, "sess-1"
        )
        client.emit(
            "Network.loadingFailed",
            {"requestId": "pre-attach", "timestamp": 1.0, "errorText": "x"},
            "sess-1",
        )
        assert recorder.snapshot().network == []


# ---------------------------------------------------------------- step hooks


class StubAction:
    def __init__(self, name, params=None, raise_on_dump=False):
        self._name = name
        self._params = params or {}
        self._raise = raise_on_dump

    def model_dump(self, exclude_unset=False):
        if self._raise:
            raise RuntimeError("bad action model")
        return {self._name: self._params}


def step(actions=(), next_goal="", errors=()):
    model_output = SimpleNamespace(action=list(actions), next_goal=next_goal)
    result = [SimpleNamespace(error=error) for error in errors] or [SimpleNamespace(error=None)]
    return SimpleNamespace(model_output=model_output, result=result)


def make_agent(*history_items):
    return SimpleNamespace(history=SimpleNamespace(history=list(history_items)))


def run_step(recorder, hooks, agent):
    on_step_start, on_step_end = hooks
    asyncio.run(on_step_start(agent))
    asyncio.run(on_step_end(agent))


class TestStepHooksHappy:
    def test_start_opens_step_and_attributes_telemetry(self):
        recorder, client, _ = make_attached()
        on_step_start, _ = step_hooks(recorder)
        asyncio.run(on_step_start(make_agent()))
        asyncio.run(on_step_start(make_agent()))
        client.emit(
            "Runtime.consoleAPICalled",
            {"type": "log", "timestamp": 0, "args": [{"type": "string", "value": "hi"}]},
            "sess-1",
        )
        assert [s.description for s in recorder.steps] == ["step 1", "step 2"]
        assert recorder.current_step_index == 1
        assert recorder.snapshot().console[0].step_index == 1

    def test_end_rewrites_description_and_passes(self):
        recorder = TelemetryRecorder()
        agent = make_agent(
            step(
                actions=[StubAction("click_element_by_index", {"index": 3}), StubAction("scroll")],
                next_goal="Open the cart",
            )
        )
        run_step(recorder, step_hooks(recorder), agent)
        assert recorder.steps[0].description == "click_element_by_index, scroll — Open the cart"
        assert recorder.steps[0].status == "passed"

    def test_action_result_error_marks_step_failed(self):
        recorder = TelemetryRecorder()
        agent = make_agent(
            step(actions=[StubAction("click_element_by_index")], errors=["Element not found"])
        )
        run_step(recorder, step_hooks(recorder), agent)
        assert recorder.steps[0].status == "failed"
        assert recorder.steps[0].note == "Element not found"

    def test_sequential_steps_get_increasing_order(self):
        recorder = TelemetryRecorder()
        hooks = step_hooks(recorder)
        agent = make_agent(step(next_goal="one"))
        run_step(recorder, hooks, agent)
        agent.history.history.append(step(next_goal="two"))
        run_step(recorder, hooks, agent)
        assert [s.order for s in recorder.steps] == [0, 1]
        assert [s.description for s in recorder.steps] == ["one", "two"]


class TestStepHooksDegradation:
    def test_empty_history_keeps_placeholder(self):
        recorder = TelemetryRecorder()
        run_step(recorder, step_hooks(recorder), make_agent())
        assert recorder.steps[0].description == "step 1"

    def test_model_output_none_keeps_placeholder(self):
        recorder = TelemetryRecorder()
        item = SimpleNamespace(model_output=None, result=[SimpleNamespace(error=None)])
        run_step(recorder, step_hooks(recorder), make_agent(item))
        assert recorder.steps[0].description == "step 1"
        assert recorder.steps[0].status == "passed"

    def test_action_dump_raising_falls_back_to_goal(self):
        recorder = TelemetryRecorder()
        agent = make_agent(
            step(actions=[StubAction("broken", raise_on_dump=True)], next_goal="Do the thing")
        )
        run_step(recorder, step_hooks(recorder), agent)
        assert recorder.steps[0].description == "Do the thing"

    def test_empty_output_keeps_placeholder(self):
        recorder = TelemetryRecorder()
        run_step(recorder, step_hooks(recorder), make_agent(step()))
        assert recorder.steps[0].description == "step 1"

    def test_agent_without_history_attribute_is_harmless(self):
        recorder = TelemetryRecorder()
        run_step(recorder, step_hooks(recorder), SimpleNamespace())
        assert recorder.steps[0].description == "step 1"

    def test_end_without_start_does_nothing(self):
        recorder = TelemetryRecorder()
        _, on_step_end = step_hooks(recorder)
        asyncio.run(on_step_end(make_agent(step(next_goal="orphan"))))
        assert recorder.steps == []

    def test_hooks_accept_exactly_one_positional_arg(self):
        recorder = TelemetryRecorder()
        for hook in step_hooks(recorder):
            asyncio.run(hook(make_agent()))
