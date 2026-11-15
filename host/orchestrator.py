"""Orchestrator: voice/text in -> local router or budgeted Claude persona -> reply out."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from host import local_exec
from host.budget import RequestBudget
from host.claude_runner import RunResult, build_argv, run_claude
from host.intent import Intent, route
from host.lang import detect_language
from host.personas import PERSONAS, write_mcp_config
from host.session import SessionManager

MAX_QUERY = 2000


@dataclass
class Reply:
    text: str
    source: str            # "local" | "llm" | "limit" | "error"
    persona: str | None = None
    lang: str = "en"
    budget: str = ""       # e.g. "142/400"
    silent: bool = False   # show but do not speak (e.g. the confirmation of "stop")


class Orchestrator:
    def __init__(self, cfg: dict[str, Any], budget: RequestBudget, data_dir: Path,
                 on_event: Callable[[dict], None] = lambda e: None,
                 config_path: str | None = None,
                 runner: Callable[..., RunResult] = run_claude,
                 local_runner: Callable[[Intent, dict], str] = local_exec.execute):
        self.cfg, self.budget, self.on_event = cfg, budget, on_event
        self.config_path = config_path
        self._runner, self._local = runner, local_runner
        self.sessions = SessionManager(cfg["session"]["reset_every_turns"])
        self._mcp_dir = Path(data_dir) / "mcp"
        self._mcp_files: dict[str, Path] = {}

    def _mcp_file(self, persona: str) -> str:
        if persona not in self._mcp_files:
            a, o = self.cfg["approval"], self.cfg["overlay"]
            env = {"NYRO_APPROVAL_URL": f"http://{a['host']}:{a['port']}/approve",
                   "NYRO_OVERLAY_URL": f"http://{o['bridge_host']}:{o['bridge_port']}"}
            self._mcp_files[persona] = write_mcp_config(PERSONAS[persona], self._mcp_dir, self.config_path, env)
        return str(self._mcp_files[persona])

    def _effective_cfg(self, persona: str) -> tuple[dict[str, Any], float]:
        """Apply cfg['persona_overrides'][persona] (model/effort/max_turns/timeout_seconds) on top of the defaults.
        Heavy personas (documents, desktop control) get a stronger model and longer limits."""
        ov = (self.cfg.get("persona_overrides") or {}).get(persona) or {}
        eff = dict(self.cfg)
        for k in ("model", "effort"):
            if k in ov:
                eff[k] = ov[k]
        eff["claude"] = {**self.cfg["claude"], **({"max_turns": ov["max_turns"]} if "max_turns" in ov else {})}
        return eff, float(ov.get("timeout_seconds", self.cfg["claude"]["timeout_seconds"]))

    def _emit_budget(self) -> str:
        s = self.budget.status()
        self.on_event({"type": "budget", "used": s.used, "cap": s.cap, "warn": s.warn,
                       "exhausted": s.exhausted, "pace_warn": s.pace_warn})
        return s.label()

    def handle(self, text: str, lang: str | None = None) -> Reply:
        text = (text or "").strip()[:MAX_QUERY]
        if not text:
            return Reply("", "error")
        intent = route(text)
        lang = lang or intent.lang or detect_language(text)
        intent.lang = lang

        if intent.kind == "local":
            out = self._local(intent, self.cfg)
            return Reply(out, "local", intent.persona, lang, self._emit_budget(),
                         silent=intent.action == "stop_speaking")

        persona = PERSONAS[intent.persona or "screencoach"]
        if not self.budget.try_consume():
            return Reply(local_exec.msg(lang, "limit"), "limit", persona.name, lang, self._emit_budget())
        self._emit_budget()
        resume = self.sessions.resume_id(persona.name)
        eff, timeout = self._effective_cfg(persona.name)
        argv = build_argv(persona, self._mcp_file(persona.name), eff, resume)
        self.on_event({"type": "start", "persona": persona.name, "query": text})
        res = self._runner(argv, text, self.on_event, timeout=timeout)

        if res.is_error and not res.produced_output:
            self.budget.refund()           # nothing happened: do not charge the user's daily cap
            if resume:
                self.sessions.reset()      # a stale --resume id is the usual cause
            self.on_event({"type": "error", "error": res.error})
            return Reply(local_exec.msg(lang, "failed", err=res.error[:120]), "error", persona.name, lang, self._emit_budget())
        self.sessions.record(persona.name, res.session_id)
        self.on_event({"type": "done", "text": res.text, "cost_usd": res.cost_usd})
        return Reply(res.text.strip() or "...", "llm", persona.name, lang, self._emit_budget())
