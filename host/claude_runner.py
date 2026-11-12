"""Run `claude -p` for one persona, streaming events. No shell, prompt goes via stdin."""
from __future__ import annotations

import json
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from host.personas import Persona


@dataclass
class RunResult:
    text: str = ""
    session_id: str | None = None
    is_error: bool = False
    error: str = ""
    cost_usd: float = 0.0
    produced_output: bool = False
    tools_used: list[str] = field(default_factory=list)
    rate_limit: dict[str, Any] | None = None


def build_argv(persona: Persona, mcp_config_path: str, cfg: dict[str, Any], resume: str | None = None) -> list[str]:
    c = cfg["claude"]
    argv = [c.get("cli", "claude"), "-p",
            "--model", cfg["model"], "--effort", cfg["effort"],
            "--system-prompt", persona.prompt,           # replaces the huge default prompt (token saver)
            "--tools", "",                               # no built-in tools: no Bash, Read, Write...
            "--mcp-config", mcp_config_path, "--strict-mcp-config",
            "--allowedTools", ",".join(persona.allowed_tools()),
            "--permission-mode", "dontAsk",              # anything not allowed is auto-denied, never prompts
            "--disable-slash-commands", "--setting-sources", "",
            "--max-turns", str(c.get("max_turns", 8)),
            "--output-format", "stream-json", "--verbose", "--include-partial-messages"]
    if c.get("use_bare"):
        argv.append("--bare")  # only works with an API key; off by default for Pro logins
    if resume:
        argv += ["--resume", resume]
    return argv


def _handle(ev: dict[str, Any], res: RunResult, on_event: Callable[[dict], None]) -> None:
    t = ev.get("type")
    if t == "stream_event":
        d = ev.get("event", {})
        if d.get("type") == "content_block_delta" and d.get("delta", {}).get("type") == "text_delta":
            res.produced_output = True
            on_event({"type": "text", "text": d["delta"]["text"]})
    elif t == "assistant":
        for block in ev.get("message", {}).get("content", []):
            if block.get("type") == "tool_use":
                name = block.get("name", "")
                res.tools_used.append(name)
                res.produced_output = True
                on_event({"type": "tool", "name": name.split("__")[-1]})
    elif t == "system" and ev.get("subtype") == "init":
        res.session_id = ev.get("session_id") or res.session_id
    elif t == "rate_limit_event":
        info = ev.get("rate_limit_info", {})
        res.rate_limit = info
        w = info.get("unifiedWindows", {}).get("five_hour", {})
        if "utilization" in w:
            on_event({"type": "usage", "five_hour": w["utilization"], "status": w.get("status")})
    elif t == "result":
        res.text = ev.get("result") or res.text
        res.session_id = ev.get("session_id") or res.session_id
        res.cost_usd = float(ev.get("total_cost_usd") or 0.0)
        res.is_error = bool(ev.get("is_error")) or str(ev.get("subtype", "success")).startswith("error")
        if res.is_error and not res.error:
            res.error = str(ev.get("result") or ev.get("subtype") or "error")[:300]


def run_claude(argv: list[str], prompt: str, on_event: Callable[[dict], None] = lambda e: None,
               timeout: float = 120.0, cwd: str | None = None) -> RunResult:
    res = RunResult()
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", cwd=cwd)
    except FileNotFoundError:
        res.is_error, res.error = True, "claude CLI not found (is Claude Code installed and on PATH?)"
        return res

    killer = threading.Timer(timeout, proc.kill)
    killer.start()
    stderr_buf: list[str] = []
    t_err = threading.Thread(target=lambda: stderr_buf.append(proc.stderr.read()), daemon=True)
    t_err.start()
    try:
        proc.stdin.write(prompt)
        proc.stdin.close()
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                _handle(json.loads(line), res, on_event)
            except json.JSONDecodeError:
                continue
        proc.wait()
    finally:
        killer.cancel()
        t_err.join(2)
        for pipe in (proc.stdin, proc.stdout, proc.stderr):
            try:
                pipe.close()
            except Exception:
                pass
    if proc.returncode not in (0, None) and not res.text:
        res.is_error = True
        err = "".join(stderr_buf).strip()
        res.error = res.error or (err[-300:] if err else f"claude exited with code {proc.returncode}")
        if proc.returncode in (-9, 137):
            res.error = "timed out"
    return res
