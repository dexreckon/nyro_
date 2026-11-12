"""Relay tests against a REAL uvicorn server + real WebSocket clients (one event loop, like production)."""
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path

import uvicorn
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

from vps import relay
from vps.relay import Settings, create_app

TOKEN = "t" * 32


class Server:
    def __init__(self, tmp: str):
        self.app = create_app(Settings(TOKEN, str(Path(tmp) / "r.db"), pwa_dir=str(Path(tmp) / "none")))
        self.srv = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=0, log_level="error", ws_ping_interval=None))
        self.thread = threading.Thread(target=self.srv.run, daemon=True)
        self.thread.start()
        for _ in range(200):
            if self.srv.started:
                break
            time.sleep(0.02)
        self.port = self.srv.servers[0].sockets[0].getsockname()[1]

    def stop(self):
        self.srv.should_exit = True
        self.thread.join(5)


class Conn:
    def __init__(self, port, role, token=TOKEN, auth=True):
        self.ws = connect(f"ws://127.0.0.1:{port}/ws/{role}", open_timeout=5)
        if auth:
            try:
                self.send({"type": "auth", "token": token})
            except ConnectionClosed:      # relay may close first (e.g. lockout): the test inspects the close code
                pass

    def send(self, obj):
        self.ws.send(obj if isinstance(obj, str) else json.dumps(obj))

    def recv(self, timeout=3):
        return json.loads(self.ws.recv(timeout=timeout))

    def closed_code(self, timeout=3):
        try:
            self.ws.recv(timeout=timeout)
        except ConnectionClosed as e:
            return e.rcvd.code if e.rcvd else None
        return "still-open"

    def silent(self, timeout=0.4):
        try:
            self.ws.recv(timeout=timeout)
            return False
        except TimeoutError:
            return True

    def close(self):
        self.ws.close()


class RelayBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.s = Server(self.tmp.name)
        self.conns = []

    def tearDown(self):
        for c in self.conns:
            try:
                c.close()
            except Exception:
                pass
        self.s.stop()
        self.tmp.cleanup()

    def conn(self, role, **kw):
        c = Conn(self.s.port, role, **kw)
        self.conns.append(c)
        return c

    def mobile(self):
        m = self.conn("mobile")
        self.assertEqual(m.recv(), {"type": "auth_ok"})
        self.assertEqual(m.recv()["type"], "snapshot")
        return m

    def host(self, mobile=None):
        h = self.conn("host")
        self.assertEqual(h.recv(), {"type": "auth_ok"})
        if mobile:
            self.assertEqual(mobile.recv(), {"type": "status", "host_online": True})
        return h


class AuthTests(RelayBase):
    def test_settings_require_strong_token(self):
        for bad in ("short", ""):
            with self.assertRaises(RuntimeError):
                Settings(bad, ":memory:")

    def test_wrong_token(self):
        self.assertEqual(self.conn("mobile", token="x" * 32).closed_code(), 4401)

    def test_non_auth_first_message(self):
        c = self.conn("host", auth=False)
        c.send({"type": "command", "text": "hi"})
        self.assertEqual(c.closed_code(), 4401)

    def test_garbage_first_message(self):
        c = self.conn("host", auth=False)
        c.send("not json at all")
        self.assertEqual(c.closed_code(), 4401)

    def test_lockout_after_failures(self):
        for _ in range(relay.FAIL_LIMIT):
            self.assertEqual(self.conn("mobile", token="bad" * 12).closed_code(), 4401)
        self.assertEqual(self.conn("mobile").closed_code(), 4429)   # correct token, but IP is locked out

    def test_healthz_and_docs_disabled(self):
        import urllib.request, urllib.error
        base = f"http://127.0.0.1:{self.s.port}"
        self.assertEqual(json.loads(urllib.request.urlopen(base + "/healthz").read()),
                         {"ok": True, "host_online": False, "mobiles": 0})
        for p in ("/docs", "/openapi.json", "/redoc"):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                urllib.request.urlopen(base + p)
            self.assertEqual(cm.exception.code, 404)


class ForwardingTests(RelayBase):
    def test_offline_host_error(self):
        m = self.mobile()
        m.send({"type": "command", "text": "hi"})
        self.assertEqual(m.recv()["error"], "Laptop is offline")

    def test_roundtrip_and_sanitising(self):
        m = self.mobile()
        h = self.host(m)
        h.send({"type": "event", "event": "text", "text": "hello"})
        self.assertEqual(m.recv()["text"], "hello")
        m.send({"type": "command", "text": "help me here", "lang": "ur", "evil": 1})
        self.assertEqual(h.recv(), {"type": "command", "text": "help me here", "lang": "ur"})
        m.send({"type": "command", "text": "x", "lang": "fr"})
        self.assertEqual(h.recv()["lang"], None)

    def test_unknown_types_dropped(self):
        m = self.mobile(); h = self.host(m)
        m.send({"type": "shell", "cmd": "calc"})
        m.send({"type": "command", "text": "ok"})
        self.assertEqual(h.recv()["text"], "ok")
        h.send({"type": "evil"}); h.send("nope"); h.send([1]); h.send({"type": "approval_request", "id": "abc"})
        h.send({"type": "reply", "text": "fine"})
        self.assertEqual(m.recv()["text"], "fine")

    def test_size_and_rate_limits(self):
        m = self.mobile(); h = self.host(m)
        m.send({"type": "command", "text": "x" * 20000})
        self.assertEqual(m.recv()["error"], "message too large")
        for i in range(relay.CMD_LIMIT):
            m.send({"type": "command", "text": f"c{i}"})
            h.recv()
        m.send({"type": "command", "text": "one too many"})
        self.assertEqual(m.recv()["error"], "Slow down")

    def test_new_host_replaces_old(self):
        m = self.mobile()
        h1 = self.host(m)
        h2 = self.host(m)
        self.assertEqual(h1.closed_code(), 4000)
        m.send({"type": "command", "text": "to h2"})
        self.assertEqual(h2.recv()["text"], "to h2")

    def test_mobile_cap(self):
        ms = [self.mobile() for _ in range(relay.MAX_MOBILES)]
        extra = self.conn("mobile")
        self.assertEqual(extra.recv(), {"type": "auth_ok"})
        self.assertEqual(extra.closed_code(), 4503)

    def test_host_disconnect_notifies(self):
        m = self.mobile(); h = self.host(m)
        h.close()
        self.assertEqual(m.recv(), {"type": "status", "host_online": False})


class ApprovalTests(RelayBase):
    def test_flow_and_stale_responses(self):
        m = self.mobile(); h = self.host(m)
        h.send({"type": "approval_request", "id": 5, "action": "write_markdown", "summary": "Write a.md"})
        req = m.recv()
        self.assertEqual((req["id"], req["action"]), (5, "write_markdown"))
        m.send({"type": "approval_response", "id": 5, "approved": True})
        self.assertEqual(h.recv(), {"type": "approval_response", "id": 5, "approved": True})
        self.assertEqual(m.recv()["type"], "approval_resolved")
        m.send({"type": "approval_response", "id": 5, "approved": True})        # replay
        self.assertEqual(m.recv()["error"], "approval done")
        self.assertTrue(h.silent())                                              # host never saw the replay
        m.send({"type": "approval_response", "id": 999, "approved": True})       # never requested
        self.assertEqual(m.recv()["error"], "approval unknown")
        self.assertTrue(h.silent())

    def test_reject_is_forwarded(self):
        m = self.mobile(); h = self.host(m)
        h.send({"type": "approval_request", "id": 6, "action": "soft_delete_file", "summary": "x"})
        m.recv()
        m.send({"type": "approval_response", "id": 6, "approved": False})
        self.assertEqual(h.recv()["approved"], False)

    def test_expired_approval(self):
        m = self.mobile(); h = self.host(m)
        h.send({"type": "approval_request", "id": 7, "action": "a", "summary": "s"})
        m.recv()
        db = sqlite3.connect(self.s.app.state.store.path)
        db.execute("UPDATE approvals SET created=?", (time.time() - 500,)); db.commit(); db.close()
        m.send({"type": "approval_response", "id": 7, "approved": True})
        self.assertEqual(m.recv()["type"], "approval_expired")
        self.assertTrue(h.silent())

    def test_pending_survives_in_snapshot_then_expires_with_host(self):
        h = self.conn("host"); h.recv()
        h.send({"type": "event", "event": "text", "text": "past"})
        h.send({"type": "screenshot", "mime": "image/webp", "b64": "AAAA"})
        h.send({"type": "approval_request", "id": 9, "action": "a", "summary": "s"})
        time.sleep(0.3)
        m = self.conn("mobile"); m.recv()
        snap = m.recv()
        self.assertTrue(snap["host_online"])
        self.assertEqual(snap["screenshot"]["b64"], "AAAA")
        self.assertIn("past", json.dumps(snap["history"]))
        self.assertEqual(snap["pending_approvals"][0]["id"], 9)
        h.close()
        self.assertEqual(m.recv()["type"], "status")
        time.sleep(0.3)
        m2 = self.conn("mobile"); m2.recv()
        self.assertEqual(m2.recv()["pending_approvals"], [])     # host gone => approvals expired


class RelayClientIntegration(RelayBase):
    """The real host-side RelayClient talking to the real relay."""

    def test_client_auth_send_receive_reconnect(self):
        from host.relay_client import RelayClient
        got = []
        rc = RelayClient(f"ws://127.0.0.1:{self.s.port}/ws/host", TOKEN, got.append)
        rc.start()
        self.addCleanup(rc.stop)
        for _ in range(100):
            if rc.connected:
                break
            time.sleep(0.05)
        self.assertTrue(rc.connected)
        m = self.mobile()
        rc.send({"type": "reply", "text": "from host"})
        while True:
            msg = m.recv()
            if msg.get("type") == "reply":
                break
        self.assertEqual(msg["text"], "from host")
        m.send({"type": "command", "text": "from phone"})
        for _ in range(100):
            if got:
                break
            time.sleep(0.05)
        self.assertEqual(got[0]["text"], "from phone")

    def test_client_with_bad_token_never_connects(self):
        from host.relay_client import RelayClient
        rc = RelayClient(f"ws://127.0.0.1:{self.s.port}/ws/host", "z" * 32, lambda m: None)
        rc.start()
        self.addCleanup(rc.stop)
        time.sleep(1.5)
        self.assertFalse(rc.connected)


if __name__ == "__main__":
    unittest.main()


class HeadersTests(RelayBase):
    def test_security_headers_and_static_pwa(self):
        import urllib.request
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "index.html").write_text("<h1>hi</h1>")
            app = create_app(Settings(TOKEN, str(Path(self.tmp.name) / "h.db"), pwa_dir=d))
            import uvicorn
            u = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error"))
            t = threading.Thread(target=u.run, daemon=True); t.start()
            for _ in range(200):
                if u.started:
                    break
                time.sleep(0.02)
            try:
                port = u.servers[0].sockets[0].getsockname()[1]
                r = urllib.request.urlopen(f"http://127.0.0.1:{port}/")
                self.assertEqual(r.read(), b"<h1>hi</h1>")
                csp = r.headers["Content-Security-Policy"]
                self.assertIn("script-src 'self'", csp)
                self.assertNotIn("unsafe-inline", csp)
                self.assertEqual(r.headers["X-Content-Type-Options"], "nosniff")
                self.assertEqual(r.headers["X-Frame-Options"], "DENY")
            finally:
                u.should_exit = True; t.join(5)
