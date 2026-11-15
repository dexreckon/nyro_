"""Arrange the icons on the Windows desktop (positions only: no file is touched).

Works by talking to Explorer's desktop list view (SysListView32). Every arrange first saves the previous
positions, so restore_layout() can put everything back exactly where it was."""
from __future__ import annotations

import ctypes
import json
import os
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any

# ---- list view messages
LVM_GETITEMCOUNT = 0x1004
LVM_SETITEMPOSITION = 0x100F
LVM_GETITEMPOSITION = 0x1010
LVM_GETITEMSPACING = 0x1033
LVM_GETITEMTEXTW = 0x1073
LVIF_TEXT = 0x1
LVS_AUTOARRANGE = 0x100
GWL_STYLE = -16
PROCESS_ACCESS = 0x0008 | 0x0010 | 0x0020 | 0x0400   # VM_OPERATION | VM_READ | VM_WRITE | QUERY_INFORMATION
MEM_COMMIT_RESERVE, MEM_RELEASE, PAGE_READWRITE = 0x3000, 0x8000, 0x04

GROUP_ORDER = ["System", "Browsers", "Communication", "Development", "Office & Docs", "Media & Creative", "Games",
               "Utilities", "Web links", "Folders", "Files", "Other apps"]
KEYWORDS: dict[str, tuple[str, ...]] = {
    "System": ("this pc", "recycle bin", "network", "control panel", "my computer"),
    "Browsers": ("chrome", "msedge", "edge", "firefox", "brave", "opera", "vivaldi", "browser"),
    "Communication": ("whatsapp", "telegram", "discord", "slack", "zoom", "teams", "skype", "signal", "outlook", "mail"),
    "Development": ("code.exe", "vs code", "visual studio", "studio64", "android studio", "pycharm", "idea", "clion",
                    "webstorm", "devenv", "git", "python", "node", "docker", "antigravity", "cursor", "postman",
                    "powershell", "terminal", "cmd.exe", "sublime", "notepad++", "xampp", "mysql", "mongodb", "arduino",
                    "unity", "unreal", "figma", "github", "dev-c++", "devcpp", "dev c++", "emu8086", "turbo c",
                    "codeblocks", "eclipse", "netbeans", "jupyter", "anaconda", "flutter", "xcode", "matlab", "proteus",
                    "packet tracer", "wireshark", "virtualbox", "vmware"),
    "Office & Docs": ("winword", "excel", "powerpnt", "onenote", "acrobat", "word", "powerpoint", "libreoffice", "wps",
                      "notepad", "notion", "obsidian", "foxit", "sumatra", "reader", "claude", "chatgpt", "gemini",
                      "copilot", "perplexity", "grammarly", "zotero"),
    "Media & Creative": ("spotify", "vlc", "obs", "photoshop", "premiere", "davinci", "audacity", "itunes", "capcut",
                         "canva", "illustrator", "lightroom", "blender", "kdenlive", "media player", "camera", "zona",
                         "netflix", "youtube", "scaniverse", "cinema"),
    "Games": ("steam", "epic", "riot", "valorant", "minecraft", "battle.net", "origin", "xbox", "game", "league",
              "fortnite", "roblox", "ubisoft", "gog", "pubg", "call of duty", "halo", "gameloop", "ea app", "ea",
              "need for speed", "gta", "fifa", "cyberpunk", "witcher"),
    "Utilities": ("settings", "task manager", "7-zip", "winrar", "ccleaner", "anydesk", "teamviewer", "vpn", "defender",
                  "antivirus", "nvidia", "amd", "razer", "logitech", "bluetooth", "driver", "docker desktop", "clipboard",
                  "remote desktop", "download manager", "partition", "disk", "backup", "recovery", "uninstall",
                  "cleaner", "optimizer", "idm", "winscp", "putty", "everything", "powertoys"),
}

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)   # same coordinate space as Explorer's list view
except Exception:
    pass

user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.VirtualAllocEx.restype = ctypes.c_void_p
kernel32.VirtualAllocEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD, wintypes.DWORD]
kernel32.VirtualFreeEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD]
kernel32.WriteProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                        ctypes.POINTER(ctypes.c_size_t)]
kernel32.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                       ctypes.POINTER(ctypes.c_size_t)]
user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.SendMessageW.restype = ctypes.c_ssize_t


class DesktopIconError(Exception):
    """Message is safe to show to the user."""


class LVITEMW(ctypes.Structure):
    _fields_ = [("mask", ctypes.c_uint), ("iItem", ctypes.c_int), ("iSubItem", ctypes.c_int), ("state", ctypes.c_uint),
                ("stateMask", ctypes.c_uint), ("pszText", ctypes.c_void_p), ("cchTextMax", ctypes.c_int),
                ("iImage", ctypes.c_int), ("lParam", ctypes.c_ssize_t), ("iIndent", ctypes.c_int),
                ("iGroupId", ctypes.c_int), ("cColumns", ctypes.c_uint), ("puColumns", ctypes.c_void_p),
                ("piColFmt", ctypes.c_void_p), ("iGroup", ctypes.c_int)]


# ------------------------------------------------------------------ finding + talking to the list view

def find_listview() -> int:
    progman = user32.FindWindowW("Progman", None)
    defview = user32.FindWindowExW(progman, None, "SHELLDLL_DefView", None) if progman else 0
    if not defview:      # when a slideshow wallpaper is active the icons live under a WorkerW window
        found: list[int] = []
        cb_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def cb(hwnd, _):
            dv = user32.FindWindowExW(hwnd, None, "SHELLDLL_DefView", None)
            if dv:
                found.append(dv)
            return True
        user32.EnumWindows(cb_type(cb), 0)
        defview = found[0] if found else 0
    lv = user32.FindWindowExW(defview, None, "SysListView32", None) if defview else 0
    if not lv:
        raise DesktopIconError("I couldn't find the desktop icons (is the desktop showing?)")
    return lv


class _Remote:
    """A scratch buffer inside Explorer's memory, used to read item text / positions."""

    def __init__(self, lv: int):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(lv, ctypes.byref(pid))
        self.h = kernel32.OpenProcess(PROCESS_ACCESS, False, pid.value)
        if not self.h:
            raise DesktopIconError("Windows would not let me read the desktop icons")
        self.buf = kernel32.VirtualAllocEx(self.h, None, 4096, MEM_COMMIT_RESERVE, PAGE_READWRITE)
        if not self.buf:
            kernel32.CloseHandle(self.h)
            raise DesktopIconError("could not talk to Explorer")

    def read(self, offset: int, size: int) -> bytes:
        data = (ctypes.c_char * size)()
        n = ctypes.c_size_t()
        kernel32.ReadProcessMemory(self.h, self.buf + offset, data, size, ctypes.byref(n))
        return bytes(data)[:n.value]

    def write(self, offset: int, data: bytes) -> None:
        n = ctypes.c_size_t()
        kernel32.WriteProcessMemory(self.h, self.buf + offset, data, len(data), ctypes.byref(n))

    def close(self) -> None:
        kernel32.VirtualFreeEx(self.h, self.buf, 0, MEM_RELEASE)
        kernel32.CloseHandle(self.h)


def read_icons(lv: int | None = None) -> list[dict[str, Any]]:
    """[{index, name, x, y}] for every icon on the desktop."""
    lv = lv or find_listview()
    count = user32.SendMessageW(lv, LVM_GETITEMCOUNT, 0, 0)
    r = _Remote(lv)
    out = []
    try:
        for i in range(count):
            item = LVITEMW(mask=LVIF_TEXT, iItem=i, iSubItem=0, pszText=r.buf + 256, cchTextMax=260)
            r.write(0, bytes(item))
            user32.SendMessageW(lv, LVM_GETITEMTEXTW, i, r.buf)
            name = r.read(256, 520).decode("utf-16-le", "ignore").split("\x00", 1)[0]
            user32.SendMessageW(lv, LVM_GETITEMPOSITION, i, r.buf + 1024)
            x, y = ctypes.c_int.from_buffer_copy(r.read(1024, 4)).value, ctypes.c_int.from_buffer_copy(r.read(1028, 4)).value
            out.append({"index": i, "name": name, "x": x, "y": y})
    finally:
        r.close()
    return out


def spacing(lv: int) -> tuple[int, int]:
    v = user32.SendMessageW(lv, LVM_GETITEMSPACING, 0, 0)
    return max(40, v & 0xFFFF), max(40, (v >> 16) & 0xFFFF)


def client_size(lv: int) -> tuple[int, int]:
    r = wintypes.RECT()
    user32.GetClientRect(lv, ctypes.byref(r))
    return r.right - r.left, r.bottom - r.top


def _turn_off_auto_arrange(lv: int) -> bool:
    style = user32.GetWindowLongW(lv, GWL_STYLE)
    if style & LVS_AUTOARRANGE:
        user32.SetWindowLongW(lv, GWL_STYLE, style & ~LVS_AUTOARRANGE)
        return True
    return False


def set_position(lv: int, index: int, x: int, y: int) -> None:
    user32.SendMessageW(lv, LVM_SETITEMPOSITION, index, ((y & 0xFFFF) << 16) | (x & 0xFFFF))


# ------------------------------------------------------------------ classifying icons

def _desktop_dirs() -> list[Path]:
    from host.organizer import known_folder
    out = [known_folder("desktop")]
    pub = os.environ.get("PUBLIC")
    if pub and (Path(pub) / "Desktop").is_dir():
        out.append(Path(pub) / "Desktop")
    return out


def _shortcut_target(path: Path) -> str:
    try:
        import win32com.client  # type: ignore
        sc = win32com.client.Dispatch("WScript.Shell").CreateShortCut(str(path))
        return f"{sc.TargetPath} {sc.Arguments}".lower()
    except Exception:
        return ""


_CHECK_ORDER = ("System", "Communication", "Development", "Office & Docs", "Media & Creative", "Games", "Utilities",
                "Browsers")        # browsers last: many app shortcuts (e.g. web apps) run through chrome.exe


def _has_keyword(hay: str, group: str) -> bool:
    import re
    for k in KEYWORDS[group]:
        if len(k) <= 4 or k.isalpha() and len(k) <= 5:        # short words must match as whole words ("cod", "epic")
            if re.search(rf"(?<![a-z0-9]){re.escape(k)}(?![a-z0-9])", hay):
                return True
        elif k in hay:
            return True
    return False


def classify(name: str, kind: str, target: str = "") -> str:
    """Group for an icon from its name, what it is (folder/shortcut/file/url) and its shortcut target.
    The NAME is judged first; the shortcut target is only a fallback (a web-app shortcut targets chrome.exe)."""
    if kind == "folder":
        return "Folders"
    for hay in (name.lower(), f"{name} {target}".lower()):
        for group in _CHECK_ORDER:
            if _has_keyword(hay, group):
                return group
    if kind == "url":
        return "Web links"
    if kind == "file":
        return "Files"
    return "Other apps"


def describe_desktop() -> dict[str, tuple[str, str]]:
    """display name (lower-case, with and without extension) -> (kind, shortcut target)."""
    out: dict[str, tuple[str, str]] = {}
    for d in _desktop_dirs():
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        for e in entries:
            if e.name.startswith(".") or e.name.lower() == "desktop.ini":
                continue
            p = Path(e.path)
            ext = p.suffix.lower()
            if e.is_dir():
                kind, target = "folder", ""
            elif ext == ".lnk":
                kind, target = "shortcut", _shortcut_target(p)
            elif ext in (".url", ".appref-ms"):
                kind, target = "url", ""
                try:
                    target = p.read_text(errors="ignore").lower()[:400]
                except OSError:
                    pass
            else:
                kind, target = "file", ""
            out[e.name.lower()] = (kind, target)
            out[p.stem.lower()] = (kind, target)
    return out


# ------------------------------------------------------------------ layout (pure, testable)

def plan_layout(items: list[dict[str, Any]], cell: tuple[int, int], area: tuple[int, int], mode: str = "category",
                margin: tuple[int, int] = (10, 10)) -> dict[int, tuple[int, int]]:
    """items: [{index, name, group}]. Returns {index: (x, y)}. 'category': one block of columns per group (groups start
    at the top of a new column); 'name': one alphabetical grid. Falls back to one compact grid if groups won't fit."""
    cx, cy = cell
    w, h = area
    rows = max(1, (h - margin[1]) // cy)
    cols_available = max(1, (w - margin[0]) // cx)

    def key(it: dict[str, Any]):
        return (GROUP_ORDER.index(it["group"]) if it["group"] in GROUP_ORDER else len(GROUP_ORDER), it["name"].lower())

    ordered = sorted(items, key=key) if mode == "category" else sorted(items, key=lambda i: i["name"].lower())

    def place(break_groups: bool) -> tuple[dict[int, tuple[int, int]], int]:
        pos: dict[int, tuple[int, int]] = {}
        col = row = 0
        last_group = None
        for it in ordered:
            if break_groups and mode == "category" and last_group is not None and it["group"] != last_group and row != 0:
                col, row = col + 1, 0
            pos[it["index"]] = (margin[0] + col * cx, margin[1] + row * cy)
            last_group = it["group"]
            row += 1
            if row >= rows:
                col, row = col + 1, 0
        return pos, col + (1 if row else 0)

    pos, cols_needed = place(True)
    if cols_needed > cols_available:
        pos, _ = place(False)          # too many groups for the screen: pack tightly instead
    return pos


# ------------------------------------------------------------------ public API

def _backup_paths(data_dir: Path) -> tuple[Path, Path]:
    return data_dir / "desktop_layout_backup.json", data_dir / "desktop_layout_original.json"


def arrange(data_dir: Path, mode: str = "category") -> dict[str, Any]:
    """Arrange the desktop icons. Saves the previous layout first. Returns a summary."""
    if mode not in ("category", "name"):
        raise DesktopIconError("mode must be 'category' or 'name'")
    lv = find_listview()
    icons = read_icons(lv)
    if not icons:
        raise DesktopIconError("There are no icons on the desktop.")
    data_dir.mkdir(parents=True, exist_ok=True)
    backup, original = _backup_paths(data_dir)
    snapshot = json.dumps({"time": time.time(), "icons": icons})
    backup.write_text(snapshot)
    if not original.exists():
        original.write_text(snapshot)

    info = describe_desktop()
    items = []
    for ic in icons:
        kind, target = info.get(ic["name"].lower(), ("virtual", ""))
        group = classify(ic["name"], kind, target)
        items.append({"index": ic["index"], "name": ic["name"], "group": group})
    turned_off = _turn_off_auto_arrange(lv)
    pos = plan_layout(items, spacing(lv), client_size(lv), mode)
    for idx, (x, y) in pos.items():
        set_position(lv, idx, x, y)
    user32.InvalidateRect(lv, None, True)
    counts: dict[str, int] = {}
    for it in items:
        counts[it["group"]] = counts.get(it["group"], 0) + 1
    return {"icons": len(icons), "groups": counts, "auto_arrange_was_on": turned_off, "mode": mode}


def restore(data_dir: Path, original: bool = False) -> int:
    """Put icons back where they were before the last arrange (or before the very first one). Returns icons restored."""
    backup, orig = _backup_paths(data_dir)
    src = orig if original else backup
    if not src.exists():
        raise DesktopIconError("I have no saved desktop layout to restore.")
    saved = json.loads(src.read_text())["icons"]
    lv = find_listview()
    _turn_off_auto_arrange(lv)
    by_name = {ic["name"]: ic for ic in read_icons(lv)}
    n = 0
    for s in saved:
        cur = by_name.get(s["name"])
        if cur:
            set_position(lv, cur["index"], s["x"], s["y"])
            n += 1
    user32.InvalidateRect(lv, None, True)
    return n
