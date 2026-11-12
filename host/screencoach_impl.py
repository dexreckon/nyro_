"""ScreenCoach logic with injectable I/O so it is fully testable off-Windows."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Callable

from host.overlay.commands import validate_batch
from host.overlay.coords import scale_command, target_box_native
from host.overlay.scaler import ScaledCapture

KEEP_CAPTURES = 5
MAX_STEPS = 12


class ClickWaiter:
    """Arm a native-pixel box and block until the user left-clicks inside it."""

    def __init__(self, pad: float = 8.0):
        from host.overlay.click_advance import ClickAdvance
        self._evt = threading.Event()
        self._ca = ClickAdvance(self._evt.set, pad=pad)
        self._started = False

    def wait(self, box, timeout: float) -> bool:
        self._evt.clear()
        self._ca.arm(box)
        if not self._started:
            self._ca.start()
            self._started = True
        ok = self._evt.wait(timeout)
        self._ca.disarm()
        return ok


def _normalize(annotations_json: str) -> list[dict[str, Any]]:
    raw = json.loads(annotations_json) if isinstance(annotations_json, str) else annotations_json
    if isinstance(raw, dict) and "steps" in raw:
        steps = raw["steps"]
    elif isinstance(raw, dict) and "annotations" in raw:
        steps = [raw]
    elif isinstance(raw, list):
        steps = [{"annotations": raw}]
    elif isinstance(raw, dict):
        steps = [{"annotations": [raw]}]
    else:
        raise ValueError("annotations_json must be a list, a step, or {'steps': [...]}")
    if not isinstance(steps, list) or not steps or len(steps) > MAX_STEPS:
        raise ValueError(f"1-{MAX_STEPS} steps required")
    return steps


class Coach:
    def __init__(self, data_dir: Path, capture_fn: Callable[[], ScaledCapture],
                 draw_fn: Callable[[list[dict]], bool], clear_fn: Callable[[], bool],
                 speak_fn: Callable[[str], Any] | None = None,
                 waiter: Any | None = None, click_timeout: float = 90.0):
        self.dir = Path(data_dir) / "captures"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.meta_path = self.dir / "last_capture.json"
        self._capture_fn, self._draw, self._clear = capture_fn, draw_fn, clear_fn
        self._speak = speak_fn or (lambda _t: None)
        self._waiter = waiter
        self._timeout = click_timeout

    # ------------------------------------------------------------ capture
    def capture(self) -> dict[str, Any]:
        cap = self._capture_fn()
        path = self.dir / f"capture_{int(time.time() * 1000)}.webp"
        path.write_bytes(cap.webp)
        meta = {"scaled_w": cap.scaled_w, "scaled_h": cap.scaled_h, "orig_w": cap.orig_w,
                "orig_h": cap.orig_h, "origin_x": cap.origin_x, "origin_y": cap.origin_y}
        self.meta_path.write_text(json.dumps(meta))
        for old in sorted(self.dir.glob("capture_*.webp"))[:-KEEP_CAPTURES]:
            old.unlink(missing_ok=True)
        return {"path": str(path), "width": cap.scaled_w, "height": cap.scaled_h,
                "note": "Coordinates you return must be in this image's pixel space."}

    def _meta(self) -> ScaledCapture:
        if not self.meta_path.exists():
            raise RuntimeError("No capture yet: call capture_foreground_window first.")
        m = json.loads(self.meta_path.read_text())
        return ScaledCapture(b"", **m)

    # ------------------------------------------------------------- render
    def render(self, annotations_json: str, voice_script: str = "", auto_advance_on_click: bool = False) -> str:
        steps = _normalize(annotations_json)
        cap = self._meta()
        total = len(steps)
        for i, step in enumerate(steps, 1):
            anns = step.get("annotations", [])
            if not isinstance(anns, list):
                raise ValueError("annotations must be a list")
            native = [scale_command(cap, a) for a in anns]
            if total > 1:
                native.insert(0, {"type": "step_hud", "step": i, "total": total,
                                  "label": str(step.get("title", ""))[:60]})
            validate_batch(native)  # reject bad input before anything is drawn
            self._clear()
            if not self._draw(native):
                raise RuntimeError("Overlay is not running (start host/overlay/screen_canvas.py).")
            self._speak(step.get("speech") or (voice_script if i == 1 else ""))
            if not auto_advance_on_click:
                return f"Drew step {i}/{total}. Overlay stays until cleared."
            box = target_box_native(cap, anns, step.get("click_box"))  # scaled space in, native out
            if box is None:
                return f"Drew step {i}/{total}; no click target, not waiting."
            if self._waiter is None:
                self._waiter = ClickWaiter()
            if not self._waiter.wait(box, self._timeout):
                self._clear()
                return f"Stopped at step {i}/{total}: no click in the target within {int(self._timeout)}s."
            if step.get("recapture_after") and i < total:
                self._clear()
                time.sleep(0.6)  # let the UI settle
                info = self.capture()
                return (f"Step {i}/{total} done; UI changed. New screenshot: {info['path']} "
                        f"({info['width']}x{info['height']}). Plan the remaining {total - i} step(s) from it.")
        self._clear()
        return f"All {total} step(s) completed."

    def clear(self) -> str:
        self._clear()
        return "Cleared."
