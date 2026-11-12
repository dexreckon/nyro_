"""Windows hardware / window control. Windows-only libs are imported lazily."""
from __future__ import annotations

import sys
from typing import Any

IS_WINDOWS = sys.platform == "win32"
WINDOW_ACTIONS = {"snap_left", "snap_right", "maximize", "minimize", "restore", "focus"}


class Unsupported(RuntimeError):
    pass


def _need_windows() -> None:
    if not IS_WINDOWS:
        raise Unsupported("This action is only available on Windows.")


def clamp_level(level: Any) -> int:
    if isinstance(level, bool) or not isinstance(level, (int, float)):
        raise ValueError("level must be a number 0-100")
    return max(0, min(100, int(round(level))))


def set_brightness(level: int) -> str:
    _need_windows()
    import screen_brightness_control as sbc
    lv = clamp_level(level)
    sbc.set_brightness(lv)
    return f"Brightness set to {lv}%"


def _endpoint():
    from ctypes import POINTER, cast
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
    dev = AudioUtilities.GetSpeakers()
    iface = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return cast(iface, POINTER(IAudioEndpointVolume))


def set_volume(level: int) -> str:
    _need_windows()
    lv = clamp_level(level)
    _endpoint().SetMasterVolumeLevelScalar(lv / 100.0, None)
    return f"Volume set to {lv}%"


def toggle_mute() -> str:
    _need_windows()
    ep = _endpoint()
    new = not bool(ep.GetMute())
    ep.SetMute(int(new), None)
    return "Muted" if new else "Unmuted"


def lock_workstation() -> str:
    _need_windows()
    import ctypes
    if not ctypes.windll.user32.LockWorkStation():
        raise RuntimeError("LockWorkStation failed")
    return "Workstation locked"


def get_system_telemetry() -> dict[str, Any]:
    import psutil
    out: dict[str, Any] = {
        "cpu_percent": psutil.cpu_percent(interval=0.3),
        "ram_percent": psutil.virtual_memory().percent,
        "disk_percent": psutil.disk_usage("C:\\" if IS_WINDOWS else "/").percent,
    }
    b = psutil.sensors_battery()
    if b is not None:
        out["battery_percent"] = round(b.percent)
        out["plugged_in"] = bool(b.power_plugged)
    return out


def manage_window(app_name: str, action: str) -> str:
    if action not in WINDOW_ACTIONS:
        raise ValueError(f"action must be one of {sorted(WINDOW_ACTIONS)}")
    if not app_name or len(app_name) > 80:
        raise ValueError("bad app_name")
    _need_windows()
    import win32api, win32con, win32gui  # type: ignore

    needle = app_name.lower()
    found: list[int] = []

    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd) and needle in win32gui.GetWindowText(hwnd).lower():
            found.append(hwnd)

    win32gui.EnumWindows(cb, None)
    if not found:
        raise LookupError(f"No visible window matching '{app_name}'")
    h = found[0]
    if action == "maximize":
        win32gui.ShowWindow(h, win32con.SW_MAXIMIZE)
    elif action == "minimize":
        win32gui.ShowWindow(h, win32con.SW_MINIMIZE)
    elif action == "restore":
        win32gui.ShowWindow(h, win32con.SW_RESTORE)
    elif action == "focus":
        win32gui.ShowWindow(h, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(h)
    else:
        mon = win32api.GetMonitorInfo(win32api.MonitorFromWindow(h))["Work"]
        l, t, r, b = mon
        half = (r - l) // 2
        x = l if action == "snap_left" else l + half
        win32gui.ShowWindow(h, win32con.SW_RESTORE)
        win32gui.SetWindowPos(h, 0, x, t, half, b - t, win32con.SWP_NOZORDER)
    return f"{action} applied to '{win32gui.GetWindowText(h)}'"
