"""Config loading for Nyro: nyro_config.json plus an optional git-ignored local override."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "nyro_config.json"
LOCAL_NAME = "nyro_config.local.json"


def deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in over.items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """Load the config; if `nyro_config.local.json` sits beside it, deep-merge it on top."""
    p = Path(path) if path else DEFAULT_CONFIG_PATH
    with p.open("r", encoding="utf-8") as fh:
        cfg = json.load(fh)
    local = p.with_name(LOCAL_NAME)
    if local.is_file() and local != p:
        with local.open("r", encoding="utf-8") as fh:
            cfg = deep_merge(cfg, json.load(fh))
    return cfg
