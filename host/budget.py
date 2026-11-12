"""Daily LLM request budget (SQLite). Local commands never touch this."""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Callable


@dataclass
class BudgetStatus:
    used: int
    cap: int
    remaining: int
    warn: bool
    exhausted: bool
    last_hour: int
    pace_warn: bool

    def label(self) -> str:
        return f"{self.used}/{self.cap}"


class RequestBudget:
    def __init__(self, db_path: str | Path, daily_cap: int = 400, warn_ratio: float = 0.8,
                 hourly_pace_warn: int = 60,
                 now: Callable[[], datetime] = datetime.now):
        self.db_path = str(db_path)
        self.cap = int(daily_cap)
        self.warn_ratio = warn_ratio
        self.hourly_pace_warn = hourly_pace_warn
        self._now = now
        self._init_db()

    # -- internals
    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        c.execute("PRAGMA journal_mode=WAL")
        return c

    def _init_db(self) -> None:
        with self._conn() as c:
            c.execute("CREATE TABLE IF NOT EXISTS requests (ts REAL NOT NULL, day TEXT NOT NULL)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_req_day ON requests(day)")
            c.execute("CREATE TABLE IF NOT EXISTS overrides (day TEXT PRIMARY KEY, extra INTEGER NOT NULL)")

    def _today(self) -> str:
        return self._now().date().isoformat()

    def _effective_cap(self, c: sqlite3.Connection, day: str) -> int:
        row = c.execute("SELECT extra FROM overrides WHERE day=?", (day,)).fetchone()
        return self.cap + (row[0] if row else 0)

    # -- public API
    def status(self) -> BudgetStatus:
        day = self._today()
        now_ts = self._now().timestamp()
        with self._conn() as c:
            used = c.execute("SELECT COUNT(*) FROM requests WHERE day=?", (day,)).fetchone()[0]
            hour = c.execute("SELECT COUNT(*) FROM requests WHERE ts>=?", (now_ts - 3600,)).fetchone()[0]
            cap = self._effective_cap(c, day)
        return BudgetStatus(
            used=used, cap=cap, remaining=max(cap - used, 0),
            warn=used >= int(cap * self.warn_ratio), exhausted=used >= cap,
            last_hour=hour, pace_warn=hour >= self.hourly_pace_warn,
        )

    def try_consume(self) -> bool:
        """Atomically reserve one request. False means cap reached (LLM must not be called)."""
        day = self._today()
        ts = self._now().timestamp()
        c = self._conn()
        try:
            c.execute("BEGIN IMMEDIATE")
            used = c.execute("SELECT COUNT(*) FROM requests WHERE day=?", (day,)).fetchone()[0]
            if used >= self._effective_cap(c, day):
                c.execute("ROLLBACK")
                return False
            c.execute("INSERT INTO requests(ts, day) VALUES (?, ?)", (ts, day))
            c.execute("COMMIT")
            return True
        finally:
            c.close()

    def override_today(self, extra: int = 100) -> None:
        """Manual tray 'override for today': raise today's cap by `extra`."""
        day = self._today()
        with self._conn() as c:
            c.execute(
                "INSERT INTO overrides(day, extra) VALUES (?, ?) "
                "ON CONFLICT(day) DO UPDATE SET extra = extra + excluded.extra", (day, extra))

    def refund(self) -> None:
        """Give back the most recent request (e.g. the CLI failed before answering)."""
        day = self._today()
        with self._conn() as c:
            c.execute("DELETE FROM requests WHERE rowid = (SELECT MAX(rowid) FROM requests WHERE day=?)", (day,))
