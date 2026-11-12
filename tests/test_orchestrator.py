import json
import os
import stat
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from host.budget import RequestBudget
from host.claude_runner import build_argv, run_claude
from host.config import load_config
from host.orchestrator import Orchestrator
from host.personas import PERSONAS, mcp_config
from host.session import SessionManager

HERE = Path(__file__).resolve().parent


class SessionTests(unittest.TestCase):
    def test_reset_after_n_turns_and_on_persona_switch(self):
        s = SessionManager(reset_every=3)
        self.assertIsNone(s.resume_id("screencoach"))
        s.record("screencoach", "A")
        self.assertEqual(s.resume_id("screencoach"), "A")
        s.record("screencoach", "A")
        s.record("screencoach", "A")                      # 3 turns used
        self.assertIsNone(s.resume_id("screencoach"))     # reset
        s.record("screencoach", "B")
        self.assertIsNone(s.resume_id("sysop"))           # persona switch
        self.assertEqual(s.turns, 0)


class ArgvTests(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config()
        self.argv = build_argv(PERSONAS["screencoach"], "/tmp/x.json", self.cfg, "SID")

    def pair(self, flag):
        return self.argv[self.argv.index(flag) + 1]

    def test_pinned_cheap_and_scoped(self):
        self.assertEqual(self.pair("--model"), "claude-haiku-5-5")
        self.assertEqual(self.pair("--effort"), "low")
        self.assertEqual(self.pair("--tools"), "")                # no built-in tools at all
        self.assertIn("--strict-mcp-config", self.argv)
        self.assertEqual(self.pair("--permission-mode"), "dontAsk")
        self.assertEqual(self.pair("--resume"), "SID")
        tools = self.pair("--allowedTools").split(",")
        self.assertTrue(all(t.startswith("mcp__nyro-screencoach__") for t in tools))
        for forbidden in ("Bash", "Write", "Edit", "Read"):
            self.assertNotIn(forbidden, tools)

    def test_no_bare_by_default_and_no_prompt_in_argv(self):
        self.assertNotIn("--bare", self.argv)
        self.assertNotIn("hello", self.argv)

    def test_every_persona_only_own_server(self):
        for name, p in PERSONAS.items():
            self.assertTrue(all(t.startswith(f"mcp__{p.server}__") for t in p.allowed_tools()), name)
            cfgj = mcp_config(p)
            self.assertEqual(list(cfgj["mcpServers"]), [p.server])

    def test_personas_never_expose_dangerous_tools(self):
        for p in PERSONAS.values():
            for t in p.tools:
                self.assertNotIn(t, {"bash", "shell", "exec", "run_command", "write_file"})


def make_fake_cli(tmp: Path) -> str:
    sh = tmp / "claude"
    sh.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{HERE / "fake_claude.py"}" "$@"\n')
    sh.chmod(sh.stat().st_mode | stat.S_IEXEC)
    return str(sh)


@unittest.skipIf(os.name == "nt", "fake CLI uses a shell wrapper")
class RunnerAndOrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.cfg = load_config()
        self.cfg["claude"]["cli"] = make_fake_cli(t)
        self.cfg["claude"]["timeout_seconds"] = 5
        self.log = t / "calls.jsonl"
        os.environ["FAKE_CLAUDE_LOG"] = str(self.log)
        os.environ["FAKE_CLAUDE"] = "ok"
        self.budget = RequestBudget(t / "b.sqlite3", daily_cap=3, now=datetime.now)
        self.events = []
        self.local_calls = []
        self.orch = Orchestrator(self.cfg, self.budget, t, self.events.append,
                                 local_runner=lambda i, c: self.local_calls.append(i.action) or "ok-local")

    def tearDown(self):
        os.environ.pop("FAKE_CLAUDE_LOG", None)
        os.environ.pop("FAKE_CLAUDE", None)
        self.tmp.cleanup()

    def calls(self):
        return [json.loads(l) for l in self.log.read_text().splitlines()] if self.log.exists() else []

    def test_local_command_never_calls_claude_or_budget(self):
        r = self.orch.handle("volume 30")
        self.assertEqual((r.source, r.text), ("local", "ok-local"))
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.budget.status().used, 0)

    def test_llm_call_charges_budget_and_streams(self):
        r = self.orch.handle("guide me how to add a transition")
        self.assertEqual((r.source, r.text, r.persona), ("llm", "Hello", "screencoach"))
        self.assertEqual(self.budget.status().used, 1)
        self.assertEqual(r.budget, "1/3")
        self.assertEqual("".join(e["text"] for e in self.events if e["type"] == "text"), "Hello")
        self.assertTrue(any(e["type"] == "usage" and e["five_hour"] == 0.31 for e in self.events))
        c = self.calls()[0]
        self.assertEqual(c["prompt"], "guide me how to add a transition")  # prompt via stdin
        self.assertIn("claude-haiku-5-5", c["args"])

    def test_cap_stops_llm_but_not_local(self):
        for _ in range(3):
            self.assertEqual(self.orch.handle("explain this slide").source, "llm")
        r = self.orch.handle("explain this slide")
        self.assertEqual(r.source, "limit")
        self.assertEqual(len(self.calls()), 3)               # 4th never reached the CLI
        self.assertEqual(self.orch.handle("mute").source, "local")  # still works
        self.assertEqual(self.orch.handle("آواز کم کرو").source, "local")

    def test_limit_message_localised(self):
        for _ in range(3):
            self.orch.handle("help me here")
        r = self.orch.handle("یہاں مدد کرو")
        self.assertEqual((r.source, r.lang), ("limit", "ur"))
        self.assertIn("AI", r.text)

    def test_cli_failure_is_refunded(self):
        os.environ["FAKE_CLAUDE"] = "missing_exit"
        r = self.orch.handle("help me here")
        self.assertEqual(r.source, "error")
        self.assertEqual(self.budget.status().used, 0)       # refunded
        self.assertTrue(any(e["type"] == "error" for e in self.events))

    def test_missing_cli(self):
        self.cfg["claude"]["cli"] = "/nonexistent/claude"
        r = self.orch.handle("help me here")
        self.assertEqual(r.source, "error")
        self.assertIn("not found", r.text)
        self.assertEqual(self.budget.status().used, 0)

    def test_timeout_kills_and_refunds(self):
        os.environ["FAKE_CLAUDE"] = "hang"
        self.cfg["claude"]["timeout_seconds"] = 1
        r = self.orch.handle("help me here")
        self.assertEqual(r.source, "error")
        self.assertEqual(self.budget.status().used, 0)

    def test_tool_use_events(self):
        os.environ["FAKE_CLAUDE"] = "tool"
        self.orch.handle("help me here")
        self.assertIn({"type": "tool", "name": "capture_foreground_window"}, self.events)

    def test_session_resume_then_reset(self):
        for _ in range(2):
            self.orch.handle("help me here")
        a = self.calls()
        self.assertNotIn("--resume", a[0]["args"])
        self.assertIn("--resume", a[1]["args"])
        self.orch.handle("go to google.com")                 # persona switch -> fresh
        self.assertNotIn("--resume", self.calls()[2]["args"])
        self.assertTrue(any("nyro-webpilot" in x for x in self.calls()[2]["args"]))

    def test_empty_and_long_input(self):
        self.assertEqual(self.orch.handle("   ").source, "error")
        self.orch.handle("help me here " + "x" * 10000)
        self.assertLessEqual(len(self.calls()[-1]["prompt"]), 2000)

    def test_failed_stale_resume_resets_session(self):
        self.orch.handle("help me here")
        os.environ["FAKE_CLAUDE"] = "missing_exit"
        self.orch.handle("help me here")
        self.assertIsNone(self.orch.sessions.session_id)

    def test_run_claude_direct(self):
        res = run_claude([self.cfg["claude"]["cli"], "-p"], "hi", timeout=5)
        self.assertEqual((res.text, res.is_error), ("Hello", False))
        self.assertAlmostEqual(res.cost_usd, 0.0002)


if __name__ == "__main__":
    unittest.main()


class McpEnvTests(unittest.TestCase):
    def test_mcp_config_carries_configured_ports(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = load_config()
            cfg["approval"]["port"], cfg["overlay"]["bridge_port"] = 9911, 9922
            o = Orchestrator(cfg, RequestBudget(Path(d) / "b.db", 5), Path(d))
            data = json.loads(Path(o._mcp_file("fileclerk")).read_text())
            env = data["mcpServers"]["nyro-fileclerk"]["env"]
            self.assertEqual(env["NYRO_APPROVAL_URL"], "http://127.0.0.1:9911/approve")
            self.assertEqual(env["NYRO_OVERLAY_URL"], "http://127.0.0.1:9922")
