"""SysOp MCP server: Windows hardware & window control."""
from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP

from host import sysop_impl as S
from host.mcp_servers import _common as C

mcp = FastMCP("nyro-sysop")


def _run(action: str, params: dict, fn):
    try:
        C.guard(action, params)
        return fn()
    except Exception as e:
        return C.as_error(e)


@mcp.tool()
def set_brightness(level: int) -> str:
    """Set display brightness 0-100."""
    return _run("set_brightness", {"level": level}, lambda: S.set_brightness(level))


@mcp.tool()
def set_volume(level: int) -> str:
    """Set master volume 0-100."""
    return _run("set_volume", {"level": level}, lambda: S.set_volume(level))


@mcp.tool()
def toggle_mute() -> str:
    """Toggle system mute."""
    return _run("toggle_mute", {}, S.toggle_mute)


@mcp.tool()
def manage_window(app_name: str, action: str) -> str:
    """Window control by title substring. action: snap_left, snap_right, maximize, minimize, restore, focus."""
    return _run("manage_window", {"app_name": app_name, "action": action},
                lambda: S.manage_window(app_name, action))


@mcp.tool()
def close_tab(target: str = "") -> str:
    """Close a browser tab (Ctrl+W on a real browser window). target: text in the tab's title, e.g. 'youtube'.
    Leave empty to close the current tab. Cycles through tabs to find a match."""
    return _run("close_tab", {"target": target}, lambda: S.close_tab(target or None))


@mcp.tool()
def open_target(target: str, browser: str = "", query: str = "") -> str:
    """Open a website or app. target: youtube, google, gmail, whatsapp, facebook, instagram, github, linkedin,
    netflix, reddit, wikipedia, maps, claude, chatgpt, twitter, amazon, drive, or an app: chrome, edge, firefox,
    brave, notepad, calculator, paint, explorer, word, excel, powerpoint. browser (optional): chrome, edge,
    firefox, brave. query (optional): search text, only for youtube or google."""
    return _run("open_target", {"target": target, "browser": browser, "query": query},
                lambda: S.open_target(target, browser or None, query or None))


@mcp.tool()
def lock_workstation() -> str:
    """Lock the Windows session."""
    return _run("lock_workstation", {}, S.lock_workstation)


@mcp.tool()
def get_system_telemetry() -> str:
    """CPU, RAM, disk and battery stats as JSON."""
    return _run("get_system_telemetry", {}, lambda: json.dumps(S.get_system_telemetry()))


if __name__ == "__main__":
    mcp.run()
