"""Foreground-window capture (Windows). Heavy imports are lazy so tests run anywhere."""
from __future__ import annotations

from PIL import Image

from .scaler import ScaledCapture, downscale_to_webp


def foreground_window_rect() -> tuple[int, int, int, int]:
    """Return (left, top, width, height) in physical pixels of the foreground window."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        raise RuntimeError("No foreground window")

    def title(h) -> str:
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(h, buf, 256)
        return buf.value

    # If the user typed the request in the Nyro window itself, explain the window behind it (next in Z-order).
    GW_HWNDNEXT = 2
    for _ in range(40):
        t = title(hwnd).strip().lower()
        if t and t != "nyro" and user32.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd):
            break
        hwnd = user32.GetWindow(hwnd, GW_HWNDNEXT)
        if not hwnd:
            raise RuntimeError("No window to capture")
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def grab_region(left: int, top: int, width: int, height: int) -> Image.Image:
    import mss

    with mss.mss() as sct:
        shot = sct.grab({"left": left, "top": top, "width": width, "height": height})
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")


def capture_foreground_scaled(target_width: int = 800, quality: int = 75) -> ScaledCapture:
    l, t, w, h = foreground_window_rect()
    return downscale_to_webp(grab_region(l, t, w, h), origin=(l, t), target_width=target_width, quality=quality)
