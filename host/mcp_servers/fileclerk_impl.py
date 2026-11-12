"""FileClerk logic (jail-enforced). Pure functions so they are unit-testable."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from host.firewall import PathJail, soft_delete

TEXT_EXT = {".md", ".txt", ".csv", ".json", ".py", ".c", ".cpp", ".h", ".hpp", ".js", ".ts",
            ".html", ".css", ".yaml", ".yml", ".log", ".ini", ".toml"}
MAX_READ = 200_000
MAX_WRITE = 200_000
MAX_LIST = 500


def list_allowed_files(jail: PathJail, trash_dirname: str = ".nyro_trash") -> list[str]:
    out: list[str] = []
    for root in jail.roots:
        for dp, dn, fn in os.walk(root):
            dn[:] = [d for d in dn if d != trash_dirname and not d.startswith(".")]
            for f in fn:
                p = Path(dp) / f
                if p.suffix.lower() in TEXT_EXT and not p.is_symlink():
                    out.append(str(p.relative_to(root)).replace("\\", "/"))
                    if len(out) >= MAX_LIST:
                        return out
    return sorted(out)


def read_document(jail: PathJail, rel_path: str) -> str:
    p = jail.resolve(rel_path)
    if p.suffix.lower() not in TEXT_EXT:
        raise ValueError(f"Unsupported file type: {p.suffix}")
    if not p.is_file():
        raise FileNotFoundError(rel_path)
    data = p.read_bytes()[:MAX_READ + 1]
    text = data.decode("utf-8", errors="replace")
    return text[:MAX_READ] + ("\n...[truncated]" if len(data) > MAX_READ else "")


def write_markdown(jail: PathJail, rel_path: str, content: str) -> str:
    p = jail.resolve(rel_path)
    if p.suffix.lower() != ".md":
        raise ValueError("write_markdown only writes .md files")
    if len(content.encode("utf-8")) > MAX_WRITE:
        raise ValueError("Content too large")
    p.parent.mkdir(parents=True, exist_ok=True)
    jail.resolve(str(p))  # re-check after mkdir
    fd, tmp = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return str(p.relative_to(jail.root_for(p))).replace("\\", "/")


def soft_delete_file(jail: PathJail, rel_path: str, trash_dirname: str = ".nyro_trash") -> str:
    dest = soft_delete(jail, rel_path, trash_dirname)
    return str(dest.relative_to(jail.root_for(dest))).replace("\\", "/")
