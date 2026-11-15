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


_island = None


def indicate(text: str = "Controlling your mouse and keyboard · Ctrl+Shift+Q stops") -> None:
    """Show on the Dynamic Island that Nyro is operating the mouse/keyboard (best effort, never raises)."""
    global _island
    try:
        if _island is None:
            from host.overlay.island_client import IslandClient
            _island = IslandClient(int((C.config().get("island") or {}).get("port", 8767)))
        _island.state("thinking", text)
    except Exception:
        pass


def _run(fn):
    try:
        return fn()
    except D.ControlDenied as e:
        return f"DENIED: {e}"
    except Exception as e:
        return C.as_error(e)


def _acting(fn):
    """Run a control action and keep the on-screen 'Nyro is controlling your mouse' indicator fresh."""
    indicate()
    return _run(fn)


@mcp.tool()
def request_control(reason: str, minutes: int = 5) -> str:
    """Start mouse/keyboard control for a few minutes. MUST be called first. reason: one short sentence of what
    you will do. By default the user's own request is the permission; if their settings require it, they are
    asked to approve. The user can stop you any time with Ctrl+Shift+Q or by moving the mouse to the
    top-left screen corner."""
    def go():
        cfg = C.config()
        mins = min(max(int(minutes), 1), D.MAX_MINUTES)
        if not (cfg.get("desktopctl") or {}).get("auto_approve", True):
            C.guard("request_control", {"reason": reason[:200], "minutes": mins},
                    f"Allow Nyro to control your mouse and keyboard for {mins} min: {reason[:120]}", cfg)
        msg = ctl().grant(reason, mins)
        indicate()
        return msg
    return _run(go)


@mcp.tool()
def open_target(target: str, browser: str = "", query: str = "") -> str:
    """Open a website, app or Windows Settings page. target: youtube, google, gmail, gemini, whatsapp, facebook,
    instagram, github, linkedin, netflix, reddit, wikipedia, maps, claude, chatgpt, twitter, amazon, drive;
    apps: chrome, edge, firefox, brave, notepad, calculator, paint, explorer, word, excel, powerpoint;
    IDEs: vscode, androidstudio, pycharm, intellij, clion, webstorm, cursor, antigravity, sublime, visualstudio;
    Settings pages (view only): settings, windowsupdate, wifi, bluetooth, display, sound, apps, battery.
    browser (optional): chrome, edge, firefox, brave. query (optional): search text for youtube or google.
    After opening, call capture_screen to continue (click/type)."""
    def go():
        cfg = C.config()
        C.guard("open_target", {"target": target, "browser": browser, "query": query}, cfg=cfg)
        indicate()
        from host import sysop_impl as S
        return S.open_target(target, browser or None, query or None)
    return _run(go)


@mcp.tool()
def close_tab(target: str = "") -> str:
    """Close a browser tab (Ctrl+W on a real browser window). target: text in the tab title, e.g. 'youtube';
    empty = the current tab."""
    def go():
        cfg = C.config()
        C.guard("close_tab", {"target": target}, cfg=cfg)
        indicate()
        from host import sysop_impl as S
        return S.close_tab(target or None)
    return _run(go)


@mcp.tool()
def capture_screen():
    """Screenshot of the whole screen. All click coordinates must be in THIS image's pixel space."""
    def go():
        cfg = C.config()
        C.guard("capture_foreground_window", cfg=cfg)  # same GREEN tier as the other screenshot tool
        indicate()
        cap = ctl().b.capture_screen()
        ctl().save_capture(cap.origin_x, cap.origin_y, cap.scale_factor, cap.orig_w, cap.orig_h)
        return [f"Screen image is {cap.scaled_w}x{cap.scaled_h} px. Use these pixel coordinates for click().",
                Image(data=cap.webp, format="webp")]
    return _run(go)


@mcp.tool()
def click(x: float, y: float, button: str = "left", double: bool = False) -> str:
    """Click at (x, y) in the last capture_screen image. button: left or right. double: double-click."""
    return _acting(lambda: ctl().click(x, y, button, double))


@mcp.tool()
def scroll(amount: int) -> str:
    """Scroll the wheel: positive = down, negative = up (notches, max 30)."""
    return _acting(lambda: ctl().scroll(amount))


@mcp.tool()
def type_text(text: str) -> str:
    """Type text into the focused field (max 2000 chars per call). Never passwords, card numbers or OTPs."""
    return _acting(lambda: ctl().type_text(text))


@mcp.tool()
def press_keys(keys: str) -> str:
    """Press a key or combo, e.g. 'enter', 'tab', 'ctrl+s', 'ctrl+shift+n'. The Windows key and Alt+F4 are blocked."""
    return _acting(lambda: ctl().press_keys(keys))


@mcp.tool()
def release_control() -> str:
    """Give control back to the user (call when finished)."""
    def go():
        released = ctl().revoke()
        try:
            from host.overlay.island_client import IslandClient
            IslandClient(int((C.config().get("island") or {}).get("port", 8767))).state("hide")
        except Exception:
            pass
        return "Control released." if released else "No active control."
    return _run(go)


if __name__ == "__main__":
    mcp.run()
