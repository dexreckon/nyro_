"""Nyro relay: lightweight FastAPI WebSocket broker between the Windows host and the phone PWA.

Run:  NYRO_PAIR_TOKEN=<long secret> uvicorn vps.relay:app --host 0.0.0.0 --port 8000
The relay never executes anything: it only forwards whitelisted JSON and stores approval state.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import sqlite3
import time
from collections import defaultdict, deque
from contextlib import closing
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

log = logging.getLogger("nyro.relay")
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
       "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
MIN_TOKEN_LEN = 24
AUTH_TIMEOUT = 8.0
MAX_MOBILE_MSG = 8 * 1024
MAX_HOST_MSG = 512 * 1024
MAX_MOBILES = 4
HISTORY = 120
APPROVAL_TTL = 130.0          # a bit over the host's 120s wait
FAIL_LIMIT, FAIL_WINDOW = 5, 300.0
CMD_LIMIT, CMD_WINDOW = 12, 60.0
MOBILE_TO_HOST = {"command", "approval_response", "sync"}
HOST_TO_MOBILE = {"event", "heard", "reply", "listening", "idle", "screenshot", "approval_request", "status"}


class Settings:
    def __init__(self, token: str | None = None, db_path: str | None = None, trust_proxy: bool | None = None,
                 pwa_dir: str | None = None):
        self.token = token if token is not None else os.environ.get("NYRO_PAIR_TOKEN", "")
        self.db_path = db_path or os.environ.get("NYRO_DB", "/data/relay.sqlite3")
        self.trust_proxy = trust_proxy if trust_proxy is not None else os.environ.get("NYRO_TRUST_PROXY") == "1"
        self.pwa_dir = pwa_dir or os.environ.get("NYRO_PWA_DIR") or str(Path(__file__).resolve().parent.parent / "pwa")
        if len(self.token) < MIN_TOKEN_LEN:
            raise RuntimeError(f"NYRO_PAIR_TOKEN must be set and at least {MIN_TOKEN_LEN} characters")


class Store:
    """SQLite: connection metadata + HITL approval state."""

    def __init__(self, path: str):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self._c()) as c:
            c.execute("CREATE TABLE IF NOT EXISTS sessions(id INTEGER PRIMARY KEY, role TEXT, ip TEXT, started REAL, ended REAL)")
            c.execute("CREATE TABLE IF NOT EXISTS approvals(id INTEGER PRIMARY KEY, action TEXT, summary TEXT, "
                      "status TEXT, created REAL, decided REAL)")

    def _c(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        c.execute("PRAGMA journal_mode=WAL")
        return c

    def session_start(self, role: str, ip: str) -> int:
        with closing(self._c()) as c:
            return c.execute("INSERT INTO sessions(role, ip, started) VALUES (?,?,?)", (role, ip, time.time())).lastrowid

    def session_end(self, sid: int) -> None:
        with closing(self._c()) as c:
            c.execute("UPDATE sessions SET ended=? WHERE id=?", (time.time(), sid))

    def approval_new(self, aid: int, action: str, summary: str) -> None:
        with closing(self._c()) as c:
            c.execute("INSERT OR REPLACE INTO approvals(id, action, summary, status, created) VALUES (?,?,?,?,?)",
                      (aid, action[:80], summary[:300], "pending", time.time()))

    def approval_decide(self, aid: int, approved: bool) -> str:
        """Return 'ok' | 'expired' | 'unknown' | 'done'. Only a pending, unexpired approval can be decided."""
        with closing(self._c()) as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT status, created FROM approvals WHERE id=?", (aid,)).fetchone()
            if not row:
                c.execute("ROLLBACK"); return "unknown"
            status, created = row
            if status != "pending":
                c.execute("ROLLBACK"); return "done"
            if time.time() - created > APPROVAL_TTL:
                c.execute("UPDATE approvals SET status='expired', decided=? WHERE id=?", (time.time(), aid))
                c.execute("COMMIT"); return "expired"
            c.execute("UPDATE approvals SET status=?, decided=? WHERE id=?",
                      ("approved" if approved else "rejected", time.time(), aid))
            c.execute("COMMIT"); return "ok"

    def expire_pending(self) -> None:
        with closing(self._c()) as c:
            c.execute("UPDATE approvals SET status='expired', decided=? WHERE status='pending'", (time.time(),))

    def pending(self) -> list[dict[str, Any]]:
        cutoff = time.time() - APPROVAL_TTL
        with closing(self._c()) as c:
            rows = c.execute("SELECT id, action, summary FROM approvals WHERE status='pending' AND created>?", (cutoff,)).fetchall()
        return [{"type": "approval_request", "id": r[0], "action": r[1], "summary": r[2]} for r in rows]


class Hub:
    def __init__(self, store: Store):
        self.store = store
        self.host: WebSocket | None = None
        self.mobiles: set[WebSocket] = set()
        self.history: deque[dict] = deque(maxlen=HISTORY)
        self.last_screenshot: dict | None = None

    async def to_mobiles(self, msg: dict) -> None:
        if msg.get("type") == "screenshot":
            self.last_screenshot = msg
        else:
            self.history.append(msg)
        for ws in list(self.mobiles):
            try:
                await ws.send_json(msg)
            except Exception as e:
                log.warning("dropping mobile after send failure: %r", e)
                self.mobiles.discard(ws)

    async def snapshot(self) -> dict:
        pending = await asyncio.to_thread(self.store.pending)
        return {"type": "snapshot", "host_online": self.host is not None, "history": list(self.history),
                "screenshot": self.last_screenshot, "pending_approvals": pending}


def create_app(settings: Settings | None = None) -> FastAPI:
    st = settings or Settings()
    store = Store(st.db_path)
    hub = Hub(store)
    app = FastAPI(title="Nyro Relay", docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def security_headers(request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("Content-Security-Policy", CSP)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        resp.headers.setdefault("Permissions-Policy", "microphone=(self), camera=(), geolocation=()")
        return resp
    fails: dict[str, deque] = defaultdict(deque)
    cmds: dict[int, deque] = defaultdict(deque)

    def client_ip(ws: WebSocket) -> str:
        if st.trust_proxy:
            fwd = ws.headers.get("x-forwarded-for", "")
            if fwd:
                return fwd.split(",")[-1].strip()[:64]
        return (ws.client.host if ws.client else "?")[:64]

    def locked_out(ip: str) -> bool:
        q, now = fails[ip], time.time()
        while q and now - q[0] > FAIL_WINDOW:
            q.popleft()
        return len(q) >= FAIL_LIMIT

    async def authenticate(ws: WebSocket, role: str) -> bool:
        ip = client_ip(ws)
        await ws.accept()
        if locked_out(ip):
            await ws.close(code=4429); return False
        try:
            msg = json.loads(await asyncio.wait_for(ws.receive_text(), AUTH_TIMEOUT))
            ok = (isinstance(msg, dict) and msg.get("type") == "auth" and isinstance(msg.get("token"), str)
                  and hmac.compare_digest(msg["token"].encode(), st.token.encode()))
        except Exception:
            ok = False
        if not ok:
            fails[ip].append(time.time())
            await ws.close(code=4401); return False
        await ws.send_json({"type": "auth_ok"})
        return True

    @app.get("/healthz")
    async def healthz():
        return JSONResponse({"ok": True, "host_online": hub.host is not None, "mobiles": len(hub.mobiles)})

    @app.websocket("/ws/host")
    async def ws_host(ws: WebSocket):
        if not await authenticate(ws, "host"):
            return
        if hub.host is not None:                       # a newer host replaces the old connection
            try:
                await hub.host.close(code=4000)
            except Exception:
                pass
        hub.host = ws
        sid = await asyncio.to_thread(store.session_start, "host", client_ip(ws))
        await hub.to_mobiles({"type": "status", "host_online": True})
        try:
            while True:
                raw = await ws.receive_text()
                if len(raw) > MAX_HOST_MSG:
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if not isinstance(msg, dict) or msg.get("type") not in HOST_TO_MOBILE:
                    continue
                if msg["type"] == "approval_request":
                    try:
                        await asyncio.to_thread(store.approval_new, int(msg["id"]), str(msg.get("action", "")), str(msg.get("summary", "")))
                    except (KeyError, TypeError, ValueError):
                        continue
                await hub.to_mobiles(msg)
        except WebSocketDisconnect:
            pass
        finally:
            if hub.host is ws:
                hub.host = None
                await asyncio.to_thread(store.expire_pending)
                await hub.to_mobiles({"type": "status", "host_online": False})
            await asyncio.to_thread(store.session_end, sid)

    @app.websocket("/ws/mobile")
    async def ws_mobile(ws: WebSocket):
        if not await authenticate(ws, "mobile"):
            return
        if len(hub.mobiles) >= MAX_MOBILES:
            await ws.close(code=4503); return
        hub.mobiles.add(ws)
        sid = await asyncio.to_thread(store.session_start, "mobile", client_ip(ws))
        await ws.send_json(await hub.snapshot())
        try:
            while True:
                raw = await ws.receive_text()
                if len(raw) > MAX_MOBILE_MSG:
                    await ws.send_json({"type": "error", "error": "message too large"}); continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                t = msg.get("type") if isinstance(msg, dict) else None
                if t not in MOBILE_TO_HOST:
                    continue
                if hub.host is None:
                    await ws.send_json({"type": "error", "error": "Laptop is offline"}); continue
                if t == "command":
                    q, now = cmds[id(ws)], time.time()
                    while q and now - q[0] > CMD_WINDOW:
                        q.popleft()
                    text = msg.get("text")
                    if not isinstance(text, str) or not text.strip():
                        continue
                    if len(q) >= CMD_LIMIT:
                        await ws.send_json({"type": "error", "error": "Slow down"}); continue
                    q.append(now)
                    msg = {"type": "command", "text": text[:2000], "lang": msg.get("lang") if msg.get("lang") in ("en", "ur") else None}
                elif t == "approval_response":
                    try:
                        aid, approved = int(msg["id"]), bool(msg.get("approved"))
                    except (KeyError, TypeError, ValueError):
                        continue
                    res = await asyncio.to_thread(store.approval_decide, aid, approved)
                    if res != "ok":
                        await ws.send_json({"type": "approval_expired" if res == "expired" else "error",
                                            "id": aid, "error": f"approval {res}"}); continue
                    msg = {"type": "approval_response", "id": aid, "approved": approved}
                    await hub.to_mobiles({"type": "approval_resolved", "id": aid, "approved": approved})
                else:
                    msg = {"type": "sync"}
                try:
                    await hub.host.send_text(json.dumps(msg))
                except Exception:
                    await ws.send_json({"type": "error", "error": "Laptop is offline"})
        except WebSocketDisconnect:
            pass
        finally:
            hub.mobiles.discard(ws)
            cmds.pop(id(ws), None)
            await asyncio.to_thread(store.session_end, sid)

    if Path(st.pwa_dir).is_dir():
        app.mount("/", StaticFiles(directory=st.pwa_dir, html=True), name="pwa")
    app.state.hub, app.state.store = hub, store
    return app


def _default_app() -> FastAPI:  # pragma: no cover
    return create_app()


def __getattr__(name: str):  # lazy `uvicorn vps.relay:app` so importing for tests needs no env var
    if name == "app":
        a = _default_app()
        globals()["app"] = a
        return a
    raise AttributeError(name)
