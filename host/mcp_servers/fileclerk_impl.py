"""FileClerk logic (jail-enforced). Pure functions so they are unit-testable."""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from host.firewall import PathJail, soft_delete

TEXT_EXT = {".md", ".txt", ".csv", ".json", ".py", ".c", ".cpp", ".h", ".hpp", ".js", ".ts",
            ".html", ".css", ".yaml", ".yml", ".log", ".ini", ".toml"}
DOC_EXT = {".pdf", ".docx"}
LIST_EXT = TEXT_EXT | DOC_EXT
# open_path may only launch passive document/media types, never anything executable
OPEN_EXT = DOC_EXT | {".md", ".txt", ".csv", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".pptx", ".xlsx", ".doc", ".xls", ".ppt"}
MAX_READ = 200_000
MAX_WRITE = 200_000
MAX_LIST = 500


def list_allowed_files(jail: PathJail, trash_dirname: str = ".nyro_trash", query: str = "") -> list[str]:
    """Readable files in the whitelist, newest first. `query` filters by case-insensitive substring of the path."""
    q = query.lower().strip()
    found: list[tuple[float, str]] = []
    for root in jail.roots:
        for dp, dn, fn in os.walk(root):
            dn[:] = [d for d in dn if d != trash_dirname and not d.startswith(".")]
            for f in fn:
                p = Path(dp) / f
                if p.suffix.lower() in LIST_EXT and not p.is_symlink():
                    rel = str(p.relative_to(root)).replace("\\", "/")
                    if q and q not in rel.lower():
                        continue
                    try:
                        found.append((p.stat().st_mtime, rel))
                    except OSError:
                        continue
    found.sort(reverse=True)
    return [rel for _, rel in found[:MAX_LIST]]


def _read_pdf(p: Path) -> str:
    from pypdf import PdfReader
    reader = PdfReader(str(p))
    parts: list[str] = []
    size = 0
    for i, page in enumerate(reader.pages, 1):
        text = (page.extract_text() or "").strip()
        parts.append(f"--- page {i} ---\n{text}")
        size += len(text)
        if size > MAX_READ:
            break
    out = "\n".join(parts)
    if not out.replace("--- page", "").strip() or size == 0:
        return "(No extractable text: this PDF looks scanned/image-only. Ask the user to describe it or use screen capture.)"
    return out


def _read_docx(p: Path) -> str:
    from docx import Document
    doc = Document(str(p))
    lines = [para.text for para in doc.paragraphs if para.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            lines.append(" | ".join(c.text.strip() for c in row.cells))
    return "\n".join(lines)


def read_document(jail: PathJail, rel_path: str) -> str:
    p = jail.resolve(rel_path)
    suffix = p.suffix.lower()
    if suffix not in LIST_EXT:
        raise ValueError(f"Unsupported file type: {p.suffix}")
    if not p.is_file():
        raise FileNotFoundError(rel_path)
    if suffix == ".pdf":
        text = _read_pdf(p)
    elif suffix == ".docx":
        text = _read_docx(p)
    else:
        data = p.read_bytes()[:MAX_READ + 1]
        text = data.decode("utf-8", errors="replace")
        return text[:MAX_READ] + ("\n...[truncated]" if len(data) > MAX_READ else "")
    return text[:MAX_READ] + ("\n...[truncated]" if len(text) > MAX_READ else "")


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


_BOLD = re.compile(r"\*\*(.+?)\*\*")


def _add_runs(paragraph, text: str) -> None:
    """Add text to a docx paragraph, turning **bold** into bold runs."""
    pos = 0
    for m in _BOLD.finditer(text):
        if m.start() > pos:
            paragraph.add_run(text[pos:m.start()])
        paragraph.add_run(m.group(1)).bold = True
        pos = m.end()
    if pos < len(text):
        paragraph.add_run(text[pos:])


def write_docx(jail: PathJail, rel_path: str, content: str, title: str = "") -> str:
    """Write a Word document from light markdown: #/##/### headings, -/* bullets, 1. numbered,
    ``` code blocks, **bold**, blank line = new paragraph."""
    p = jail.resolve(rel_path)
    if p.suffix.lower() != ".docx":
        raise ValueError("write_docx only writes .docx files")
    if len(content.encode("utf-8")) > MAX_WRITE:
        raise ValueError("Content too large")
    from docx import Document
    from docx.shared import Pt

    doc = Document()
    if title.strip():
        doc.add_heading(title.strip(), 0)
    in_code, code_lines = False, []
    for raw in content.splitlines():
        line = raw.rstrip()
        if line.strip().startswith("```"):
            if in_code:
                para = doc.add_paragraph()
                run = para.add_run("\n".join(code_lines))
                run.font.name, run.font.size = "Consolas", Pt(10)
                code_lines, in_code = [], False
            else:
                in_code = True
            continue
        if in_code:
            code_lines.append(raw)
            continue
        s = line.strip()
        if not s:
            continue
        m = re.match(r"^(#{1,3})\s+(.*)$", s)
        if m:
            doc.add_heading(m.group(2), len(m.group(1)))
        elif re.match(r"^[-*]\s+", s):
            _add_runs(doc.add_paragraph(style="List Bullet"), re.sub(r"^[-*]\s+", "", s))
        elif re.match(r"^\d+[.)]\s+", s):
            _add_runs(doc.add_paragraph(style="List Number"), re.sub(r"^\d+[.)]\s+", "", s))
        else:
            _add_runs(doc.add_paragraph(), s)
    if code_lines:  # unterminated block
        doc.add_paragraph().add_run("\n".join(code_lines)).font.name = "Consolas"

    p.parent.mkdir(parents=True, exist_ok=True)
    jail.resolve(str(p))  # re-check after mkdir
    fd, tmp = tempfile.mkstemp(dir=p.parent, suffix=".tmp")
    os.close(fd)
    try:
        doc.save(tmp)
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return str(p.relative_to(jail.root_for(p))).replace("\\", "/")


def open_path(jail: PathJail, rel_path: str = "") -> str:
    """Open a whitelisted folder or passive document in Explorer / its default app (Windows)."""
    p = jail.resolve(rel_path) if rel_path.strip() else jail.roots[0]
    if p.is_dir():
        pass
    elif p.is_file():
        if p.suffix.lower() not in OPEN_EXT:
            raise ValueError(f"Refusing to open {p.suffix} files")
    else:
        raise FileNotFoundError(rel_path)
    os.startfile(str(p))  # type: ignore[attr-defined]  # Windows only
    return str(p)


def soft_delete_file(jail: PathJail, rel_path: str, trash_dirname: str = ".nyro_trash") -> str:
    dest = soft_delete(jail, rel_path, trash_dirname)
    return str(dest.relative_to(jail.root_for(dest))).replace("\\", "/")
