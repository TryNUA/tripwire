import asyncio
import json

import pytest

websockets = pytest.importorskip("websockets")

from tripwire.cli.cdp import CDPClient, CDPError  # noqa: E402


class FakeCDPServer:
    """Speaks just enough CDP: scriptable responses plus server-pushed events."""

    def __init__(self):
        self.received = []
        self.responders = {}  # method -> dict result | {"error": ...}
        self._conn = None

    async def handle(self, ws):
        self._conn = ws
        async for raw in ws:
            msg = json.loads(raw)
            self.received.append(msg)
            reply = self.responders.get(msg["method"], {})
            if "error" in reply:
                await ws.send(json.dumps({"id": msg["id"], "error": reply["error"]}))
            else:
                await ws.send(json.dumps({"id": msg["id"], "result": reply}))

    async def emit(self, method, params, session_id=None):
        msg = {"method": method, "params": params}
        if session_id:
            msg["sessionId"] = session_id
        await self._conn.send(json.dumps(msg))


@pytest.fixture
async def server_and_client():
    server = FakeCDPServer()
    async with websockets.serve(server.handle, "127.0.0.1", 0) as ws_server:
        port = ws_server.sockets[0].getsockname()[1]
        client = await CDPClient.connect(f"ws://127.0.0.1:{port}/")
        yield server, client
        await client.close()


@pytest.mark.asyncio
class TestCDPClient:
    async def test_send_correlates_response(self, server_and_client):
        server, client = server_and_client
        server.responders["Browser.getVersion"] = {"product": "Chrome/1"}
        result = await client.send("Browser.getVersion")
        assert result == {"product": "Chrome/1"}
        assert server.received[0]["method"] == "Browser.getVersion"

    async def test_session_id_is_sent(self, server_and_client):
        server, client = server_and_client
        await client.send("Runtime.enable", session_id="sess-1")
        assert server.received[0]["sessionId"] == "sess-1"

    async def test_error_response_raises(self, server_and_client):
        server, client = server_and_client
        server.responders["Nope.nope"] = {"error": {"code": -32601, "message": "not found"}}
        with pytest.raises(CDPError, match="not found"):
            await client.send("Nope.nope")

    async def test_events_dispatch_with_session(self, server_and_client):
        server, client = server_and_client
        events = []
        client.on_event(lambda m, p, s: events.append((m, p, s)))
        await client.send("Runtime.enable")  # round-trip ensures connection is up
        await server.emit("Runtime.consoleAPICalled", {"type": "error"}, session_id="s1")
        await client.send("Runtime.enable")  # round-trip flushes the event
        assert events == [("Runtime.consoleAPICalled", {"type": "error"}, "s1")]

    async def test_handler_exception_does_not_kill_reader(self, server_and_client):
        server, client = server_and_client

        def broken(m, p, s):
            raise RuntimeError("handler broke")

        client.on_event(broken)
        await client.send("Runtime.enable")
        await server.emit("Runtime.consoleAPICalled", {}, None)
        assert await client.send("Runtime.enable") == {}

    async def test_connection_close_rejects_pending(self):
        async def hang(ws):
            async for _ in ws:
                await ws.close()

        async with websockets.serve(hang, "127.0.0.1", 0) as ws_server:
            port = ws_server.sockets[0].getsockname()[1]
            client = await CDPClient.connect(f"ws://127.0.0.1:{port}/")
            with pytest.raises((CDPError, asyncio.TimeoutError)):
                await client.send("Browser.getVersion", timeout=2.0)
            await client.close()
