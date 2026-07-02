"""Minimal async CDP client over a raw websocket.

Protocol-dumb by design: correlates command ids to futures and fans events out
to one handler. Everything CDP-specific (targets, domains, sessions) lives in
the watcher.
"""

from __future__ import annotations

import asyncio
import json
import urllib.request
from collections.abc import Callable
from typing import Any

import websockets


class CDPError(Exception):
    pass


def discover_ws_url(http_url: str) -> str:
    """Resolve a CDP http endpoint to its browser websocket URL."""
    with urllib.request.urlopen(f"{http_url.rstrip('/')}/json/version", timeout=10) as resp:
        return str(json.load(resp)["webSocketDebuggerUrl"])


class CDPClient:
    def __init__(self, ws: Any) -> None:
        self._ws = ws
        self._next_id = 0
        self._futures: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._handler: Callable[[str, dict[str, Any], str | None], None] | None = None
        self._reader_task = asyncio.ensure_future(self._reader())

    @classmethod
    async def connect(cls, ws_url: str) -> CDPClient:
        # max_size=None: CDP messages (console args, response bodies) routinely
        # exceed the 1 MiB default, and fail exactly when telemetry matters.
        return cls(await websockets.connect(ws_url, max_size=None))

    def on_event(self, handler: Callable[[str, dict[str, Any], str | None], None]) -> None:
        self._handler = handler

    async def send(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        session_id: str | None = None,
        timeout: float = 10.0,
    ) -> dict[str, Any]:
        self._next_id += 1
        msg_id = self._next_id
        payload: dict[str, Any] = {"id": msg_id, "method": method, "params": params or {}}
        if session_id:
            payload["sessionId"] = session_id
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._futures[msg_id] = future
        try:
            await self._ws.send(json.dumps(payload))
            return await asyncio.wait_for(future, timeout)
        finally:
            self._futures.pop(msg_id, None)

    async def close(self) -> None:
        self._reader_task.cancel()
        await self._ws.close()

    async def _reader(self) -> None:
        try:
            async for raw in self._ws:
                msg = json.loads(raw)
                if "id" in msg:
                    future = self._futures.get(msg["id"])
                    if future is not None and not future.done():
                        if "error" in msg:
                            future.set_exception(CDPError(str(msg["error"])))
                        else:
                            future.set_result(msg.get("result") or {})
                elif self._handler is not None:
                    try:
                        self._handler(
                            str(msg.get("method", "")),
                            msg.get("params") or {},
                            msg.get("sessionId"),
                        )
                    except Exception:
                        pass
        except Exception as exc:
            for future in self._futures.values():
                if not future.done():
                    future.set_exception(CDPError(f"connection lost: {exc!r}"))
        finally:
            for future in self._futures.values():
                if not future.done():
                    future.set_exception(CDPError("connection closed"))
