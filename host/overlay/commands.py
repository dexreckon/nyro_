"""Draw-command schema + validation (pure Python, no Qt)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

KINDS = {"cursor_point", "box_highlight", "arrow_connect", "freehand_circle", "step_hud", "clear"}
MAX_LABEL = 120
MAX_COORD = 20000
MAX_COMMANDS = 64


class CommandError(ValueError):
    pass


@dataclass
class DrawCommand:
    kind: str
    data: dict[str, Any] = field(default_factory=dict)


def _num(d: dict, key: str, lo: float = -MAX_COORD, hi: float = MAX_COORD, default=None) -> float:
    v = d.get(key, default)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise CommandError(f"'{key}' must be a number")
    if not lo <= v <= hi:
        raise CommandError(f"'{key}' out of range")
    return float(v)


def _label(d: dict) -> str:
    lab = d.get("label", "")
    if not isinstance(lab, str):
        raise CommandError("'label' must be a string")
    return lab[:MAX_LABEL]


def _color(d: dict) -> str:
    c = d.get("color", "red")
    if c not in {"red", "cyan", "yellow", "green"}:
        raise CommandError("bad color")
    return c


def validate_command(raw: Any) -> DrawCommand:
    if not isinstance(raw, dict):
        raise CommandError("command must be an object")
    kind = raw.get("type")
    if kind not in KINDS:
        raise CommandError(f"unknown type: {kind!r}")
    if kind == "clear":
        return DrawCommand("clear")
    if kind == "cursor_point":
        return DrawCommand(kind, {"x": _num(raw, "x"), "y": _num(raw, "y"), "label": _label(raw), "color": _color(raw)})
    if kind == "box_highlight":
        return DrawCommand(kind, {"x": _num(raw, "x"), "y": _num(raw, "y"),
                                  "w": _num(raw, "w", 1), "h": _num(raw, "h", 1),
                                  "label": _label(raw), "color": _color(raw)})
    if kind == "arrow_connect":
        return DrawCommand(kind, {"x1": _num(raw, "x1"), "y1": _num(raw, "y1"),
                                  "x2": _num(raw, "x2"), "y2": _num(raw, "y2"),
                                  "label": _label(raw), "color": _color(raw)})
    if kind == "freehand_circle":
        return DrawCommand(kind, {"cx": _num(raw, "cx"), "cy": _num(raw, "cy"),
                                  "rx": _num(raw, "rx", 1), "ry": _num(raw, "ry", 1),
                                  "label": _label(raw), "color": _color(raw)})
    # step_hud
    step, total = int(_num(raw, "step", 1, 999)), int(_num(raw, "total", 1, 999))
    if step > total:
        raise CommandError("step > total")
    return DrawCommand(kind, {"step": step, "total": total, "label": _label(raw)})


def validate_batch(raw: Any) -> list[DrawCommand]:
    """Accept a single command, a list, or {'commands': [...]}."""
    if isinstance(raw, dict) and "commands" in raw:
        raw = raw["commands"]
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        raise CommandError("body must be a command, list, or {'commands': [...]}")
    if len(raw) > MAX_COMMANDS:
        raise CommandError("too many commands")
    return [validate_command(c) for c in raw]
