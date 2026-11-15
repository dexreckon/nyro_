"""Find installed IDEs / code editors and open files in them. Whitelisted names only; no shell, no arbitrary exes."""
from __future__ import annotations

import glob
import os
import subprocess
from pathlib import Path

# name -> (label, candidate paths; {PF} {PF86} {LA} = ProgramFiles, ProgramFiles(x86), LocalAppData; * is a glob)
IDES: dict[str, tuple[str, list[str]]] = {
    "vscode": ("VS Code", ["{LA}/Programs/Microsoft VS Code/Code.exe", "{PF}/Microsoft VS Code/Code.exe"]),
    "cursor": ("Cursor", ["{LA}/Programs/cursor/Cursor.exe"]),
    "antigravity": ("Antigravity", ["{LA}/Programs/antigravity/Antigravity.exe",
                                    "{LA}/Programs/Antigravity IDE/Antigravity IDE.exe"]),
    "androidstudio": ("Android Studio", ["{PF}/Android/Android Studio/bin/studio64.exe",
                                         "{LA}/Programs/Android Studio/bin/studio64.exe"]),
    "pycharm": ("PyCharm", ["{PF}/JetBrains/PyCharm*/bin/pycharm64.exe", "{LA}/Programs/PyCharm*/bin/pycharm64.exe",
                            "{LA}/JetBrains/Toolbox/apps/PyCharm*/ch-*/*/bin/pycharm64.exe"]),
    "intellij": ("IntelliJ IDEA", ["{PF}/JetBrains/IntelliJ IDEA*/bin/idea64.exe",
                                   "{LA}/Programs/IntelliJ IDEA*/bin/idea64.exe",
                                   "{LA}/JetBrains/Toolbox/apps/IDEA*/ch-*/*/bin/idea64.exe"]),
    "clion": ("CLion", ["{PF}/JetBrains/CLion*/bin/clion64.exe", "{LA}/Programs/CLion*/bin/clion64.exe",
                        "{LA}/JetBrains/Toolbox/apps/CLion*/ch-*/*/bin/clion64.exe"]),
    "webstorm": ("WebStorm", ["{PF}/JetBrains/WebStorm*/bin/webstorm64.exe", "{LA}/Programs/WebStorm*/bin/webstorm64.exe",
                              "{LA}/JetBrains/Toolbox/apps/WebStorm*/ch-*/*/bin/webstorm64.exe"]),
    "sublime": ("Sublime Text", ["{PF}/Sublime Text/sublime_text.exe", "{PF}/Sublime Text 3/sublime_text.exe"]),
    "notepadpp": ("Notepad++", ["{PF}/Notepad++/notepad++.exe", "{PF86}/Notepad++/notepad++.exe"]),
    "visualstudio": ("Visual Studio", []),   # found through vswhere
}
DEFAULT_IDE = "vscode"


def _expand(pattern: str) -> list[str]:
    env = {"PF": os.environ.get("ProgramFiles", ""), "PF86": os.environ.get("ProgramFiles(x86)", ""),
           "LA": os.environ.get("LOCALAPPDATA", "")}
    p = pattern
    for k, v in env.items():
        p = p.replace("{" + k + "}", v)
    return sorted(glob.glob(p), reverse=True)    # newest version folder first


def _vswhere() -> str | None:
    vw = Path(os.environ.get("ProgramFiles(x86)", "")) / "Microsoft Visual Studio/Installer/vswhere.exe"
    if not vw.is_file():
        return None
    try:
        out = subprocess.run([str(vw), "-latest", "-property", "productPath"], capture_output=True, text=True,
                             timeout=10).stdout.strip()
        return out if out and Path(out).is_file() else None
    except Exception:
        return None


def find_ide(name: str) -> str | None:
    """Full path of the IDE's executable, or None if it isn't installed."""
    name = (name or DEFAULT_IDE).lower()
    if name not in IDES:
        raise ValueError(f"unknown IDE '{name}'. Known: {', '.join(sorted(IDES))}")
    if name == "visualstudio":
        return _vswhere()
    for pattern in IDES[name][1]:
        for hit in _expand(pattern):
            if Path(hit).is_file():
                return hit
    return None


def installed() -> dict[str, str]:
    out = {}
    for n in IDES:
        try:
            p = find_ide(n)
        except Exception:
            p = None
        if p:
            out[n] = p
    return out


def open_in_ide(name: str, path: str | os.PathLike | None = None) -> str:
    """Launch the IDE (optionally with a file/folder). Returns the IDE label."""
    name = (name or DEFAULT_IDE).lower()
    exe = find_ide(name)
    if not exe:
        have = ", ".join(IDES[n][0] for n in installed()) or "none found"
        raise LookupError(f"{IDES[name][0]} is not installed (installed here: {have})")
    args = [exe]
    if path:
        args += (["/Edit", str(path)] if name == "visualstudio" else [str(path)])
    subprocess.Popen(args, close_fds=True)
    return IDES[name][0]
