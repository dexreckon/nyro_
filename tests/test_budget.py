import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from host.budget import RequestBudget
from host.config import load_config


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "b.sqlite3"
        self.clock = Clock(datetime(2026, 11, 12, 9, 0, 0))

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, cap=5, **kw):
        return RequestBudget(self.db, daily_cap=cap, now=self.clock, **kw)

    def test_default_cap_is_400(self):
        self.assertEqual(load_config()["budget"]["daily_request_cap"], 400)

    def test_consume_until_cap(self):
        b = self.make(cap=3)
        self.assertTrue(all(b.try_consume() for _ in range(3)))
        self.assertFalse(b.try_consume())
        s = b.status()
        self.assertTrue(s.exhausted)
        self.assertEqual((s.used, s.remaining), (3, 0))

    def test_warn_threshold(self):
        b = self.make(cap=10, warn_ratio=0.8)
        for _ in range(7):
            b.try_consume()
        self.assertFalse(b.status().warn)
        b.try_consume()
        self.assertTrue(b.status().warn)

    def test_resets_next_day(self):
        b = self.make(cap=1)
        self.assertTrue(b.try_consume())
        self.assertFalse(b.try_consume())
        self.clock.t += timedelta(days=1)
        self.assertTrue(b.try_consume())

    def test_persists_across_instances(self):
        self.make(cap=2).try_consume()
        b2 = self.make(cap=2)
        self.assertEqual(b2.status().used, 1)

    def test_override_today(self):
        b = self.make(cap=1)
        b.try_consume()
        self.assertFalse(b.try_consume())
        b.override_today(2)
        self.assertTrue(b.try_consume())
        self.assertTrue(b.try_consume())
        self.assertFalse(b.try_consume())

    def test_refund(self):
        b = self.make(cap=1)
        b.try_consume()
        b.refund()
        self.assertEqual(b.status().used, 0)
        self.assertTrue(b.try_consume())

    def test_hourly_pace(self):
        b = self.make(cap=100, hourly_pace_warn=3)
        for _ in range(3):
            b.try_consume()
        self.assertTrue(b.status().pace_warn)
        self.clock.t += timedelta(hours=2)
        self.assertFalse(b.status().pace_warn)


if __name__ == "__main__":
    unittest.main()
