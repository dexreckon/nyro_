"""Map draw commands from 800px-capture space to native desktop pixels."""
from __future__ import annotations

from typing import Any

from .scaler import ScaledCapture, clamp_scaled

_POINTS = {"cursor_point": [("x", "y")], "arrow_connect": [("x1", "y1"), ("x2", "y2")],
           "box_highlight": [("x", "y")], "freehand_circle": [("cx", "cy")]}
_LENGTHS = {"box_highlight": ["w", "h"], "freehand_circle": ["rx", "ry"]}


def scale_command(cap: ScaledCapture, cmd: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of cmd with coordinates mapped to native desktop pixels.

    step_hud / clear have no coordinates and pass through.
    """
    out = dict(cmd)
    s = cap.scale_factor
    kind = cmd.get("type")
    for xk, yk in _POINTS.get(kind, []):
        x, y = clamp_scaled(cap, float(cmd[xk]), float(cmd[yk]))
        out[xk] = round(cap.origin_x + x * s)
        out[yk] = round(cap.origin_y + y * s)
    for lk in _LENGTHS.get(kind, []):
        out[lk] = max(1, round(float(cmd[lk]) * s))
    return out


def target_box_native(cap: ScaledCapture, cmds: list[dict[str, Any]],
                      click_box: list[float] | None = None, point_half: float = 30.0):
    """Click target for auto-advance: explicit click_box, else first box, else around cursor."""
    s = cap.scale_factor
    if click_box:
        x, y, w, h = click_box
    else:
        box = next((c for c in cmds if c.get("type") == "box_highlight"), None)
        if box:
            x, y, w, h = box["x"], box["y"], box["w"], box["h"]
        else:
            cur = next((c for c in cmds if c.get("type") == "cursor_point"), None)
            if not cur:
                return None
            x, y, w, h = cur["x"] - point_half, cur["y"] - point_half, point_half * 2, point_half * 2
    return (cap.origin_x + x * s, cap.origin_y + y * s, w * s, h * s)
