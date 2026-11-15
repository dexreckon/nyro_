"""File-manager actions shared by the instant commands and Claude's tools: search, organise, undo, arrange icons."""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from host import organizer as O
from host.fileindex import FileIndex

Approver = Callable[[str, str, dict], bool]      # (action, summary, params) -> approved?
STAMP = "index_updated.txt"
OPEN_EXT = {"pdf", "docx", "doc", "xlsx", "xls", "pptx", "ppt", "txt", "md", "csv", "png", "jpg", "jpeg", "gif", "webp",
            "mp4", "mkv", "mp3", "wav", "zip"}


def data_dir(cfg: dict[str, Any]) -> Path:
    d = Path(cfg.get("data_dir", "nyro_data")).resolve()
    d.mkdir(parents=True, exist_ok=True)
    return d


def index_roots(cfg: dict[str, Any]) -> list[Path]:
    names = (cfg.get("index") or {}).get("roots") or ["downloads", "desktop", "documents"]
    out = []
    for n in names:
        try:
            p = O.known_folder(n) if n.lower() in O._KNOWN else Path(n).expanduser()
        except O.OrganizerError:
            continue
        if p.is_dir() and not O._is_protected(p.resolve(), cfg):
            out.append(p.resolve())
    return out


def get_index(cfg: dict[str, Any]) -> FileIndex:
    return FileIndex(data_dir(cfg) / "index.sqlite3", index_roots(cfg))


def refresh_index(cfg: dict[str, Any], force: bool = False, budget_s: float = 30.0) -> dict[str, Any]:
    """Update the index if it is older than refresh_minutes (or empty / forced)."""
    idx = get_index(cfg)
    stamp = data_dir(cfg) / STAMP
    every = float((cfg.get("index") or {}).get("refresh_minutes", 20)) * 60
    fresh = stamp.exists() and time.time() - stamp.stat().st_mtime < every and idx.status()["files"] > 0
    if fresh and not force:
        return {"skipped": "index is fresh", **idx.status()}
    stats = idx.update(extract_budget_s=budget_s)
    stamp.write_text(str(time.time()))
    return {**stats, **idx.status()}


def _human_age(ts: float) -> str:
    d = time.time() - ts
    for limit, unit in ((3600, "min"), (86400, "h"), (86400 * 60, "days")):
        if d < limit:
            div = {"min": 60, "h": 3600, "days": 86400}[unit]
            return f"{max(1, int(d // div))} {unit} ago"
    return time.strftime("%d %b %Y", time.localtime(ts))


def search(cfg: dict[str, Any], query: str, limit: int = 8) -> list[dict[str, Any]]:
    refresh_index(cfg, budget_s=15.0)          # cheap when fresh
    return get_index(cfg).search(query, limit)


def format_results(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "No matching files found."
    lines = []
    for r in rows:
        snip = f" | ...{r['snip']}..." if r.get("snip") else ""
        lines.append(f"{r['path']} | {r['category']} | {_human_age(r['mtime'])}{snip}")
    return "\n".join(lines)


# ---------------------------------------------------------------- organising

def _root(cfg: dict[str, Any], folder: str) -> tuple[Path, bool]:
    root = O.resolve_root(folder, cfg)
    return root, root == O.known_folder("desktop").resolve()


def _plan(cfg: dict[str, Any], folder: str) -> tuple[O.Plan, Path, str]:
    root, is_desktop = _root(cfg, folder)
    oc = cfg.get("organizer") or {}
    plan = O.build_plan(root, is_desktop=is_desktop, min_age=float(oc.get("min_age_seconds", O.MIN_AGE_SECONDS)),
                        extra_rules=oc.get("extra_rules") or None)
    return plan, root, (root.name or folder)


def preview_organize(cfg: dict[str, Any], folder: str) -> str:
    plan, _root_p, name = _plan(cfg, folder)
    if not plan.moves:
        return f"{name} is already tidy ({len(plan.skipped)} items skipped)."
    ex = "\n".join(f"  {Path(m.src).name}  ->  {m.category}/" for m in plan.moves[:12])
    skipped = f"\nLeft alone: {len(plan.skipped)} items (folders, shortcuts, new or in-progress files)." if plan.skipped else ""
    return f"{plan.summary(name)}.\nExamples:\n{ex}{'' if len(plan.moves) <= 12 else f'  ...and {len(plan.moves) - 12} more'}{skipped}"


def organize(cfg: dict[str, Any], folder: str, approver: Approver) -> str:
    plan, root, name = _plan(cfg, folder)
    if not plan.moves:
        return f"{name} is already tidy."
    summary = plan.summary(name)
    if not approver("organize_folder", summary, {"folder": name, "files": len(plan.moves)}):
        return "Okay, I left everything as it was."
    res = O.apply_plan(plan, min_age=float((cfg.get("organizer") or {}).get("min_age_seconds", O.MIN_AGE_SECONDS)))
    try:
        get_index(cfg).note_moves(res.moved)
    except Exception:
        pass
    return f"{res.summary()} {', '.join(f'{n} {c}' for c, n in plan.counts().items())}. Say 'undo' to put them back."


def undo(cfg: dict[str, Any], folder: str | None = None) -> str:
    roots = O.allowed_roots(cfg)
    if folder:
        targets = {folder.lower(): O.resolve_root(folder, cfg)}
    else:
        targets = roots
    best, best_t = None, 0.0
    for key, p in targets.items():
        for b in O._read_journal(p)["batches"][-1:]:
            if b.get("time", 0) > best_t:
                best, best_t = p, b["time"]
    if best is None:
        return "There is nothing to undo."
    res = O.undo_last(best)
    try:
        get_index(cfg).note_moves(res.moved)
    except Exception:
        pass
    return f"Put {len(res.moved)} files back in {best.name}." + (f" {len(res.failed)} could not be moved back." if res.failed else "")


# ---------------------------------------------------------------- desktop icons

def arrange_icons(cfg: dict[str, Any], mode: str = "category") -> str:
    from host import desktop_icons as DI
    r = DI.arrange(data_dir(cfg), mode)
    groups = ", ".join(f"{n} {g}" for g, n in sorted(r["groups"].items(), key=lambda kv: -kv[1]))
    return f"Arranged {r['icons']} desktop icons ({groups}). Say 'restore desktop layout' to undo."


def restore_icons(cfg: dict[str, Any], original: bool = False) -> str:
    from host import desktop_icons as DI
    n = DI.restore(data_dir(cfg), original)
    return f"Put {n} desktop icons back where they were."


def organize_desktop(cfg: dict[str, Any], approver: Approver) -> str:
    """Desktop = move loose files into folders (shortcuts stay) and then tidy the icon layout."""
    parts = []
    try:
        parts.append(organize(cfg, "desktop", approver))
    except O.OrganizerError as e:
        parts.append(str(e))
    try:
        parts.append(arrange_icons(cfg))
    except Exception as e:
        parts.append(f"I could not arrange the icons: {e}")
    return " ".join(parts)


# ---------------------------------------------------------------- showing results

def _inside_known_roots(cfg: dict[str, Any], path: Path) -> bool:
    roots = index_roots(cfg) + list(O.allowed_roots(cfg).values())
    try:
        rp = path.resolve()
    except OSError:
        return False
    return any(rp == r or rp.is_relative_to(r) for r in roots)


def open_known_folder(cfg: dict[str, Any], name: str) -> str:
    """Open Downloads / Desktop / Documents / Pictures in File Explorer."""
    key = (name or "").lower()
    if key not in O._KNOWN:
        raise O.OrganizerError("I can open Downloads, Desktop, Documents or Pictures")
    p = O.known_folder(key)
    os.startfile(str(p))  # type: ignore[attr-defined]
    return f"Opened {p.name}"


def reveal(cfg: dict[str, Any], path: str) -> str:
    """Open Explorer with the file selected."""
    p = Path(path)
    if not p.exists() or not _inside_known_roots(cfg, p):
        raise O.OrganizerError("that file is not in a folder I manage")
    subprocess.Popen(["explorer.exe", f"/select,{p}"])
    return f"Showing {p.name} in {p.parent.name}"


def open_file(cfg: dict[str, Any], path: str) -> str:
    p = Path(path)
    if not p.is_file() or not _inside_known_roots(cfg, p):
        raise O.OrganizerError("that file is not in a folder I manage")
    if p.suffix.lower().lstrip(".") not in OPEN_EXT:
        raise O.OrganizerError(f"I will not launch {p.suffix} files")
    os.startfile(str(p))  # type: ignore[attr-defined]
    return f"Opened {p.name}"
