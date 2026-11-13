"""Defense-in-depth firewall: path jail, soft delete, 3-tier risk gate."""
from __future__ import annotations

import os
import re
import shutil
from datetime import datetime
from enum import Enum
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Iterable


class FirewallError(Exception):
    """Base class for firewall refusals."""


class PathJailError(FirewallError):
    """Path escapes the allowed roots."""


class RedBlockError(FirewallError):
    """Action is in the kernel-block (red) tier."""


class Risk(str, Enum):
    GREEN = "green"    # auto-run
    YELLOW = "yellow"  # needs mobile approval
    RED = "red"        # always blocked


# ---------------------------------------------------------------- path jail

def _norm_text(s: str) -> str:
    return s.replace("/", "\\").rstrip("\\").lower()


def is_protected_path(path: str | os.PathLike, cfg: dict[str, Any]) -> bool:
    """True if path is under a protected prefix or contains a protected fragment.

    Works on the raw string (both Windows and POSIX style) so it is testable on
    any OS and catches Windows paths even when run elsewhere.
    """
    text = _norm_text(str(path))
    for prefix in cfg.get("protected_path_prefixes", []):
        pre = _norm_text(prefix)
        if text == pre or text.startswith(pre + "\\"):
            return True
    for frag in cfg.get("protected_path_fragments", []):
        if _norm_text(frag).lstrip("\\") in text:
            return True
    return False


class PathJail:
    """Resolve every path and require it to live inside an allowed root."""

    def __init__(self, allowed_roots: Iterable[str | os.PathLike],
                 protected_cfg: dict[str, Any] | None = None):
        self.roots = [Path(r).resolve() for r in allowed_roots]
        self.protected_cfg = protected_cfg or {}
        if not self.roots:
            raise PathJailError("No ALLOWED_ROOTS configured; file tools disabled.")

    def resolve(self, rel_path: str | os.PathLike, root: str | os.PathLike | None = None) -> Path:
        """Resolve rel_path inside a root. Raise PathJailError if it escapes."""
        raw = os.fspath(rel_path)
        if "\x00" in raw:
            raise PathJailError("Null byte in path.")
        if (Path(raw).is_absolute() or PurePosixPath(raw).is_absolute()
                or PureWindowsPath(raw).is_absolute() or PureWindowsPath(raw).drive):
            # absolute paths are allowed only if they already land in a root
            candidate = Path(raw)
        else:
            base = Path(root).resolve() if root else self.roots[0]
            candidate = base / raw
        resolved = candidate.resolve()  # follows symlinks and ..
        if is_protected_path(resolved, self.protected_cfg):
            raise PathJailError(f"Protected path: {resolved}")
        for r in self.roots:
            if resolved.is_relative_to(r):
                return resolved
        raise PathJailError(f"Path escapes allowed roots: {rel_path!s}")

    def root_for(self, resolved: Path) -> Path:
        for r in self.roots:
            if resolved.is_relative_to(r):
                return r
        raise PathJailError("Path not under any allowed root.")


# --------------------------------------------------------------- soft delete

def soft_delete(jail: PathJail, rel_path: str, trash_dirname: str = ".nyro_trash") -> Path:
    """Move a file into <root>/.nyro_trash instead of deleting it. Never os.remove."""
    target = jail.resolve(rel_path)
    root = jail.root_for(target)
    trash = (root / trash_dirname).resolve()
    if target == trash or target.is_relative_to(trash):
        raise PathJailError("Refusing to delete inside the trash folder.")
    if target == root:
        raise PathJailError("Refusing to delete an allowed root.")
    if not target.exists():
        raise FileNotFoundError(str(target))
    if target.is_dir():
        raise PathJailError("soft_delete only handles files.")
    trash.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    dest = trash / f"{stamp}__{target.name}"
    shutil.move(str(target), str(dest))
    return dest


# ---------------------------------------------------------------- risk gate

# action -> default tier. Unknown actions are YELLOW (fail safe, ask the user).
_ACTION_TIERS: dict[str, Risk] = {
    # overlay / vision / system tuning
    "render_screen_annotations": Risk.GREEN,
    "clear_annotations": Risk.GREEN,
    "capture_foreground_window": Risk.GREEN,
    "capture_tab_screenshot": Risk.GREEN,
    "set_brightness": Risk.GREEN,
    "set_volume": Risk.GREEN,
    "toggle_mute": Risk.GREEN,
    "manage_window": Risk.GREEN,
    "get_system_telemetry": Risk.GREEN,
    "lock_workstation": Risk.GREEN,
    "list_allowed_files": Risk.GREEN,
    "read_document": Risk.GREEN,
    "open_url": Risk.GREEN,
    "close_tab": Risk.GREEN,  # Ctrl+W on a real browser window only (enforced in sysop_impl.close_tab)
    "open_target": Risk.GREEN,  # whitelisted sites/apps only (enforced in sysop_impl.open_target)
    "extract_dom_text": Risk.GREEN,
    "type_text": Risk.GREEN,
    # modifying
    "write_markdown": Risk.YELLOW,
    "write_docx": Risk.YELLOW,
    "soft_delete_file": Risk.YELLOW,
    "request_control": Risk.YELLOW,  # mouse/keyboard grant: always asks the user first
    "open_path": Risk.GREEN,  # jailed + passive file types only (enforced in fileclerk_impl.open_path)
}

_SUBMIT_WORDS = re.compile(
    r"\b(submit|pay|payment|checkout|buy|purchase|send|confirm|delete|transfer|place order)\b",
    re.I,
)

_RED_COMMAND = re.compile(
    r"(\bformat\s+[a-z]:|\bdel\s+/[sqf]|\brmdir\s+/s|\brm\s+-rf?\b|\breg(\.exe)?\s+(add|delete|import)"
    r"|\bpowershell\b|\bcmd(\.exe)?\b|\bbash\b|\bsh\s+-c\b|\bshutdown\b|\bbcdedit\b|\btakeown\b|\bcipher\s+/w)",
    re.I,
)
_RED_REGISTRY = re.compile(r"\b(HKEY_[A-Z_]+|HKLM|HKCU)\b[\\:]?", re.I)

_FILE_ACTIONS = {"read_document", "write_markdown", "soft_delete_file"}


def classify_action(action: str, params: dict[str, Any] | None = None,
                    cfg: dict[str, Any] | None = None) -> Risk:
    """Return the risk tier for an action. RED means never run it."""
    params = params or {}
    cfg = cfg or {}

    # raw shell / unknown "run" style actions
    if action in {"bash", "shell", "exec", "run_command", "powershell", "cmd"}:
        return Risk.RED

    blob = " ".join(str(v) for v in params.values())
    if _RED_COMMAND.search(blob) and action not in {"type_text", "request_control"}:
        return Risk.RED
    if _RED_REGISTRY.search(blob):
        return Risk.RED

    # paths touching protected locations
    for key in ("path", "rel_path", "file", "target"):
        val = params.get(key)
        if isinstance(val, str) and is_protected_path(val, cfg):
            return Risk.RED

    # killing critical processes
    if action in {"kill_process", "terminate_process"}:
        name = str(params.get("name", "")).lower()
        if name in {p.lower() for p in cfg.get("blocked_processes", [])}:
            return Risk.RED
        return Risk.YELLOW

    # browser submit/pay/send gates
    if action in {"click_element", "type_text"}:
        sel = " ".join(str(params.get(k, "")) for k in ("selector", "label", "text"))
        if action == "click_element" and _SUBMIT_WORDS.search(sel):
            return Risk.YELLOW
        return Risk.GREEN

    return _ACTION_TIERS.get(action, Risk.YELLOW)


def enforce(action: str, params: dict[str, Any] | None = None,
            cfg: dict[str, Any] | None = None) -> Risk:
    """Classify and raise RedBlockError for red; return tier otherwise."""
    tier = classify_action(action, params, cfg)
    if tier is Risk.RED:
        raise RedBlockError(f"Blocked by firewall: {action}")
    return tier
