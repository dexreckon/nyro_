"""Tiny client for the overlay HTTP bridge."""
from __future__ import annotations

import json
import os
import urllib.request


def _base() -> str:
    return os.environ.get("NYRO_OVERLAY_URL", "http://127.0.0.1:8765")


def _post(path: str, body) -> bool:
    req = urllib.request.Request(_base() + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def draw(commands: list[dict]) -> bool:
    return _post("/draw", {"commands": commands})


def clear() -> bool:
    return _post("/clear", {})
