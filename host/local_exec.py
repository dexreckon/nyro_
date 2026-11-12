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
           "failed": "Sorry, that did not work: {err}",
           "limit": "The daily AI limit is reached. Local controls still work."},
    "ur": {"set_volume": "آواز {level} فیصد۔", "change_volume": "آواز تبدیل ہو گئی۔", "toggle_mute": "ہو گیا۔",
           "set_brightness": "روشنی {level} فیصد۔", "change_brightness": "روشنی تبدیل ہو گئی۔",
           "lock_workstation": "لاک کر رہا ہوں۔", "manage_window": "ہو گیا۔", "clear_annotations": "صاف ہو گیا۔",
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
}


def execute(intent: Intent, cfg: dict[str, Any]) -> str:
    """Run a local intent. Everything here is GREEN-tier; anything else is refused."""
    action, lang = intent.action or "", intent.lang
    base = {"change_volume": "set_volume", "change_brightness": "set_brightness"}.get(action, action)
    tier = classify_action(base, intent.params, cfg["firewall"])
    if tier is not Risk.GREEN:
        return msg(lang, "failed", err="not allowed")
    try:
        if action == "get_system_telemetry":
            return _telemetry_text(lang)
        fn = _ACTIONS[action]
        fn(intent.params)
        return msg(lang, action, **{k: v for k, v in intent.params.items() if k == "level"})
    except Exception as e:
        return msg(lang, "failed", err=str(e)[:120])
