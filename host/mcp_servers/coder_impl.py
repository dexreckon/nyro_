"""Coder logic: create/read source files in ONE dedicated workspace folder and open them in an IDE."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from host import ide
from host.firewall import PathJail

CODE_EXT = {".py", ".js", ".mjs", ".ts", ".tsx", ".jsx", ".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".java", ".kt",
            ".dart", ".cs", ".go", ".rs", ".swift", ".php", ".rb", ".lua", ".r", ".sql", ".sh", ".html", ".css",
            ".scss", ".json", ".yaml", ".yml", ".xml", ".toml", ".ini", ".gradle", ".md", ".txt", ".csv"}
# files Windows can run or that change the system: never created here
BLOCKED_EXT = {".bat", ".cmd", ".ps1", ".vbs", ".vbe", ".reg", ".exe", ".dll", ".msi", ".lnk", ".scr", ".com", ".hta",
               ".jar", ".wsf", ".inf", ".sys"}
MAX_WRITE = 200_000
MAX_READ = 200_000
MAX_LIST = 300


def workspace(cfg: dict[str, Any]) -> Path:
    raw = (cfg.get("code") or {}).get("workspace") or "~/Documents/Nyro Code"
    p = Path(os.path.expanduser(raw)).resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def make_jail(cfg: dict[str, Any]) -> PathJail:
    return PathJail([workspace(cfg)], cfg.get("firewall", {}))


def check_ext(path: Path) -> None:
    ext = path.suffix.lower()
    if ext in BLOCKED_EXT:
        raise ValueError(f"{ext} files are not allowed (they can run or change the system)")
    if ext not in CODE_EXT:
        raise ValueError(f"unsupported file type '{ext or '(none)'}'. Allowed: {' '.join(sorted(CODE_EXT))}")


def exists(jail: PathJail, rel_path: str) -> bool:
    return jail.resolve(rel_path).exists()


def write_code_file(jail: PathJail, rel_path: str, content: str) -> str:
    p = jail.resolve(rel_path)
    check_ext(p)
    if len(content.encode("utf-8")) > MAX_WRITE:
        raise ValueError("file too large (max 200 KB)")
    p.parent.mkdir(parents=True, exist_ok=True)
    jail.resolve(str(p))  # re-check after mkdir
    fd, tmp = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return str(p.relative_to(jail.root_for(p))).replace("\\", "/")


def read_code_file(jail: PathJail, rel_path: str) -> str:
    p = jail.resolve(rel_path)
    check_ext(p)
    if not p.is_file():
        raise FileNotFoundError(rel_path)
    data = p.read_bytes()[:MAX_READ + 1]
    text = data.decode("utf-8", errors="replace")
    return text[:MAX_READ] + ("\n...[truncated]" if len(data) > MAX_READ else "")


def list_code_files(jail: PathJail) -> list[str]:
    found: list[tuple[float, str]] = []
    for root in jail.roots:
        for dp, dn, fn in os.walk(root):
            dn[:] = [d for d in dn if not d.startswith(".") and d not in {"node_modules", "__pycache__", ".nyro_trash"}]
            for f in fn:
                p = Path(dp) / f
                if p.suffix.lower() in CODE_EXT and not p.is_symlink():
                    try:
                        found.append((p.stat().st_mtime, str(p.relative_to(root)).replace("\\", "/")))
                    except OSError:
                        continue
    found.sort(reverse=True)
    return [rel for _, rel in found[:MAX_LIST]]


def open_in_ide(jail: PathJail, rel_path: str = "", ide_name: str = "") -> str:
    """Open a workspace file (or the whole workspace folder) in the named IDE. Returns '<IDE label>: <path>'."""
    p = jail.resolve(rel_path) if rel_path.strip() else jail.roots[0]
    if not p.exists():
        raise FileNotFoundError(rel_path)
    if p.is_file():
        check_ext(p)
    label = ide.open_in_ide(ide_name or ide.DEFAULT_IDE, p)
    return f"{label}: {p}"
