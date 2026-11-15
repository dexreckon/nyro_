"""Coder MCP server: create source files (.py .cpp .js .dart ...) in a dedicated workspace and open them in an IDE."""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from host import ide
from host.mcp_servers import _common as C
from host.mcp_servers import coder_impl as K

mcp = FastMCP("nyro-coder")


@mcp.tool()
def write_code_file(rel_path: str, content: str) -> str:
    """Create a source file in the code workspace, e.g. 'hello.py', 'sort.cpp', 'main.dart', 'app.js',
    'project/utils/math.py'. content = the COMPLETE file text. Subfolders are created automatically.
    New files are created immediately; overwriting an existing file needs the user's approval."""
    try:
        cfg = C.config()
        jail = K.make_jail(cfg)
        action = "write_code_overwrite" if K.exists(jail, rel_path) else "write_code_new"
        C.guard(action, {"rel_path": rel_path, "bytes": len(content)}, f"Overwrite {rel_path} ({len(content)} chars)", cfg)
        return "Saved " + K.write_code_file(jail, rel_path, content)
    except Exception as e:
        return C.as_error(e)


@mcp.tool()
def read_code_file(rel_path: str) -> str:
    """Read a source file from the code workspace (to edit or extend it)."""
    try:
        cfg = C.config()
        C.guard("read_code_file", {"rel_path": rel_path}, cfg=cfg)
        return K.read_code_file(K.make_jail(cfg), rel_path)
    except Exception as e:
        return C.as_error(e)


@mcp.tool()
def list_code_files() -> str:
    """List files in the code workspace, newest first."""
    try:
        cfg = C.config()
        C.guard("list_code_files", cfg=cfg)
        return "\n".join(K.list_code_files(K.make_jail(cfg))) or "(workspace is empty)"
    except Exception as e:
        return C.as_error(e)


@mcp.tool()
def open_in_ide(rel_path: str = "", ide_name: str = "vscode") -> str:
    """Open a workspace file (empty rel_path = the whole workspace folder) in an IDE so the user sees it.
    ide_name: vscode (default), cursor, antigravity, androidstudio, pycharm, intellij, clion, webstorm,
    sublime, notepadpp, visualstudio. Fails with a message if that IDE is not installed."""
    try:
        cfg = C.config()
        C.guard("open_in_ide", {"rel_path": rel_path, "ide": ide_name}, cfg=cfg)
        return "Opened " + K.open_in_ide(K.make_jail(cfg), rel_path, ide_name)
    except Exception as e:
        return C.as_error(e)


@mcp.tool()
def installed_ides() -> str:
    """Which IDEs/editors are installed on this computer."""
    try:
        C.guard("list_code_files", cfg=C.config())
        return ", ".join(f"{ide.IDES[n][0]} ({n})" for n in ide.installed()) or "none found"
    except Exception as e:
        return C.as_error(e)


if __name__ == "__main__":
    mcp.run()
