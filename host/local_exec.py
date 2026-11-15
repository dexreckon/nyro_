"""Execute local (zero-LLM) intents and produce a short reply in the user's language."""
from __future__ import annotations

from typing import Any, Callable

from host import sysop_impl as S
from host.firewall import Risk, classify_action
from host.intent import Intent
from host.overlay import client as overlay_client

REPLIES = {
    "en": {"set_volume": "Volume {level} percent.", "change_volume": "Volume adjusted.", "toggle_mute": "Done.",
           "set_brightness": "Brightness {level} percent.", "change_brightness": "Brightness adjusted.",
           "lock_workstation": "Locking.", "manage_window": "Done.", "clear_annotations": "Cleared.",
           "open_target": "Opening {name}.", "close_tab": "Closed {name}.", "stop_speaking": "Stopped.",
           "failed": "Sorry, that did not work: {err}",
           "limit": "The daily AI limit is reached. Local controls still work."},
    "ur": {"set_volume": "آواز {level} فیصد۔", "change_volume": "آواز تبدیل ہو گئی۔", "toggle_mute": "ہو گیا۔",
           "set_brightness": "روشنی {level} فیصد۔", "change_brightness": "روشنی تبدیل ہو گئی۔",
           "lock_workstation": "لاک کر رہا ہوں۔", "manage_window": "ہو گیا۔", "clear_annotations": "صاف ہو گیا۔",
           "open_target": "{name} کھول رہا ہوں۔", "close_tab": "{name} بند کر دیا۔", "stop_speaking": "رک گیا۔",
           "failed": "معذرت، یہ نہیں ہو سکا: {err}",
           "limit": "آج کی AI حد پوری ہو گئی ہے۔ مقامی کنٹرول اب بھی کام کرتے ہیں۔"},
}


def msg(lang: str, key: str, **kw) -> str:
    return REPLIES.get(lang, REPLIES["en"])[key].format(**kw)


def _telemetry_text(lang: str) -> str:
    t = S.get_system_telemetry()
    parts = [f"CPU {t['cpu_percent']:.0f}%", f"RAM {t['ram_percent']:.0f}%"]
    if "battery_percent" in t:
        parts.append(f"{'بیٹری' if lang == 'ur' else 'Battery'} {t['battery_percent']}%" +
                     (" (charging)" if t.get("plugged_in") and lang != "ur" else ""))
    return ", ".join(parts)


FILE_MANAGER_ACTIONS = {"organize_folder", "organize_desktop", "arrange_icons", "restore_icons", "undo_organize",
                        "find_files", "open_folder"}


def _file_manager(intent: Intent, cfg: dict[str, Any]) -> str:
    """Instant file-manager commands. Moving files asks for approval (the approval prompt appears in the app/phone)."""
    from host import filemanager as FM
    from host.approval import request_approval

    def approver(action: str, summary: str, params: dict) -> bool:
        return request_approval(action, summary, params, timeout=cfg.get("approval", {}).get("timeout_seconds", 120))

    a, p, lang = intent.action, intent.params, intent.lang
    try:
        if a == "organize_folder":
            return FM.organize(cfg, p["folder"], approver)
        if a == "organize_desktop":
            return FM.organize_desktop(cfg, approver)
        if a == "arrange_icons":
            return FM.arrange_icons(cfg, p.get("mode", "category"))
        if a == "restore_icons":
            return FM.restore_icons(cfg)
        if a == "undo_organize":
            return FM.undo(cfg, p.get("folder"))
        if a == "open_folder":
            return FM.open_known_folder(cfg, p["folder"])
        if a == "find_files":
            rows = FM.search(cfg, p["query"], 5)
            if not rows:
                return f"I couldn't find anything for {p['query']}."
            best = rows[0]
            try:
                FM.reveal(cfg, best["path"])
            except Exception:
                pass
            from pathlib import Path
            where = Path(best["path"]).parent.name
            more = ", ".join(Path(r["path"]).name for r in rows[1:3])
            return f"Found {Path(best['path']).name} in {where}." + (f" Also: {more}." if more else "")
    except Exception as e:
        return msg(lang, "failed", err=str(e)[:160])
    return msg(lang, "failed", err="unknown command")


def _stop_everything(cfg: dict[str, Any]) -> str:
    """'stop': silence Nyro's voice and take back any mouse/keyboard control it was granted."""
    from pathlib import Path

    from host.desktopctl_impl import GRANT_FILE
    from host.voice import tts
    tts.stop()
    try:
        (Path(cfg.get("data_dir", "nyro_data")).resolve() / GRANT_FILE).unlink(missing_ok=True)
    except OSError:
        pass
    return "ok"


def _clear_overlay(_p) -> str:
    if not overlay_client.clear():
        raise RuntimeError("overlay not running")
    return "ok"


_ACTIONS: dict[str, Callable[..., str]] = {
    "set_volume": lambda p: S.set_volume(p["level"]),
    "change_volume": lambda p: S.change_volume(p["delta"]),
    "toggle_mute": lambda p: S.toggle_mute(),
    "set_brightness": lambda p: S.set_brightness(p["level"]),
    "change_brightness": lambda p: S.change_brightness(p["delta"]),
    "lock_workstation": lambda p: S.lock_workstation(),
    "manage_window": lambda p: S.manage_window(p["app_name"], p["action"]),
    "clear_annotations": _clear_overlay,
    "open_target": lambda p: S.open_target(p["target"], p.get("browser"), p.get("query")),
    "close_tab": lambda p: S.close_tab(p.get("target")),
}


def execute(intent: Intent, cfg: dict[str, Any]) -> str:
    """Run a local intent. Everything here is GREEN-tier; anything else is refused."""
    action, lang = intent.action or "", intent.lang
    if action in FILE_MANAGER_ACTIONS:      # these guard themselves (approval before any file is moved)
        return _file_manager(intent, cfg)
    base = {"change_volume": "set_volume", "change_brightness": "set_brightness"}.get(action, action)
    tier = classify_action(base, intent.params, cfg["firewall"])
    if tier is not Risk.GREEN:
        return msg(lang, "failed", err="not allowed")
    try:
        if action == "get_system_telemetry":
            return _telemetry_text(lang)
        if action == "stop_speaking":
            _stop_everything(cfg)
            return msg(lang, action)
        fn = _ACTIONS[action]
        out = fn(intent.params)
        kw = {k: v for k, v in intent.params.items() if k == "level"}
        if action in ("open_target", "close_tab"):
            kw["name"] = out
        return msg(lang, action, **kw)
    except Exception as e:
        return msg(lang, "failed", err=str(e)[:120])
