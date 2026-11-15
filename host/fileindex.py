"""A small local search index (SQLite FTS5) so Nyro can find files from a few words: "the assignment pdf",
"invoice from last month", "the python file about sorting".

What is stored (all on this PC, in nyro_data/index.sqlite3): path, name, type, size, date, and up to ~2000
characters of text from the start of PDF / Word / text / code files. Nothing is uploaded; text only reaches
Claude if a search result is read out to it."""
from __future__ import annotations

import os
import re
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any, Callable

import logging

from host.organizer import categorize

logging.getLogger("pypdf").setLevel(logging.CRITICAL)   # scanned/odd PDFs make pypdf very chatty; we just skip them

TEXT_EXT = {"txt", "md", "csv", "json", "xml", "yaml", "yml", "html", "htm", "css", "py", "js", "ts", "cpp", "c", "h",
            "hpp", "java", "kt", "dart", "cs", "go", "rs", "sql", "sh", "php", "rb", "swift", "ini", "toml", "log", "tex"}
SKIP_DIRS = {"node_modules", "__pycache__", ".git", ".venv", "venv", "appdata", "$recycle.bin", "system volume information"}
MAX_CONTENT = 2000
MAX_EXTRACT_BYTES = 30 * 1024 * 1024
MAX_FILES_PER_ROOT = 50_000
STOPWORDS = {"my", "the", "a", "an", "of", "for", "file", "files", "folder", "document", "documents", "please", "find",
             "search", "where", "is", "are", "that", "i", "downloaded", "named", "called", "from", "in", "on", "open",
             "show", "me", "to", "and", "it", "this", "latest", "one", "about", "with", "kholo", "dhundo", "mera", "meri"}


def extract_text(path: Path) -> str:
    """First ~2000 characters of readable text, '' for anything else. Never raises."""
    ext = path.suffix.lower().lstrip(".")
    try:
        if path.stat().st_size > MAX_EXTRACT_BYTES:
            return ""
        if ext in TEXT_EXT:
            return " ".join(path.read_bytes()[:20_000].decode("utf-8", "replace").split())[:MAX_CONTENT]
        if ext == "pdf":
            from pypdf import PdfReader
            reader = PdfReader(str(path))
            out = ""
            for page in reader.pages[:3]:
                out += " " + (page.extract_text() or "")
                if len(out) > MAX_CONTENT:
                    break
            return " ".join(out.split())[:MAX_CONTENT]
        if ext == "docx":
            from docx import Document
            return " ".join(" ".join(p.text for p in Document(str(path)).paragraphs[:60]).split())[:MAX_CONTENT]
    except Exception:
        return ""
    return ""


def _query(text: str, mode: str = "and") -> str | None:
    toks = [t for t in re.findall(r"[\w]+", text.lower()) if t not in STOPWORDS and len(t) > 1]
    if not toks:
        return None
    return (" AND " if mode == "and" else " OR ").join(f'"{t}"*' for t in toks[:8])


class FileIndex:
    def __init__(self, db_path: str | Path, roots: list[Path]):
        self.db_path = str(db_path)
        self.roots = [Path(r) for r in roots]
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self._c()) as c:
            c.execute("CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY, path TEXT UNIQUE, name TEXT, ext TEXT, "
                      "size INTEGER, mtime REAL, root TEXT, category TEXT, content_done INTEGER DEFAULT 0)")
            c.execute("CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(name, path, kind, content, "
                      "tokenize='unicode61 remove_diacritics 2')")
            c.commit()

    def _c(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path, timeout=30)
        c.row_factory = sqlite3.Row
        return c

    # ---------------------------------------------------------------- building
    def _fts_put(self, c: sqlite3.Connection, rid: int, name: str, path: str, kind: str, content: str) -> None:
        c.execute("DELETE FROM fts WHERE rowid=?", (rid,))
        c.execute("INSERT INTO fts(rowid, name, path, kind, content) VALUES(?,?,?,?,?)",
                  (rid, re.sub(r"[_\-.]+", " ", name), path.replace("\\", " ").replace("/", " "), kind, content))

    def update(self, extract_budget_s: float = 45.0, progress: Callable[[str], None] | None = None) -> dict[str, int]:
        """Scan the roots: add new/changed files, drop vanished ones, then read text for as many files as the time
        budget allows (the rest is picked up on the next call)."""
        stats = {"scanned": 0, "added": 0, "changed": 0, "removed": 0, "text_read": 0}
        seen: set[str] = set()
        with closing(self._c()) as c:
            for root in self.roots:
                if not root.is_dir():
                    continue
                n = 0
                for dp, dn, fn in os.walk(root):
                    dn[:] = [d for d in dn if not d.startswith(".") and d.lower() not in SKIP_DIRS]
                    for f in fn:
                        if f.startswith(".") or f.lower() in ("desktop.ini", "thumbs.db"):
                            continue
                        p = Path(dp) / f
                        try:
                            st = p.stat()
                        except OSError:
                            continue
                        n += 1
                        stats["scanned"] += 1
                        sp = str(p)
                        seen.add(sp)
                        row = c.execute("SELECT id, size, mtime FROM files WHERE path=?", (sp,)).fetchone()
                        ext = p.suffix.lower().lstrip(".")
                        cat = categorize(f)
                        if row is None:
                            cur = c.execute("INSERT INTO files(path,name,ext,size,mtime,root,category,content_done) "
                                            "VALUES(?,?,?,?,?,?,?,0)", (sp, f, ext, st.st_size, st.st_mtime, str(root), cat))
                            self._fts_put(c, cur.lastrowid, f, sp, f"{cat} {ext}", "")
                            stats["added"] += 1
                        elif row["size"] != st.st_size or abs(row["mtime"] - st.st_mtime) > 1:
                            c.execute("UPDATE files SET size=?, mtime=?, category=?, content_done=0 WHERE id=?",
                                      (st.st_size, st.st_mtime, cat, row["id"]))
                            self._fts_put(c, row["id"], f, sp, f"{cat} {ext}", "")
                            stats["changed"] += 1
                        if n >= MAX_FILES_PER_ROOT:
                            break
                c.commit()
            for row in c.execute("SELECT id, path FROM files").fetchall():
                if row["path"] not in seen and any(row["path"].startswith(str(r)) for r in self.roots):
                    c.execute("DELETE FROM files WHERE id=?", (row["id"],))
                    c.execute("DELETE FROM fts WHERE rowid=?", (row["id"],))
                    stats["removed"] += 1
            c.commit()
            deadline = time.monotonic() + extract_budget_s
            for row in c.execute("SELECT id, path, name, ext, category FROM files WHERE content_done=0 "
                                 "ORDER BY mtime DESC").fetchall():
                if time.monotonic() > deadline:
                    break
                text = extract_text(Path(row["path"])) if row["ext"] in TEXT_EXT | {"pdf", "docx"} else ""
                self._fts_put(c, row["id"], row["name"], row["path"], f"{row['category']} {row['ext']}", text)
                c.execute("UPDATE files SET content_done=1 WHERE id=?", (row["id"],))
                stats["text_read"] += 1
                if stats["text_read"] % 50 == 0:
                    c.commit()
            c.commit()
        if progress:
            progress(f"index: {stats}")
        return stats

    def note_moves(self, pairs: list[tuple[str, str]]) -> None:
        """After Nyro moves files, keep the index right without a full rescan."""
        with closing(self._c()) as c:
            for old, new in pairs:
                row = c.execute("SELECT id, content_done FROM files WHERE path=?", (old,)).fetchone()
                if not row:
                    continue
                p = Path(new)
                cat = categorize(p.name)
                c.execute("UPDATE files SET path=?, name=?, category=? WHERE id=?", (new, p.name, cat, row["id"]))
                old_content = c.execute("SELECT content FROM fts WHERE rowid=?", (row["id"],)).fetchone()
                self._fts_put(c, row["id"], p.name, new, f"{cat} {p.suffix.lower().lstrip('.')}",
                              old_content["content"] if old_content else "")
            c.commit()

    # ---------------------------------------------------------------- searching
    def search(self, text: str, limit: int = 8) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        with closing(self._c()) as c:
            for mode in ("and", "or"):
                q = _query(text, mode)
                if q is None:
                    break
                try:
                    rows = c.execute(
                        "SELECT f.path, f.name, f.category, f.size, f.mtime, "
                        "snippet(fts, 3, '[', ']', ' ... ', 12) AS snip, bm25(fts, 10.0, 3.0, 2.0, 1.0) AS rank "
                        "FROM fts JOIN files f ON f.id = fts.rowid WHERE fts MATCH ? "
                        "ORDER BY rank LIMIT ?", (q, limit * 2)).fetchall()
                except sqlite3.OperationalError:
                    rows = []
                if rows:
                    out = [dict(r) for r in rows]
                    break
            if not out:
                like = [t for t in re.findall(r"\w+", text.lower()) if t not in STOPWORDS and len(t) > 1][:3]
                if like:
                    cond = " AND ".join("lower(name) LIKE ?" for _ in like)
                    rows = c.execute(f"SELECT path, name, category, size, mtime, '' AS snip, 0 AS rank FROM files "
                                     f"WHERE {cond} ORDER BY mtime DESC LIMIT ?", [f"%{t}%" for t in like] + [limit]).fetchall()
                    out = [dict(r) for r in rows]
            if not out and _query(text) is None:      # "latest file" etc.: just the newest items
                rows = c.execute("SELECT path, name, category, size, mtime, '' AS snip, 0 AS rank FROM files "
                                 "ORDER BY mtime DESC LIMIT ?", (limit,)).fetchall()
                out = [dict(r) for r in rows]
        # a recent file beats an old one of similar relevance
        out.sort(key=lambda r: (round(r["rank"], 1), -r["mtime"]))
        return out[:limit]

    def status(self) -> dict[str, Any]:
        with closing(self._c()) as c:
            n = c.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            pending = c.execute("SELECT COUNT(*) FROM files WHERE content_done=0").fetchone()[0]
        return {"files": n, "text_pending": pending, "roots": [str(r) for r in self.roots]}
