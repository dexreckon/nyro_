"""Tiny language helpers for English / Urdu (script) / Roman Urdu."""
from __future__ import annotations

import re

_URDU_RE = re.compile(r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]")
_ROMAN_URDU_HINTS = {
    "awaz", "awaaz", "kam", "zyada", "ziada", "barhao", "barha", "karo", "kardo", "band", "chalao",
    "roshni", "lock", "kholo", "dikhao", "batao", "mujhe", "meri", "mera", "kya", "kaise", "samjhao",
    "yahan", "idhar", "madad", "madat", "bijli", "battery", "screen", "bhai", "yar", "yaar", "ko", "ki", "ka",
}


def urdu_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    return (sum(1 for c in letters if _URDU_RE.match(c)) / len(letters)) if letters else 0.0


def detect_language(text: str) -> str:
    """'ur' for Urdu script, 'en' otherwise (Roman Urdu is treated as 'en' for TTS)."""
    return "ur" if urdu_ratio(text) > 0.3 else "en"


def looks_roman_urdu(text: str) -> bool:
    words = re.findall(r"[a-z]+", text.lower())
    return sum(1 for w in words if w in _ROMAN_URDU_HINTS) >= 2
