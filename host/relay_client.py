"""Outbound WebSocket client: host <-> VPS relay <-> phone. Reconnects forever, thread-safe send()."""
from __future__ import annotations

import asyncio
import json
import os
import threading
from typing import Any, Callable

MAX_MSG = 512 * 1024


class RelayClient:
    def __init__(self, url: str, token: str, on_message: Callable[[dict], None]):
        self.url, self.token, self.on_message = url, token, on_message
        self._loop: asyncio.AbstractEventLoop | None = None
        self._q: asyncio.Queue | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._stop = threading.Event()
        self.connected = False

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._loop:
            self._loop.call_soon_threadsafe(lambda: self._q and self._q.put_nowait(None))

    def send(self, msg: dict[str, Any]) -> None:
        """Thread-safe. Dropped silently if not connected (the phone gets a snapshot on reconnect)."""
        if self._loop and self._q and self.connected:
            self._loop.call_soon_threadsafe(self._q.put_nowait, msg)

    def _run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._q = asyncio.Queue(maxsize=500)
        self._loop.run_until_complete(self._main())

    async def _main(self) -> None:
        import websockets
        backoff = 1.0
        while not self._stop.is_set():
            try:
                async with websockets.connect(self.url, max_size=MAX_MSG, ping_interval=20, ping_timeout=20) as ws:
                    await ws.send(json.dumps({"type": "auth", "role": "host", "token": self.token}))
                    reply = json.loads(await asyncio.wait_for(ws.recv(), 10))
                    if reply.get("type") != "auth_ok":
                        raise PermissionError(f"relay refused auth: {reply}")
                    self.connected, backoff = True, 1.0
                    await asyncio.gather(self._reader(ws), self._writer(ws))
            except Exception as e:
                print(f"[relay] {type(e).__name__}: {e}")
            finally:
                self.connected = False
            if self._stop.is_set():
                break
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)

    async def _reader(self, ws) -> None:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(msg, dict):
                try:
                    self.on_message(msg)
                except Exception as e:
                    print(f"[relay] handler error: {e}")

    async def _writer(self, ws) -> None:
        while True:
            msg = await self._q.get()
            if msg is None:
                await ws.close()
                return
            await ws.send(json.dumps(msg))


def token_from_env(cfg: dict[str, Any]) -> str:
    return os.environ.get(cfg["relay"]["token_env"], "")
