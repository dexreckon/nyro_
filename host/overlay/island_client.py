"""Fire-and-forget UDP client for the Dynamic Island window. Never raises, never blocks."""
from __future__ import annotations

import json
import socket


class IslandClient:
    def __init__(self, port: int = 8767, enabled: bool = True):
        self.enabled = enabled
        self._addr = ("127.0.0.1", port)
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM) if enabled else None

    def _send(self, msg: dict) -> None:
        if not self._sock:
            return
        try:
            self._sock.sendto(json.dumps(msg).encode("utf-8"), self._addr)
        except OSError:
            pass

    def state(self, state: str, text: str = "") -> None:
        self._send({"state": state, "text": text})

    def level(self, value: float) -> None:
        self._send({"level": round(max(0.0, min(1.0, value)), 3)})


class NullIsland(IslandClient):
    def __init__(self):
        super().__init__(enabled=False)
