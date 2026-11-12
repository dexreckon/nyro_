"""Config loading for Nyro."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "nyro_config.json"


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    p = Path(path) if path else DEFAULT_CONFIG_PATH
    with p.open("r", encoding="utf-8") as fh:
        return json.load(fh)
