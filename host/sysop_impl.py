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
    if hasattr(dev, "EndpointVolume"):  # newer pycaw returns an AudioDevice wrapper
        return dev.EndpointVolume
    iface = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return cast(iface, POINTER(IAudioEndpointVolume))


def get_volume() -> int:
    _need_windows()
    return round(_endpoint().GetMasterVolumeLevelScalar() * 100)


def get_brightness() -> int:
    _need_windows()
    import screen_brightness_control as sbc
    v = sbc.get_brightness()
    return int(v[0] if isinstance(v, list) else v)


def change_volume(delta: int) -> str:
    return set_volume(get_volume() + int(delta))


def change_brightness(delta: int) -> str:
    return set_brightness(get_brightness() + int(delta))


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


SITES = {
    "youtube": "https://www.youtube.com", "google": "https://www.google.com", "gmail": "https://mail.google.com",
    "whatsapp": "https://web.whatsapp.com", "facebook": "https://www.facebook.com",
    "instagram": "https://www.instagram.com", "github": "https://github.com", "linkedin": "https://www.linkedin.com",
    "netflix": "https://www.netflix.com", "reddit": "https://www.reddit.com", "wikipedia": "https://www.wikipedia.org",
    "maps": "https://maps.google.com", "claude": "https://claude.ai", "chatgpt": "https://chatgpt.com",
    "twitter": "https://x.com", "amazon": "https://www.amazon.com", "drive": "https://drive.google.com",
    "gemini": "https://gemini.google.com",
}
# Windows Settings pages (viewing only: opening a page changes nothing)
SETTINGS = {"settings": "ms-settings:", "windowsupdate": "ms-settings:windowsupdate", "wifi": "ms-settings:network-wifi",
            "bluetooth": "ms-settings:bluetooth", "display": "ms-settings:display", "sound": "ms-settings:sound",
            "apps": "ms-settings:appsfeatures", "battery": "ms-settings:batterysaver"}
BROWSERS = {"chrome": "chrome", "edge": "msedge", "firefox": "firefox", "brave": "brave"}
APPS = {"notepad": "notepad", "calculator": "calc", "paint": "mspaint", "explorer": "explorer",
        "word": "winword", "excel": "excel", "powerpoint": "powerpnt"}
_SEARCH = {"youtube": "https://www.youtube.com/results?search_query=", "google": "https://www.google.com/search?q="}


def open_target(target: str, browser: str | None = None, query: str | None = None) -> str:
    """Open a whitelisted site/app. Sites open in `browser` (chrome/edge/firefox/brave) or the default one.
    Never runs arbitrary commands: only names in SITES / APPS / BROWSERS are accepted."""
    _need_windows()
    import ctypes
    from urllib.parse import quote_plus
    target = (target or "").lower().strip()
    if browser and browser not in BROWSERS:
        raise ValueError(f"unknown browser '{browser}'")
    if target in SITES:
        url = SITES[target]
        if query:
            if target not in _SEARCH:
                raise ValueError(f"cannot search on {target}")
            url = _SEARCH[target] + quote_plus(query[:200])
        exe = BROWSERS[browser] if browser else None
        r = ctypes.windll.shell32.ShellExecuteW(None, "open", exe or url, url if exe else None, None, 1)
        if r <= 32:
            raise RuntimeError(f"could not open {browser or 'the default browser'} (code {r})")
        return f"{target} in {browser}" if browser else target
    from host import ide as _ide
    if target in _ide.IDES:                      # "open vs code", "open android studio"
        return _ide.open_in_ide(target)
    if target in SETTINGS:
        r = ctypes.windll.shell32.ShellExecuteW(None, "open", SETTINGS[target], None, None, 1)
        if r <= 32:
            raise RuntimeError(f"could not open Windows Settings (code {r})")
        return "Windows Settings" if target == "settings" else f"Windows Settings: {target}"
    if target in BROWSERS or target in APPS:
        exe = BROWSERS.get(target) or APPS[target]
        r = ctypes.windll.shell32.ShellExecuteW(None, "open", exe, None, None, 1)
        if r <= 32:
            raise RuntimeError(f"{target} is not installed or could not start (code {r})")
        return target
    raise ValueError(f"'{target}' is not in the allowed list")


_BROWSER_EXES = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe"}


def _tap_keys(*vks: int) -> None:
    """Press the given virtual keys together (e.g. Ctrl+W), then release in reverse order."""
    import ctypes
    import time
    u = ctypes.windll.user32
    for vk in vks:
        u.keybd_event(vk, 0, 0, 0)
    time.sleep(0.03)
    for vk in reversed(vks):
        u.keybd_event(vk, 0, 2, 0)


def close_tab(target: str | None = None) -> str:
    """Close a browser tab with Ctrl+W. target = text in the tab's title (e.g. 'youtube'); None = the current tab.
    Only ever acts on real browser windows. Looks at the active tab of each browser window first, then cycles
    through one window's tabs (Ctrl+Tab, bounded) to find a match."""
    _need_windows()
    import time

    import psutil
    import win32gui  # type: ignore
    import win32process  # type: ignore

    VK_CTRL, VK_TAB, VK_W, VK_ALT = 0x11, 0x09, 0x57, 0x12

    def is_browser(h) -> bool:
        try:
            _, pid = win32process.GetWindowThreadProcessId(h)
            return psutil.Process(pid).name().lower() in _BROWSER_EXES
        except Exception:
            return False

    def title(h) -> str:
        return win32gui.GetWindowText(h)

    windows: list[int] = []  # top-to-bottom z-order

    def cb(h, _):
        t = title(h).strip()
        if win32gui.IsWindowVisible(h) and t and t.lower() != "nyro" and is_browser(h):
            windows.append(h)

    win32gui.EnumWindows(cb, None)
    if not windows:
        raise LookupError("No browser window is open")

    def focus(h) -> None:
        import ctypes
        win32gui.ShowWindow(h, 9)  # SW_RESTORE
        ctypes.windll.user32.keybd_event(VK_ALT, 0, 0, 0)  # lets SetForegroundWindow through the foreground lock
        ctypes.windll.user32.keybd_event(VK_ALT, 0, 2, 0)
        win32gui.SetForegroundWindow(h)
        time.sleep(0.2)
        if win32gui.GetForegroundWindow() != h:
            raise RuntimeError("Windows would not let me switch to the browser")

    needle = (target or "").lower().strip()
    if not needle:
        h = windows[0]
        focus(h)
        name = title(h)
        _tap_keys(VK_CTRL, VK_W)
        return f"the current tab ({name[:40]})"

    for h in windows:  # 1) a window whose ACTIVE tab matches
        if needle in title(h).lower():
            focus(h)
            name = title(h)
            _tap_keys(VK_CTRL, VK_W)
            return f"the '{name[:40]}' tab"

    for h in windows[:2]:  # 2) cycle through that window's tabs (returns to the start if nothing matches)
        focus(h)
        start = title(h)
        for _ in range(40):
            _tap_keys(VK_CTRL, VK_TAB)
            time.sleep(0.15)
            now = title(win32gui.GetForegroundWindow())
            if needle in now.lower():
                _tap_keys(VK_CTRL, VK_W)
                return f"the '{now[:40]}' tab"
            if now == start:
                break
    raise LookupError(f"I couldn't find a browser tab with '{needle}' in its title")


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
