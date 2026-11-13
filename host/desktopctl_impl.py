"""Guarded mouse + keyboard control. Pure logic here; the real input device is an injectable backend.

Safety model (all enforced in code, not just in the prompt):
  * Nothing works without a GRANT the user approved: time-limited (default 5 min) and action-limited.
  * Kill switches: revoke() (Ctrl+Shift+Q hotkey / tray / release_control) and the screen-corner failsafe.
  * Never types into or clicks inside terminals, registry/task manager, UAC, security or settings windows.
  * Never types card-number-like text. Key combos using the Windows key / Alt+F4 / Ctrl+Shift+Esc are refused.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable

GRANT_FILE = "control_grant.json"
CAPTURE_FILE = "last_screen.json"
DEFAULT_MINUTES = 5
MAX_MINUTES = 15
DEFAULT_ACTIONS = 150
MAX_TEXT = 2000
CORNER = 3  # px: pointer parked in the top-left corner = emergency stop

MODIFIERS = {"ctrl", "control", "alt", "shift"}
NAMED_KEYS = {"enter", "return", "tab", "esc", "escape", "backspace", "delete", "del", "space", "up", "down", "left",
              "right", "home", "end", "pageup", "pagedown", "insert"} | {f"f{i}" for i in range(1, 13)}
BLOCKED_NAMES = {"win", "windows", "cmd", "super", "meta", "command"}
BLOCKED_COMBOS = [{"alt", "f4"}, {"ctrl", "shift", "esc"}, {"ctrl", "shift", "escape"}, {"ctrl", "alt", "delete"},
                  {"ctrl", "alt", "del"}]

# windows we never type into / click inside
SENSITIVE_WINDOW = re.compile(
    r"(powershell|command prompt|\bcmd\b|windows terminal|\bterminal\b|registry editor|regedit|task manager|"
    r"user account control|windows security|group policy|device manager|control panel|\bservices\b|^settings$|"
    r"sign in|log in|login|password|passkey|paypal|bank)", re.I)
CARD_LIKE = re.compile(r"(?:\d[ -]?){13,19}")


class ControlDenied(Exception):
    """Message is safe to show to the model."""


def parse_combo(combo: str) -> list[str]:
    """'Ctrl+Shift+S' -> ['ctrl','shift','s'] (validated). Raises ControlDenied for unsafe/unknown keys."""
    parts = [p.strip().lower() for p in re.split(r"\+", combo) if p.strip()]
    if not parts or len(parts) > 4:
        raise ControlDenied("Give a key combo like 'ctrl+s' or 'enter' (max 4 keys).")
    if any(p in BLOCKED_NAMES for p in parts):
        raise ControlDenied("The Windows/Command key is not allowed.")
    if any(set(parts) >= b for b in BLOCKED_COMBOS):
        raise ControlDenied("That key combination is blocked for safety.")
    key = parts[-1]
    if not (len(key) == 1 or key in NAMED_KEYS):
        raise ControlDenied(f"Unknown key '{key}'.")
    if any(m not in MODIFIERS for m in parts[:-1]):
        raise ControlDenied("Only ctrl/alt/shift may precede the final key.")
    return parts


class DesktopControl:
    def __init__(self, data_dir: str | Path, backend: Any, now: Callable[[], float] = time.time):
        self.dir = Path(data_dir)
        self.b = backend
        self.now = now

    # ---- grant lifecycle
    @property
    def _grant_path(self) -> Path:
        return self.dir / GRANT_FILE

    def grant(self, reason: str, minutes: int = DEFAULT_MINUTES, max_actions: int = DEFAULT_ACTIONS) -> str:
        minutes = max(1, min(int(minutes), MAX_MINUTES))
        data = {"expires": self.now() + minutes * 60, "actions_left": int(max_actions), "reason": reason[:200]}
        self._grant_path.write_text(json.dumps(data))
        return f"Control granted for {minutes} min (max {max_actions} actions). Ctrl+Shift+Q stops it."

    def revoke(self) -> bool:
        try:
            self._grant_path.unlink()
            return True
        except FileNotFoundError:
            return False

    def _consume(self) -> None:
        """Validate the grant and spend one action. Raises ControlDenied if control is not allowed right now."""
        try:
            g = json.loads(self._grant_path.read_text())
        except (FileNotFoundError, ValueError):
            raise ControlDenied("No active permission. Call request_control first (the user must approve).")
        if self.now() > g.get("expires", 0):
            self.revoke()
            raise ControlDenied("Permission expired. Call request_control again if more is needed.")
        if g.get("actions_left", 0) <= 0:
            self.revoke()
            raise ControlDenied("Action limit reached. Call request_control again if more is needed.")
        x, y = self.b.position()
        if x <= CORNER and y <= CORNER:
            self.revoke()
            raise ControlDenied("Emergency stop: the pointer is in the screen corner. Control was revoked.")
        g["actions_left"] -= 1
        self._grant_path.write_text(json.dumps(g))

    def _check_window(self, what: str) -> None:
        title = (self.b.foreground_title() or "").strip()
        if SENSITIVE_WINDOW.search(title):
            raise ControlDenied(f"I will not {what} in this window ('{title[:40]}'): it is a terminal, system or "
                                f"sign-in window. Ask the user to do this step.")

    # ---- screen mapping
    def save_capture(self, origin_x: int, origin_y: int, scale: float, w: int, h: int) -> None:
        (self.dir / CAPTURE_FILE).write_text(json.dumps(
            {"origin_x": origin_x, "origin_y": origin_y, "scale": scale, "w": w, "h": h}))

    def to_native(self, x: float, y: float) -> tuple[int, int]:
        try:
            c = json.loads((self.dir / CAPTURE_FILE).read_text())
        except (FileNotFoundError, ValueError):
            raise ControlDenied("Call capture_screen first, then use coordinates from that image.")
        nx = c["origin_x"] + float(x) * c["scale"]
        ny = c["origin_y"] + float(y) * c["scale"]
        # stay on screen and out of the emergency-stop corner
        nx = min(max(nx, c["origin_x"] + CORNER + 2), c["origin_x"] + c["w"] - 1)
        ny = min(max(ny, c["origin_y"] + CORNER + 2), c["origin_y"] + c["h"] - 1)
        return round(nx), round(ny)

    # ---- actions
    def click(self, x: float, y: float, button: str = "left", double: bool = False) -> str:
        if button not in ("left", "right"):
            raise ControlDenied("button must be left or right")
        self._consume()
        self._check_window("click")
        nx, ny = self.to_native(x, y)
        self.b.click(nx, ny, button, bool(double))
        return f"{'Double-' if double else ''}clicked {button} at image ({x:.0f},{y:.0f})"

    def scroll(self, amount: int) -> str:
        """amount > 0 scrolls down, < 0 up (in wheel notches)."""
        self._consume()
        self._check_window("scroll")
        self.b.scroll(0, -max(-30, min(30, int(amount))))
        return f"Scrolled {'down' if amount > 0 else 'up'} {abs(int(amount))}"

    def type_text(self, text: str) -> str:
        if not text or len(text) > MAX_TEXT:
            raise ControlDenied(f"Text must be 1-{MAX_TEXT} characters; type long content in several calls.")
        if CARD_LIKE.search(text):
            raise ControlDenied("I will not type card-number-like text.")
        self._consume()
        self._check_window("type")
        self.b.type_text(text)
        return f"Typed {len(text)} characters"

    def press_keys(self, combo: str) -> str:
        parts = parse_combo(combo)
        self._consume()
        self._check_window("press keys")
        self.b.press(parts)
        return f"Pressed {'+'.join(parts)}"


# ---------------------------------------------------------------- real backend (Windows)

class PynputBackend:  # pragma: no cover - needs a Windows desktop
    def __init__(self):
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # physical pixels, same space as the capture
        except Exception:
            pass
        from pynput import keyboard, mouse
        self._kb, self._ms = keyboard, mouse
        self.k, self.m = keyboard.Controller(), mouse.Controller()

    def position(self) -> tuple[int, int]:
        return tuple(int(v) for v in self.m.position)  # type: ignore[return-value]

    def foreground_title(self) -> str:
        import ctypes
        u = ctypes.windll.user32
        hwnd = u.GetForegroundWindow()
        buf = ctypes.create_unicode_buffer(256)
        u.GetWindowTextW(hwnd, buf, 256)
        return buf.value

    def click(self, x: int, y: int, button: str, double: bool) -> None:
        self.m.position = (x, y)
        time.sleep(0.08)
        btn = self._ms.Button.left if button == "left" else self._ms.Button.right
        self.m.click(btn, 2 if double else 1)

    def scroll(self, dx: int, dy: int) -> None:
        self.m.scroll(dx, dy)

    def type_text(self, text: str) -> None:
        for ch in text:
            self.k.type(ch)
            time.sleep(0.006)

    def press(self, parts: list[str]) -> None:
        K = self._kb.Key
        alias = {"enter": K.enter, "return": K.enter, "tab": K.tab, "esc": K.esc, "escape": K.esc,
                 "backspace": K.backspace, "delete": K.delete, "del": K.delete, "space": K.space, "up": K.up,
                 "down": K.down, "left": K.left, "right": K.right, "home": K.home, "end": K.end,
                 "pageup": K.page_up, "pagedown": K.page_down, "insert": K.insert,
                 "ctrl": K.ctrl, "control": K.ctrl, "alt": K.alt, "shift": K.shift}
        alias.update({f"f{i}": getattr(K, f"f{i}") for i in range(1, 13)})
        *mods, key = parts
        key_obj = alias.get(key, key)
        held = [alias[m] for m in mods]
        for m in held:
            self.k.press(m)
        try:
            self.k.press(key_obj)
            self.k.release(key_obj)
        finally:
            for m in reversed(held):
                self.k.release(m)

    def capture_screen(self):
        """Whole primary monitor -> ScaledCapture (physical px origin)."""
        import mss
        from PIL import Image

        from host.overlay.scaler import downscale_to_webp
        with mss.mss() as sct:
            mon = sct.monitors[1]
            shot = sct.grab(mon)
            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        return downscale_to_webp(img, origin=(mon["left"], mon["top"]), target_width=1000, quality=75)
