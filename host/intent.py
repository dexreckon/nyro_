"""Local intent router: handles simple commands with ZERO LLM calls (English / Urdu / Roman Urdu)
and picks a persona for everything else."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from host.lang import detect_language

_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_CHAR_FIX = str.maketrans({"ي": "ی", "ك": "ک", "ۃ": "ہ", "ة": "ہ", "أ": "ا", "إ": "ا", "آ": "ا"})
STEP = 10
MAX_LOCAL_WORDS = 9


def normalize(text: str) -> str:
    t = text.translate(_DIGITS).translate(_CHAR_FIX).lower()
    t = re.sub(r"[^\w\s%.\-/:]", " ", t, flags=re.UNICODE)
    return re.sub(r"\s+", " ", t).strip()


def _ns(words: set[str]) -> set[str]:
    return {w.translate(_CHAR_FIX).translate(_DIGITS) for w in words}


def _has(words: set[str], toks: list[str], text: str) -> bool:
    return any(w in toks for w in words)


VOLUME = _ns({"volume", "sound", "awaz", "awaaz", "avaz", "آواز", "والیوم", "ساؤنڈ"})
BRIGHT = _ns({"brightness", "roshni", "روشنی", "برائٹنس", "برائٹ"})
MUTE = _ns({"mute", "unmute", "khamosh", "khamoosh", "خاموش", "میوٹ" })
LOCK = _ns({"lock", "لاک"})
UP = _ns({"up", "increase", "raise", "louder", "more", "higher", "brighter", "barhao", "barha", "barhado", "zyada", "ziada",
      "بڑھاو", "بڑھا", "زیادہ", "اونچی", "اونچا", "تیز"})
DOWN = _ns({"down", "decrease", "lower", "reduce", "quieter", "less", "dim", "dimmer", "kam", "ghatao", "ghata", "kamkardo",
        "کم", "گھٹاو", "گھٹا", "نیچے", "دھیمی", "دھیما"})
QUESTION = _ns({"how", "why", "what", "which", "explain", "kaise", "kyun", "kyon", "kya", "samjhao", "کیسے", "کیوں", "کیا",
            "سمجھاو", "can", "could", "should"})
STATS = _ns({"battery", "cpu", "ram", "memory", "stats", "telemetry", "بیٹری"})
WINDOW_VERBS = {"maximize": "maximize", "maximise": "maximize", "minimize": "minimize", "minimise": "minimize",
                "restore": "restore", "focus": "focus"}
CLEAR_WORDS = {"clear", "hide", "remove", "dismiss"}
CLEAR_OBJECTS = {"overlay", "annotations", "drawings", "arrows", "cursor", "marks", "highlights", "screen"}

PERSONAS = ("screencoach", "sysop", "webpilot", "fileclerk")


@dataclass
class Intent:
    kind: str                     # "local" | "llm"
    persona: str | None
    action: str | None = None     # local action name
    params: dict[str, Any] = field(default_factory=dict)
    lang: str = "en"


def _number(text: str) -> int | None:
    m = re.search(r"(?<![\w.])(\d{1,3})\s*%?(?![\w.])", text)
    return int(m.group(1)) if m else None


def parse_local(text: str) -> Intent | None:
    t = normalize(text)
    toks = t.split()
    if not toks or len(toks) > MAX_LOCAL_WORDS or _has(QUESTION, toks, t):
        return None
    lang = detect_language(text)
    num = _number(t)
    up, down = _has(UP, toks, t), _has(DOWN, toks, t)

    def rel(kind_set_action: str, kind_change_action: str) -> Intent | None:
        if num is not None and not (up or down):
            return Intent("local", "sysop", kind_set_action, {"level": min(num, 100)}, lang)
        if num is not None and (up or down):
            return Intent("local", "sysop", kind_change_action, {"delta": num if up else -num}, lang)
        if up or down:
            return Intent("local", "sysop", kind_change_action, {"delta": STEP if up else -STEP}, lang)
        return None

    if _has(BRIGHT, toks, t):
        return rel("set_brightness", "change_brightness")
    if _has(VOLUME, toks, t):
        if _has(MUTE, toks, t):
            return Intent("local", "sysop", "toggle_mute", {}, lang)
        return rel("set_volume", "change_volume")
    if _has(MUTE, toks, t):
        return Intent("local", "sysop", "toggle_mute", {}, lang)
    if _has(LOCK, toks, t):
        return Intent("local", "sysop", "lock_workstation", {}, lang)

    for verb, action in WINDOW_VERBS.items():
        if verb in toks:
            app = " ".join(w for w in toks if w != verb and w not in {"the", "window", "my", "app"})
            if app:
                return Intent("local", "sysop", "manage_window", {"app_name": app, "action": action}, lang)
    m = re.match(r"^(?:snap|move|put|dock)\s+(?:the\s+)?(.+?)\s+(?:to\s+(?:the\s+)?)?(left|right)$", t)
    if m:
        return Intent("local", "sysop", "manage_window",
                      {"app_name": m.group(1).replace(" window", "").strip(), "action": f"snap_{m.group(2)}"}, lang)

    if any(w in toks for w in CLEAR_WORDS) and any(w in toks for w in CLEAR_OBJECTS):
        return Intent("local", "screencoach", "clear_annotations", {}, lang)
    if _has(STATS, toks, t) and not _has((VOLUME | BRIGHT), toks, t):
        return Intent("local", "sysop", "get_system_telemetry", {}, lang)
    return None


_WEB = re.compile(r"(https?://|www\.|\b[\w-]+\.(com|org|net|edu|io|pk)\b|\bwebsite\b|\bbrowser\b|\bchrome tab\b|"
                  r"\bgo to\b|\bgoogle\b|\bportal\b|\bfill (in |out )?(the |this )?form\b|\bweb page\b|ویب|ویب سائٹ)")
_FILE = re.compile(r"(\bnotes?\b|\bfiles?\b|\bfolder\b|\bdocuments?\b|\.md\b|\bsave (this )?(as|to)\b|\bwrite (me )?a note\b|"
                   r"فائل|نوٹ|فولڈر)")
_SCREEN = re.compile(r"(\bslide\b|\bscreen\b|\bhere\b|\bguide\b|\bpoint\b|\bshow me\b|\bon my\b|\bhelp me\b|"
                     r"\berror\b|\bsyntax\b|اسکرین|یہاں|یہ\b)")


def route_persona(text: str) -> str:
    t = normalize(text)
    if _WEB.search(t):
        return "webpilot"
    if _FILE.search(t) and not _SCREEN.search(t):
        return "fileclerk"
    return "screencoach"


def route(text: str) -> Intent:
    local = parse_local(text)
    if local:
        return local
    return Intent("llm", route_persona(text), lang=detect_language(text))
