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

PERSONAS = ("screencoach", "sysop", "webpilot", "fileclerk", "desktopctl", "coder", "filemanager")

# ---- file manager words (English / Roman Urdu / Urdu / Devanagari)
ORG_VERBS = _ns({"organize", "organise", "clean", "cleanup", "tidy", "sort", "declutter", "arrange", "saaf", "safai",
                 "tarteeb", "صاف", "ترتیب", "साफ", "व्यवस्थित"})
DOWNLOAD_WORDS = _ns({"downloads", "download", "ڈاؤنلوڈ", "ڈاؤنلوڈز", "ڈاؤن", "डाउनलोड", "डाउनलोड्स"})
DESKTOP_WORDS = _ns({"desktop", "ڈیسکٹاپ", "ڈیسک", "डेस्कटॉप"})
ICON_WORDS = _ns({"icons", "icon", "shortcuts", "shortcut", "layout", "آئیکن", "آئیکنز", "شارٹ", "आइकन", "शॉर्टकट"})
UNDO_WORDS = _ns({"undo", "revert", "wapas", "واپس", "वापस"})
RESTORE_WORDS = _ns({"restore", "reset", "undo", "revert"})
FIND_VERBS = _ns({"find", "locate", "search", "dhundo", "dhoondo", "ڈھونڈو", "تلاش", "खोजो", "ढूंढो"})
FILEISH = _ns({"file", "files", "pdf", "doc", "docx", "document", "documents", "folder", "downloads", "desktop",
               "assignment", "ppt", "pptx", "excel", "xlsx", "image", "photo", "picture", "resume", "cv", "invoice",
               "receipt", "notes", "zip", "lecture", "report", "thesis", "lab", "slides", "فائل", "फाइल"})
_FM_FILLER = _ns({"my", "the", "a", "an", "for", "please", "me", "in", "on", "pc", "laptop", "computer", "that", "this",
                  "file", "files", "up", "it", "now", "kar", "karo", "kardo", "do", "mere", "meri", "mera", "ka", "ki",
                  "ko", "کرو", "کر", "دو", "میرا", "میری", "करो", "कर", "दो", "मेरा", "मेरी"})

STOP_WORDS = _ns({"stop", "quiet", "silence", "enough", "cancel", "shh", "shush", "ruko", "ruk", "bas", "chup",
                  "رکو", "رک", "بس", "چپ", "रुको", "रुक", "बस", "चुप"})
_STOP_FILLER = _ns({"it", "talking", "speaking", "please", "now", "jao", "jaao", "karo", "kardo", "kar", "do", "nyro",
                    "ok", "okay", "alright", "just", "yes", "yeah", "oh",
                    "hey", "the", "that", "everything", "all", "جاؤ", "کرو", "کر", "دو", "जाओ", "करो", "कर", "दो"})

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
    "gemini": ["gemini", "جیمنی", "जेमिनी"],
    "vscode": ["vscode", "vs code", "visual studio code", "وی ایس کوڈ", "वीएस कोड"],
    "androidstudio": ["android studio", "اینڈرائیڈ اسٹوڈیو", "एंड्रॉयड स्टूडियो"],
    "pycharm": ["pycharm", "py charm"],
    "intellij": ["intellij", "intellij idea"],
    "clion": ["clion", "c lion"],
    "webstorm": ["webstorm", "web storm"],
    "antigravity": ["antigravity", "anti gravity"],
    "sublime": ["sublime text", "sublime"],
    "visualstudio": ["visual studio"],
    "settings": ["windows settings", "settings", "سیٹنگز", "सेटिंग्स"],
    "windowsupdate": ["windows update", "ونڈوز اپڈیٹ", "विंडोज अपडेट"],
    "wifi": ["wifi settings", "wi fi settings"],
    "bluetooth": ["bluetooth settings"],
    "display": ["display settings"],
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


_ALIAS_WORDS = {w for alias, _ in _ALIASES for w in alias.split()}
_OPEN_FILLER = _ns({"the", "a", "my", "please", "now", "on", "in", "at", "for", "me", "app", "application", "website",
                    "site", "browser", "window", "laptop", "pc", "computer", "and", "then", "it", "up",
                    "kar", "karo", "kardo", "kardijiye", "do", "dena", "de", "mein", "me", "main", "par", "pe", "se",
                    "ko", "ka", "ki", "ke", "zara", "jaldi",
                    "میں", "پر", "سے", "کو", "کرو", "کر", "دو", "دیں", "کریں", "ذرا", "جلدی",
                    "में", "पर", "से", "को", "करो", "कर", "दो", "ज़रा", "जल्दी"})


def _only_open_words(t: str) -> bool:
    """True when the sentence is just: open-verb + site/app/browser names + filler words."""
    words = [w.strip(".") for w in t.split()]
    return all(w in OPEN_VERBS or w in _ALIAS_WORDS or w in _OPEN_FILLER or not w for w in words)


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

    words = [w.strip(".") for w in toks]
    if len(words) <= 4 and any(w in STOP_WORDS for w in words) and all(w in STOP_WORDS or w in _STOP_FILLER for w in words):
        return Intent("local", "sysop", "stop_speaking", {}, lang)      # "stop", "stop talking", "ruk jao", "bas karo"

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
        # only for a plain "open X (in Y)". Anything with extra instructions ("...and in Gemini type hello")
        # goes to the mouse/keyboard agent, which can open things AND keep going.
        if found and _only_open_words(t):
            sites_apps = [c for c in found if c not in BROWSER_NAMES]
            browsers = [c for c in found if c in BROWSER_NAMES]
            if sites_apps:
                return Intent("local", "sysop", "open_target",
                              {"target": sites_apps[0], "browser": browsers[0] if browsers else None}, lang)
            return Intent("local", "sysop", "open_target", {"target": browsers[0], "browser": None}, lang)

    # ---- file manager: organise folders, arrange desktop icons, undo, find files
    fm = _parse_file_manager(bare, lang)
    if fm:
        return fm

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


def _parse_file_manager(words: list[str], lang: str) -> Intent | None:
    ws = set(words)
    has_dl, has_dt, has_icons = bool(ws & DOWNLOAD_WORDS), bool(ws & DESKTOP_WORDS), bool(ws & ICON_WORDS)
    # restore the desktop icon layout
    if ws & RESTORE_WORDS and has_icons and (has_dt or "layout" in ws or "icons" in ws or "shortcuts" in ws):
        return Intent("local", "filemanager", "restore_icons", {}, lang)
    # undo the last organise ("undo", "undo cleanup", "revert downloads")
    undo_ok = _ns({"undo", "revert", "organize", "organise", "cleanup", "clean", "up", "tidy", "sort", "last", "that",
                   "it", "the", "my", "please", "wapas", "karo", "kardo", "do", "layout", "arrange"}) | DOWNLOAD_WORDS | DESKTOP_WORDS | UNDO_WORDS
    if ws & UNDO_WORDS and ws <= undo_ok and len(words) <= 5:
        return Intent("local", "filemanager", "undo_organize",
                      {"folder": "downloads" if has_dl else "desktop" if has_dt else None}, lang)
    # organise / arrange
    if ws & ORG_VERBS:
        if has_dt and has_icons:
            return Intent("local", "filemanager", "arrange_icons", {"mode": "name" if "name" in ws else "category"}, lang)
        if has_dt:
            return Intent("local", "filemanager", "organize_desktop", {}, lang)
        if has_dl:
            return Intent("local", "filemanager", "organize_folder", {"folder": "downloads"}, lang)
    # "open downloads", "open the desktop folder", "downloads kholo"
    if ws & OPEN_VERBS and (has_dl or has_dt) and ws <= (OPEN_VERBS | DOWNLOAD_WORDS | DESKTOP_WORDS | _FM_FILLER
                                                         | _ns({"folder", "فولڈر", "फोल्डर"})):
        return Intent("local", "filemanager", "open_folder", {"folder": "downloads" if has_dl else "desktop"}, lang)
    # find a file: needs a file-ish word so "find lofi music" is not treated as a file search
    if words and words[0] in FIND_VERBS and ws & FILEISH:
        query = " ".join(w for w in words[1:] if w not in _FM_FILLER)
        if query:
            return Intent("local", "filemanager", "find_files", {"query": query}, lang)
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


# The user wants Nyro to DO things. It only guides (points/draws) when asked to explain or when it is a question.
_QUESTION_START = re.compile(
    r"^(?:(?:can|could|would|will) you |please |hey |nyro |ok |okay )*"
    r"(?:explain|how|why|what|whats|which|where|who|show me|guide|teach|tell me|describe|walk me|help me understand|"
    r"is there|does|do you|samjhao|samjha|kaise|kyun|kyon|kya|batao|bataiye)\b")
_GUIDEY = re.compile(r"(\bslide\b|\bguide\b|\bpoint\b|\bshow me\b|\bhelp me\b|\berror\b|\bsyntax\b|اسکرین|یہاں|یہ\b|کیا)")
_GUIDE_ANYWHERE = re.compile(r"(\bexplain\b|\bguide me\b|\bteach me\b|\bwhere (is|are)\b|\bshow me (how|where)\b|\bsamjhao\b|"
                             r"سمجھا|کیسے|کیوں|بتاؤ|समझा|कैसे|क्यों|बताओ)")


_LANGS = r"(?:python|cpp|csharp|dart|flutter|javascript|typescript|java|kotlin|golang|rust|php|ruby|swift|html|css|sql)"
_THING = r"(?:file|program|script|code|app|class|function|project|snippet)"
_IDES = r"(?:vscode|visual studio|pycharm|intellij|android studio|clion|webstorm|cursor|sublime|antigravity)"
_MAKE = r"(?:create|make|write|generate|code|build|develop)"
_CODE = re.compile(
    r"(\.(?:py|cpp|cc|cxx|c|h|hpp|js|mjs|ts|tsx|jsx|dart|java|kt|cs|go|rs|swift|php|rb|html|css|sql)\b"
    rf"|\b{_LANGS}\b.*\b{_THING}\b|\b{_THING}\b.*\b{_LANGS}\b|\b{_MAKE}\b.*\b{_LANGS}\b"
    rf"|\b{_MAKE}\b.*\b{_IDES}\b|\b{_IDES}\b.*\b{_MAKE}\b)")


def _speech_to_code_words(text: str) -> str:
    """'C++' / 'C#' / 'VS Code' / 'dot py' are mangled by normalization or speech-to-text; spell them out."""
    t = re.sub(r"c\s*\+\+|c plus plus|see plus plus", "cpp", text, flags=re.I)
    t = re.sub(r"c\s*#|c sharp", "csharp", t, flags=re.I)
    t = re.sub(r"vs\s*code|visual studio code|v s code", "vscode", t, flags=re.I)
    return re.sub(r"\bdot (py|cpp|js|dart|java|ts|html|css|cs|go|rs)\b", r".\1", t, flags=re.I)


_ORGANIZE = re.compile(r"\b(organi[sz]e|tidy|declutter|clean ?up|sort out|arrange)\b.*\b(downloads?|desktop|icons|shortcuts)\b"
                       r"|\b(downloads?|desktop)\b.*\b(organi[sz]e|tidy|declutter|clean ?up|sort out)\b")
_SOLVE = re.compile(r"\b(solve|solution|answer|answers|complete|make me|write me|prepare|do my|fill)\b")
_FM_FIND = re.compile(
    r"\b(find|search|locate|look for|where (is|are|did)|where's|show me|open|dhundo)\b.*\b(file|files|pdf|docx?|document|"
    r"documents|folder|downloads?|assignment|photo|picture|image|video|invoice|receipt|notes?|zip|presentation|ppt|"
    r"spreadsheet|excel|sheet|lab|report|resume|cv|thesis|lecture|slides)\b")


def route_persona(text: str) -> str:
    t = normalize(_speech_to_code_words(text))
    if _CODE.search(t) and not _INPUT.search(t.replace("code", "")):
        return "coder"                         # "make a .py file in vs code", "write a c++ program in clion"
    if _ORGANIZE.search(t):                    # "organise my downloads but keep invoices separate", "tidy my desktop"
        return "filemanager"
    if _DOCS.search(t) and _SOLVE.search(t):   # "make a solution Word file for the assignment PDF in Downloads"
        return "fileclerk"
    if _FM_FIND.search(t):                     # "where is my assignment pdf", "find the invoice I downloaded"
        return "filemanager"
    if _DOCS.search(t):
        return "fileclerk"
    if _QUESTION_START.match(t) or _GUIDE_ANYWHERE.search(t):
        return "screencoach"                   # explain / point / draw on the screen
    if _GUIDEY.search(t) and not _INPUT.search(t):
        return "screencoach"                   # "help me here", "there is a syntax error"
    if _FILE.search(t) and not _SCREEN.search(t) and not _INPUT.search(t):
        return "fileclerk"
    # everything else is an instruction: operate the mouse and keyboard (it can also open sites/apps, close tabs,
    # and answers plain chat in one sentence without touching anything)
    return "desktopctl"


def route(text: str) -> Intent:
    local = parse_local(text)
    if local:
        return local
    return Intent("llm", route_persona(text), lang=detect_language(text))
