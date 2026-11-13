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
    # keep Devanagari (incl. vowel signs, which \w drops) and Arabic diacritics so Hindi/Urdu words stay whole
    t = re.sub(r"[^\w\s%.\-/:ऀ-ॿً-ٰٟ]", " ", t, flags=re.UNICODE)
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

PERSONAS = ("screencoach", "sysop", "webpilot", "fileclerk", "desktopctl")

OPEN_VERBS = _ns({"open", "launch", "start", "kholo", "khol", "kholdo", "kholna", "kholiye", "chalao", "chalu", "chala",
                  "کھولو", "کھول", "کھولیں", "کھولئے", "کھولیے", "کھولدو", "چلاو", "چلا", "چالو",
                  "खोलो", "खोल", "खोलिए", "खोलें", "चलाओ", "चलाइए", "चालू"})

# canonical name -> spoken/typed aliases in English, Roman Urdu, Urdu script and Devanagari
_ALIAS_SRC: dict[str, list[str]] = {
    "youtube": ["youtube", "you tube", "yt", "یوٹیوب", "یو ٹیوب", "यूट्यूब", "यू ट्यूब", "यूटयूब"],
    "google": ["google", "گوگل", "गूगल"],
    "gmail": ["gmail", "g mail", "جی میل", "جیمیل", "जीमेल"],
    "whatsapp": ["whatsapp", "whats app", "واٹس ایپ", "واٹس اپ", "واٹسایپ", "व्हाट्सएप", "वाट्सएप", "व्हाट्सऐप"],
    "facebook": ["facebook", "فیس بک", "فیسبک", "फेसबुक"],
    "instagram": ["instagram", "insta", "انسٹاگرام", "انسٹا گرام", "इंस्टाग्राम"],
    "github": ["github", "git hub", "گٹ ہب", "गिटहब"],
    "linkedin": ["linkedin", "linked in", "لنکڈ ان", "लिंक्डइन"],
    "netflix": ["netflix", "نیٹ فلکس", "नेटफ्लिक्स"],
    "reddit": ["reddit"],
    "wikipedia": ["wikipedia", "وکیپیڈیا", "विकिपीडिया"],
    "maps": ["maps", "google maps", "نقشہ", "मैप्स"],
    "claude": ["claude", "کلاڈ", "क्लॉड"],
    "chatgpt": ["chatgpt", "chat gpt"],
    "twitter": ["twitter", "ٹویٹر", "ट्विटर"],
    "amazon": ["amazon", "ایمیزون", "अमेज़न", "अमेजन"],
    "drive": ["google drive", "drive", "ڈرائیو"],
    "chrome": ["chrome", "google chrome", "کروم", "گوگل کروم", "क्रोम", "गूगल क्रोम"],
    "edge": ["edge", "microsoft edge", "ایج", "एज"],
    "firefox": ["firefox", "فائرفاکس", "फायरफॉक्स"],
    "brave": ["brave", "بریو"],
    "notepad": ["notepad", "note pad", "نوٹ پیڈ", "नोटपैड"],
    "calculator": ["calculator", "calc", "کیلکولیٹر", "कैलकुलेटर"],
    "paint": ["paint", "پینٹ", "पेंट"],
    "explorer": ["file explorer", "explorer", "فائل ایکسپلورر"],
    "word": ["microsoft word", "ms word"],
    "excel": ["excel", "ایکسل", "एक्सेल"],
    "powerpoint": ["powerpoint", "power point"],
}
BROWSER_NAMES = {"chrome", "edge", "firefox", "brave"}

CLOSE_VERBS = _ns({"close", "band", "bandh", "بند", "बंद"})
TAB_WORDS = _ns({"tab", "tabs", "ٹیب", "टैब", "टेब"})
_TAB_FILLER = _ns({"the", "this", "that", "current", "my", "a", "please", "now", "window", "browser", "website",
                   "page", "on", "in", "of", "kar", "karo", "kardo", "kardijiye", "do", "dena", "de", "ko", "ka", "ki",
                   "ke", "wala", "wali", "wale", "ye", "yeh", "is", "isko", "اس", "کو", "کرو", "کر", "دو", "دیں", "کریں",
                   "کا", "کی", "کے", "والا", "والی", "یہ", "इस", "को", "करो", "कर", "दो", "का", "की", "के", "वाला", "वाली"})


def _build_aliases() -> list[tuple[str, str]]:
    pairs = [(normalize(a), canon) for canon, al in _ALIAS_SRC.items() for a in al]
    return sorted(pairs, key=lambda p: -len(p[0]))  # longest first: "google chrome" beats "google"


_ALIASES = _build_aliases()


def find_targets(t: str) -> list[str]:
    """Canonical site/app/browser names mentioned in normalized text, in spoken order."""
    t = " ".join(w.strip(".") for w in t.split())  # speech-to-text often ends sentences with "."
    padded, taken, hits = f" {t} ", [], []
    for alias, canon in _ALIASES:
        i = padded.find(f" {alias} ")
        if i < 0:
            continue
        span = (i, i + len(alias) + 2)
        if any(span[0] < e and s < span[1] for s, e in taken):
            continue
        taken.append(span)
        hits.append((i, canon))
    return [c for _, c in sorted(hits)]


_SEARCH_RE = re.compile(r"^(?:search|find|look up|play)\s+(.+?)\s+(?:on|in)\s+(youtube|google)"
                        r"(?:\s+(?:in|on)\s+(chrome|edge|firefox|brave))?$")


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

    bare = [w.strip(".") for w in toks]  # speech-to-text often leaves a trailing "." ("tab.")
    if any(w in CLOSE_VERBS for w in bare) and any(w in TAB_WORDS for w in bare):
        toks = bare
        names = find_targets(t)
        sites = [c for c in names if c not in BROWSER_NAMES]
        if sites:
            target = sites[0]
        elif names:            # only a browser was named ("close the chrome tab"): the current tab
            target = None
        else:                  # any other site: use the leftover words as the title text, e.g. "stack overflow"
            rest = [w.strip(".") for w in toks if w not in CLOSE_VERBS | TAB_WORDS | _TAB_FILLER]
            target = " ".join(rest) or None
        return Intent("local", "sysop", "close_tab", {"target": target}, lang)

    sm = _SEARCH_RE.match(t)
    if sm:
        return Intent("local", "sysop", "open_target",
                      {"target": sm.group(2), "browser": sm.group(3), "query": sm.group(1)}, lang)
    if any(w in OPEN_VERBS for w in toks):
        found = find_targets(t)
        if found:
            sites_apps = [c for c in found if c not in BROWSER_NAMES]
            browsers = [c for c in found if c in BROWSER_NAMES]
            if sites_apps:
                return Intent("local", "sysop", "open_target",
                              {"target": sites_apps[0], "browser": browsers[0] if browsers else None}, lang)
            return Intent("local", "sysop", "open_target", {"target": browsers[0], "browser": None}, lang)

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


_DOCS = re.compile(r"(\bpdf\b|\bassignments?\b|\bhomework\b|\bdocx\b|\bword (file|document|doc)\b|\bdownloads?\b|"
                   r"\bsolution (file|document|doc)\b)")
_INPUT = re.compile(r"(\bmouse\b|\bkeyboard\b|\bdouble.?click\b|\bclick\b|\bscroll\b|\bclose\b|\bpress\b|\bselect\b|"
                    r"\bdrag\b|\brefresh\b|\breload\b|\bgo back\b|\btype (this|it|the|in|out|into)\b)")


def route_persona(text: str) -> str:
    t = normalize(text)
    # "open youtube in chrome" phrased in a way the local router missed: SysOp has open_target, WebPilot does not
    if any(w in OPEN_VERBS for w in t.split()) and find_targets(t):
        return "sysop"
    if _DOCS.search(t):          # "make a solution Word file for the assignment PDF in Downloads"
        return "fileclerk"
    if _INPUT.search(t):         # explicit mouse/keyboard work
        return "desktopctl"
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
