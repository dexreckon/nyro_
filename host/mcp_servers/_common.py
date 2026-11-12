"""Shared plumbing for all Nyro MCP servers: config, firewall guard, approvals."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from host.approval import request_approval
from host.config import load_config
from host.firewall import FirewallError, PathJail, RedBlockError, Risk, classify_action


def config() -> dict[str, Any]:
    return load_config(os.environ.get("NYRO_CONFIG") or None)


def data_dir() -> Path:
    d = Path(os.environ.get("NYRO_DATA_DIR") or config().get("data_dir", "nyro_data")).resolve()
    d.mkdir(parents=True, exist_ok=True)
    return d


def make_jail(cfg: dict[str, Any] | None = None) -> PathJail:
    cfg = cfg or config()
    roots = cfg["firewall"].get("allowed_roots", [])
    return PathJail(roots, cfg["firewall"])


class Denied(Exception):
    """Raised by guard(); message is safe to show to the model."""


def guard(action: str, params: dict[str, Any] | None = None, summary: str = "",
          cfg: dict[str, Any] | None = None, approver=request_approval) -> Risk:
    """Run the 3-tier gate. GREEN passes, YELLOW needs phone approval, RED is blocked."""
    cfg = cfg or config()
    params = params or {}
    tier = classify_action(action, params, cfg["firewall"])
    if tier is Risk.RED:
        raise Denied("Blocked by Nyro firewall (red tier).")
    if tier is Risk.YELLOW:
        ok = approver(action, summary or action, params, timeout=cfg["approval"]["timeout_seconds"])
        if not ok:
            raise Denied("The user did not approve this action.")
    return tier


def as_error(e: Exception) -> str:
    if isinstance(e, (Denied, FirewallError)):
        return f"DENIED: {e}"
    return f"ERROR: {type(e).__name__}: {e}"
