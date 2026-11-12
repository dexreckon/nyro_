"""FileClerk MCP server: whitelisted workspace archivist."""
from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from host.mcp_servers import _common as C
from host.mcp_servers import fileclerk_impl as F

mcp = FastMCP("nyro-fileclerk")


@mcp.tool()
def list_allowed_files() -> str:
    """List note/text files inside the user's whitelisted folders (relative paths)."""
    try:
        cfg = C.config()
        C.guard("list_allowed_files", cfg=cfg)
        return "\n".join(F.list_allowed_files(C.make_jail(cfg), cfg["firewall"]["trash_dirname"])) or "(no files)"
    except Exception as e:
        return C.as_error(e)


@mcp.tool()
def read_document(rel_path: str) -> str:
    """Read a text/markdown/code file from the whitelist (max 200KB)."""
    try:
        cfg = C.config()
        C.guard("read_document", {"rel_path": rel_path}, cfg=cfg)
        return F.read_document(C.make_jail(cfg), rel_path)
    except Exception as e:
        return C.as_error(e)


@mcp.tool()
def write_markdown(rel_path: str, content: str) -> str:
    """Create or overwrite a .md file. Needs the user's approval on their phone."""
    try:
        cfg = C.config()
        C.guard("write_markdown", {"rel_path": rel_path, "bytes": len(content)},
                f"Write {rel_path} ({len(content)} chars)", cfg)
        return "Saved " + F.write_markdown(C.make_jail(cfg), rel_path, content)
    except Exception as e:
        return C.as_error(e)


@mcp.tool()
def soft_delete_file(rel_path: str) -> str:
    """Move a file to .nyro_trash (never permanently deletes). Needs approval."""
    try:
        cfg = C.config()
        C.guard("soft_delete_file", {"rel_path": rel_path}, f"Move {rel_path} to trash", cfg)
        return "Moved to " + F.soft_delete_file(C.make_jail(cfg), rel_path, cfg["firewall"]["trash_dirname"])
    except Exception as e:
        return C.as_error(e)


if __name__ == "__main__":
    mcp.run()
