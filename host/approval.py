"""Human-in-the-loop approvals for YELLOW actions.

MCP servers (separate processes) call `request_approval()`. The orchestrator runs an
`ApprovalBroker` that forwards the request to the phone PWA and blocks until the user
taps Approve/Reject. Fail closed: no broker / no answer / error => denied.
"""
from __future__ import annotations

import itertools
import json
import os
import threading
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

DEFAULT_URL = "http://127.0.0.1:8766/approve"


def request_approval(action: str, summary: str, params: dict[str, Any] | None = None,
                     timeout: float = 120.0, url: str | None = None) -> bool:
    url = url or os.environ.get("NYRO_APPROVAL_URL", DEFAULT_URL)
    body = json.dumps({"action": action, "summary": summary[:300], "params": params or {},
                       "timeout": timeout}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout + 5) as r:
            return bool(json.loads(r.read()).get("approved") is True)
    except Exception:
        return False


@dataclass
class Pending:
    id: int
    action: str
    summary: str
    params: dict[str, Any]
    event: threading.Event = field(default_factory=threading.Event)
    approved: bool = False


class ApprovalBroker:
    def __init__(self, notify: Callable[[Pending], None], host: str = "127.0.0.1", port: int = 8766):
        if host not in ("127.0.0.1", "localhost", "::1"):
            raise ValueError("Approval broker must bind to loopback only")
        self._notify = notify
        self._ids = itertools.count(1)
        self._pending: dict[int, Pending] = {}
        self._lock = threading.Lock()
        broker = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                if self.headers.get("Origin") is not None or self.path != "/approve":
                    self.send_response(403); self.end_headers(); return
                try:
                    n = int(self.headers.get("Content-Length", "0"))
                    if not 0 < n <= 32768:
                        raise ValueError
                    d = json.loads(self.rfile.read(n))
                    timeout = min(float(d.get("timeout", 120)), 600)
                    ok = broker.ask(str(d["action"]), str(d.get("summary", "")), dict(d.get("params", {})), timeout)
                except Exception:
                    self.send_response(400); self.end_headers(); return
                out = json.dumps({"approved": ok}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

        self._srv = ThreadingHTTPServer((host, port), H)
        self._thread = threading.Thread(target=self._srv.serve_forever, daemon=True)

    @property
    def port(self) -> int:
        return self._srv.server_address[1]

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._srv.shutdown()
        self._srv.server_close()

    def ask(self, action: str, summary: str, params: dict, timeout: float) -> bool:
        p = Pending(next(self._ids), action, summary, params)
        with self._lock:
            self._pending[p.id] = p
        try:
            self._notify(p)
            p.event.wait(timeout)
            return p.approved if p.event.is_set() else False
        finally:
            with self._lock:
                self._pending.pop(p.id, None)

    def resolve(self, approval_id: int, approved: bool) -> bool:
        with self._lock:
            p = self._pending.get(approval_id)
        if not p:
            return False
        p.approved = bool(approved)
        p.event.set()
        return True
