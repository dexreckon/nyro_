"""WebPilot pure helpers (URL policy, form safety). Browser I/O lives in webpilot_mcp.py."""
from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

MAX_DOM_TEXT = 8000
SENSITIVE_INPUT_TYPES = {"password"}
SENSITIVE_NAME_HINTS = ("password", "passwd", "pwd", "cvv", "cvc", "card", "ssn", "otp", "pin")


def check_url(url: str, allowed_schemes: list[str]) -> str:
    """Return the normalized URL or raise ValueError. Blocks file:, javascript:, data:, etc."""
    u = urlparse(url.strip())
    if u.scheme.lower() not in allowed_schemes:
        raise ValueError(f"Scheme not allowed: {u.scheme or '(none)'}")
    if not u.hostname:
        raise ValueError("URL has no host")
    return url.strip()


def is_private_host(host: str) -> bool:
    h = host.strip("[]").lower()
    if h in {"localhost"} or h.endswith(".local") or h.endswith(".internal"):
        return True
    try:
        ip = ipaddress.ip_address(h)
        return ip.is_private or ip.is_loopback or ip.is_link_local
    except ValueError:
        return False


def is_sensitive_field(input_type: str | None, name_or_id: str | None) -> bool:
    if (input_type or "").lower() in SENSITIVE_INPUT_TYPES:
        return True
    n = (name_or_id or "").lower()
    return any(h in n for h in SENSITIVE_NAME_HINTS)
