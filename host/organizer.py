"""Organise a folder (Downloads, Desktop) into category sub-folders.

Safety rules (enforced here, not in prompts):
  * only MOVES files; never deletes, overwrites or renames anything that already exists
  * existing sub-folders and everything inside them are left alone
  * skips files that look like in-progress downloads, were just modified, are hidden/system, or are in use
  * Desktop: shortcuts (.lnk/.url) are never moved
  * every run is journaled in a small hidden file inside the folder, so it can be undone
  * only folders on the configured allow-list (default: Downloads and Desktop) can be organised
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

JOURNAL = ".nyro_moves.json"
MAX_BATCHES = 20
MAX_FILES = 5000
MIN_AGE_SECONDS = 120

CATEGORIES: dict[str, set[str]] = {
    "Documents": {"pdf", "doc", "docx", "txt", "rtf", "odt", "md", "epub", "tex", "pages", "xps"},
    "Spreadsheets": {"xls", "xlsx", "xlsm", "csv", "tsv", "ods"},
    "Presentations": {"ppt", "pptx", "pps", "ppsx", "odp", "key"},
    "Images": {"jpg", "jpeg", "png", "gif", "webp", "bmp", "svg", "heic", "heif", "tif", "tiff", "ico", "raw", "psd", "ai"},
    "Videos": {"mp4", "mkv", "avi", "mov", "wmv", "webm", "flv", "m4v", "mpg", "mpeg", "3gp"},
    "Audio": {"mp3", "wav", "flac", "aac", "ogg", "m4a", "wma", "opus", "mid"},
    "Archives": {"zip", "rar", "7z", "tar", "gz", "bz2", "xz", "tgz", "cab"},
    "Installers": {"exe", "msi", "msix", "msixbundle", "appx", "apk", "iso", "dmg", "deb", "bat", "cmd"},
    "Code": {"py", "js", "mjs", "ts", "tsx", "jsx", "cpp", "cc", "c", "h", "hpp", "java", "kt", "dart", "cs", "go", "rs",
             "html", "htm", "css", "scss", "json", "xml", "yaml", "yml", "sql", "ipynb", "sh", "php", "rb", "swift", "ps1"},
    "Fonts": {"ttf", "otf", "woff", "woff2"},
}
# name-based rules win over extension for document-like files (first match wins)
NAME_RULES: list[tuple[re.Pattern[str], str, set[str]]] = [
    # matched against the name with _ - . turned into spaces, so "Lab_Task_Solutions" and "Exam-Notes" work
    (re.compile(r"assign|homework|\blabs?\d*\b|quiz|\bmid ?terms?\b|\bmid\b|final ?exam|\bexams?\b|\blec(?:ture|tures)?\b|"
                r"syllabus|past ?paper|tutorial|semester|\bcourse\b|study notes|\bweek \d+|\b[a-z]{3}\d{3}\b|"
                r"\btheory\b|\bsolutions?\b", re.I),
     "University", {"Documents", "Presentations", "Spreadsheets", "Archives", "Code"}),
    (re.compile(r"invoice|receipt|payslip|\bbills?\b|statement|\bticket|booking|voucher", re.I),
     "Bills & Receipts", {"Documents", "Images", "Spreadsheets"}),
]
PARTIAL_EXT = {"crdownload", "part", "partial", "tmp", "download", "opdownload", "temp", "!ut", "aria2"}
SHORTCUT_EXT = {"lnk", "url", "appref-ms"}
SKIP_NAMES = {"desktop.ini", "thumbs.db", ".ds_store"}
FILE_ATTRIBUTE_HIDDEN, FILE_ATTRIBUTE_SYSTEM = 0x2, 0x4


class OrganizerError(Exception):
    """Message is safe to show to the user / model."""


# ---------------------------------------------------------------- well-known folders

_KNOWN = {"desktop": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641", "downloads": "374DE290-123F-4565-9164-39C4925E467B",
          "documents": "FDD39AD0-238F-46AF-ADB4-6C85480369C7", "pictures": "33E28130-4E1E-4676-835A-98395C3BC3BB"}


def known_folder(name: str) -> Path:
    """Real location of Desktop/Downloads/Documents (they can be redirected to OneDrive)."""
    name = name.lower()
    if name not in _KNOWN:
        raise OrganizerError(f"unknown folder '{name}'")
    try:
        g = uuid.UUID(_KNOWN[name])

        class GUID(ctypes.Structure):
            _fields_ = [("a", ctypes.c_ulong), ("b", ctypes.c_ushort), ("c", ctypes.c_ushort), ("d", ctypes.c_ubyte * 8)]

        guid = GUID(g.time_low, g.time_mid, g.time_hi_version, (ctypes.c_ubyte * 8)(*g.bytes[8:]))
        out = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(out)) == 0 and out.value:
            return Path(out.value)
    except Exception:
        pass
    return Path.home() / name.capitalize()


def allowed_roots(cfg: dict[str, Any]) -> dict[str, Path]:
    """name -> folder, for every folder Nyro may organise (config organizer.roots: well-known names or full paths)."""
    names = (cfg.get("organizer") or {}).get("roots") or ["downloads", "desktop"]
    out: dict[str, Path] = {}
    for n in names:
        try:
            p = known_folder(n) if n.lower() in _KNOWN else Path(n).expanduser()
        except OrganizerError:
            continue
        p = p.resolve()
        if p.is_dir() and not _is_protected(p, cfg):
            out[(n.lower() if n.lower() in _KNOWN else p.name.lower())] = p
    return out


def _is_protected(p: Path, cfg: dict[str, Any]) -> bool:
    from host.firewall import is_protected_path
    fw = cfg.get("firewall", {})
    if is_protected_path(p, fw):
        return True
    return p.parent == p or str(p).rstrip("\\/").endswith(":") or p == Path.home()   # never a drive root or the whole profile


def resolve_root(name: str, cfg: dict[str, Any]) -> Path:
    roots = allowed_roots(cfg)
    key = (name or "").strip().lower()
    if key in roots:
        return roots[key]
    raise OrganizerError(f"I can only organise: {', '.join(roots) or 'nothing (no folders enabled)'}")


# ---------------------------------------------------------------- planning

@dataclass
class Move:
    src: str
    dst: str
    category: str


@dataclass
class Plan:
    root: str
    moves: list[Move] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    new_folders: list[str] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for m in self.moves:
            out[m.category] = out.get(m.category, 0) + 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    def summary(self, root_name: str = "") -> str:
        if not self.moves:
            return "Nothing to organise."
        parts = ", ".join(f"{n} {c}" for c, n in self.counts().items())
        return f"Move {len(self.moves)} files in {root_name or self.root} into folders: {parts}"


def categorize(name: str, extra_rules: dict[str, str] | None = None) -> str:
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if extra_rules and ext in extra_rules:
        return extra_rules[ext]
    base = next((c for c, exts in CATEGORIES.items() if ext in exts), "Other")
    spaced = re.sub(r"[_\-.]+", " ", name.rsplit(".", 1)[0] if "." in name else name)
    for pattern, folder, applies_to in NAME_RULES:
        if base in applies_to and pattern.search(spaced):
            return folder
    return base


def _attrs(entry: os.DirEntry) -> int:
    try:
        return getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
    except OSError:
        return 0


def build_plan(root: Path, *, is_desktop: bool = False, min_age: float = MIN_AGE_SECONDS,
               extra_rules: dict[str, str] | None = None, now: float | None = None) -> Plan:
    now = time.time() if now is None else now
    plan = Plan(str(root))
    existing_dirs = {e.name.lower(): e.name for e in os.scandir(root) if e.is_dir(follow_symlinks=False)}
    new_folders: set[str] = set()
    for entry in sorted(os.scandir(root), key=lambda e: e.name.lower()):
        name = entry.name
        if entry.is_dir(follow_symlinks=False) or entry.is_symlink():
            continue                                   # existing folders are never touched
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        reason = None
        if name.lower() in SKIP_NAMES or name.startswith("."):
            reason = "system/hidden"
        elif _attrs(entry) & (FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM):
            reason = "hidden/system file"
        elif ext in PARTIAL_EXT:
            reason = "download in progress"
        elif is_desktop and ext in SHORTCUT_EXT:
            reason = "desktop shortcut"
        else:
            try:
                if now - entry.stat().st_mtime < min_age:
                    reason = "modified a moment ago"
            except OSError:
                reason = "unreadable"
        if reason:
            plan.skipped.append((name, reason))
            continue
        cat = categorize(name, extra_rules)
        folder_name = existing_dirs.get(cat.lower(), cat)     # reuse a matching folder the user already has
        if cat.lower() not in existing_dirs:
            new_folders.add(folder_name)
        plan.moves.append(Move(str(entry.path), str(root / folder_name / name), cat))
        if len(plan.moves) >= MAX_FILES:
            break
    plan.new_folders = sorted(new_folders)
    return plan


# ---------------------------------------------------------------- applying + journal

def unique_path(p: Path) -> Path:
    if not p.exists():
        return p
    stem, suf = p.stem, p.suffix
    for i in range(1, 10_000):
        cand = p.with_name(f"{stem} ({i}){suf}")
        if not cand.exists():
            return cand
    raise OrganizerError(f"too many files named {p.name}")


def _hide(path: Path) -> None:
    try:
        ctypes.windll.kernel32.SetFileAttributesW(str(path), FILE_ATTRIBUTE_HIDDEN)
    except Exception:
        pass


def _read_journal(root: Path) -> dict[str, Any]:
    try:
        return json.loads((root / JOURNAL).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"batches": []}


def _write_journal(root: Path, data: dict[str, Any]) -> None:
    p = root / JOURNAL
    if p.exists():
        try:
            ctypes.windll.kernel32.SetFileAttributesW(str(p), 0x80)   # normal, so we can overwrite a hidden file
        except Exception:
            pass
    p.write_text(json.dumps(data, indent=1), encoding="utf-8")
    _hide(p)


@dataclass
class Result:
    moved: list[tuple[str, str]] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    created: list[str] = field(default_factory=list)
    batch_id: str = ""

    def summary(self) -> str:
        s = f"Moved {len(self.moved)} files"
        if self.failed:
            s += f"; {len(self.failed)} could not be moved (in use or unreadable)"
        return s + "."


def apply_plan(plan: Plan, *, min_age: float = MIN_AGE_SECONDS) -> Result:
    root = Path(plan.root)
    res = Result(batch_id=time.strftime("%Y%m%d-%H%M%S"))
    for folder in plan.new_folders:
        d = root / folder
        if not d.exists():
            d.mkdir()
            res.created.append(folder)
    now = time.time()
    for m in plan.moves:
        src, dst = Path(m.src), Path(m.dst)
        try:
            if not src.is_file() or src.is_symlink() or now - src.stat().st_mtime < min_age:
                continue                                # changed since the preview: leave it
            if src.parent != root or dst.parent.parent != root:
                raise OrganizerError("path outside the folder")
            dst.parent.mkdir(exist_ok=True)
            final = unique_path(dst)
            shutil.move(str(src), str(final))
            res.moved.append((str(src), str(final)))
        except (OSError, OrganizerError) as e:
            res.failed.append((m.src, str(e)[:100]))
    if res.moved:
        j = _read_journal(root)
        j["batches"].append({"id": res.batch_id, "time": time.time(), "created": res.created,
                             "moves": [{"from": a, "to": b} for a, b in res.moved]})
        j["batches"] = j["batches"][-MAX_BATCHES:]
        _write_journal(root, j)
    return res


def undo_last(root: Path) -> Result:
    j = _read_journal(root)
    if not j["batches"]:
        raise OrganizerError("There is nothing to undo here.")
    batch = j["batches"].pop()
    res = Result(batch_id=batch.get("id", ""))
    root = root.resolve()
    for mv in reversed(batch["moves"]):
        src, dst = Path(mv["to"]), Path(mv["from"])
        try:
            if not (src.resolve().is_relative_to(root) and dst.resolve().parent == root):
                raise OrganizerError("path outside the folder")
            if not src.is_file():
                res.failed.append((str(src), "no longer there"))
                continue
            shutil.move(str(src), str(unique_path(dst)))
            res.moved.append((str(src), str(dst)))
        except (OSError, OrganizerError) as e:
            res.failed.append((str(src), str(e)[:100]))
    for folder in batch.get("created", []):          # remove only the folders WE created, and only if now empty
        d = root / folder
        try:
            if d.is_dir() and not any(d.iterdir()):
                d.rmdir()
        except OSError:
            pass
    _write_journal(root, j)
    return res
