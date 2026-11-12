"""Local HTTP bridge on 127.0.0.1 that accepts JSON draw commands (stdlib only)."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

from .commands import CommandError, DrawCommand, validate_batch

MAX_BODY = 64 * 1024


def make_handler(on_commands: Callable[[list[DrawCommand]], None]):
    class Handler(BaseHTTPRequestHandler):
        server_version = "NyroOverlay/1"

        def log_message(self, *a):  # silence
            pass

        def _send(self, code: int, obj: dict) -> None:
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/health":
                self._send(200, {"ok": True})
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):
            # Block browser pages (CSRF to localhost): browsers always attach Origin.
            if self.headers.get("Origin") is not None:
                return self._send(403, {"error": "browser origins not allowed"})
            if self.path not in ("/draw", "/clear"):
                return self._send(404, {"error": "not found"})
            if self.path == "/clear":
                on_commands([DrawCommand("clear")])
                return self._send(200, {"ok": True})
            if (self.headers.get("Content-Type") or "").split(";")[0].strip() != "application/json":
                return self._send(415, {"error": "application/json required"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return self._send(400, {"error": "bad length"})
            if length <= 0 or length > MAX_BODY:
                return self._send(413, {"error": "bad body size"})
            try:
                raw = json.loads(self.rfile.read(length))
                cmds = validate_batch(raw)
            except (json.JSONDecodeError, CommandError) as e:
                return self._send(400, {"error": str(e)})
            on_commands(cmds)
            self._send(200, {"ok": True, "count": len(cmds)})

    return Handler


class OverlayBridge:
    def __init__(self, on_commands: Callable[[list[DrawCommand]], None],
                 host: str = "127.0.0.1", port: int = 8765):
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("Bridge must bind to loopback only")
        self._srv = ThreadingHTTPServer((host, port), make_handler(on_commands))
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return self._srv.server_address[1]

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._srv.shutdown()
        self._srv.server_close()
