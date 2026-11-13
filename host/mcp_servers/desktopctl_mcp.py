"""DesktopCtl MCP server: guarded mouse + keyboard control (needs a user-approved, time-limited grant)."""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP, Image

from host import desktopctl_impl as D
from host.mcp_servers import _common as C

mcp = FastMCP("nyro-desktopctl")
_ctl: D.DesktopControl | None = None


def ctl() -> D.DesktopControl:
    global _ctl
    if _ctl is None:
        _ctl = D.DesktopControl(C.data_dir(), D.PynputBackend())
    return _ctl


def _run(fn):
    try:
        return fn()
    except D.ControlDenied as e:
        return f"DENIED: {e}"
    except Exception as e:
        return C.as_error(e)


@mcp.tool()
def request_control(reason: str, minutes: int = 5) -> str:
    """Ask the user (approval on their phone/app) to let Nyro use the mouse and keyboard for a few minutes.
    MUST be called first. reason: one short sentence of what you will do. The user can stop you any time
    with Ctrl+Shift+Q or by moving the mouse to the top-left screen corner."""
    def go():
        cfg = C.config()
        C.guard("request_control", {"reason": reason[:200], "minutes": minutes},
                f"Allow Nyro to control your mouse and keyboard for {min(max(minutes, 1), D.MAX_MINUTES)} min: "
                f"{reason[:120]}", cfg)
        return ctl().grant(reason, minutes)
    return _run(go)


@mcp.tool()
def capture_screen():
    """Screenshot of the whole screen. All click coordinates must be in THIS image's pixel space."""
    def go():
        cfg = C.config()
        C.guard("capture_foreground_window", cfg=cfg)  # same GREEN tier as the other screenshot tool
        cap = ctl().b.capture_screen()
        ctl().save_capture(cap.origin_x, cap.origin_y, cap.scale_factor, cap.orig_w, cap.orig_h)
        return [f"Screen image is {cap.scaled_w}x{cap.scaled_h} px. Use these pixel coordinates for click().",
                Image(data=cap.webp, format="webp")]
    return _run(go)


@mcp.tool()
def click(x: float, y: float, button: str = "left", double: bool = False) -> str:
    """Click at (x, y) in the last capture_screen image. button: left or right. double: double-click."""
    return _run(lambda: ctl().click(x, y, button, double))


@mcp.tool()
def scroll(amount: int) -> str:
    """Scroll the wheel: positive = down, negative = up (notches, max 30)."""
    return _run(lambda: ctl().scroll(amount))


@mcp.tool()
def type_text(text: str) -> str:
    """Type text into the focused field (max 2000 chars per call). Never passwords, card numbers or OTPs."""
    return _run(lambda: ctl().type_text(text))


@mcp.tool()
def press_keys(keys: str) -> str:
    """Press a key or combo, e.g. 'enter', 'tab', 'ctrl+s', 'ctrl+shift+n'. The Windows key and Alt+F4 are blocked."""
    return _run(lambda: ctl().press_keys(keys))


@mcp.tool()
def release_control() -> str:
    """Give control back to the user (call when finished)."""
    return _run(lambda: "Control released." if ctl().revoke() else "No active control.")


if __name__ == "__main__":
    mcp.run()
