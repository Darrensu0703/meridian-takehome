from __future__ import annotations

import re


_TERM_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{1,80}$")


def extract_definition_term(text: str) -> str | None:
    """
    Extract a candidate term from questions like:
      - "what does booked_q1 mean?"
      - "define `attainment`"
      - "what is quota_q1_2026?"
    """
    t = (text or "").strip()
    if not t:
        return None

    lower = t.lower()
    if not any(k in lower for k in ("what does", "what is", "define", "meaning of")):
        return None

    # Prefer backticked terms.
    m = re.search(r"`([^`]+)`", t)
    if m:
        candidate = m.group(1).strip()
        if _TERM_RE.fullmatch(candidate):
            return candidate

    # Common phrasing patterns.
    patterns = [
        r"what does\s+([a-zA-Z][a-zA-Z0-9_]*)\s+mean\??",
        r"define\s+([a-zA-Z][a-zA-Z0-9_]*)\??",
        r"meaning of\s+([a-zA-Z][a-zA-Z0-9_]*)\??",
        r"what is\s+([a-zA-Z][a-zA-Z0-9_]*)\??",
    ]
    for p in patterns:
        m2 = re.search(p, lower)
        if m2:
            candidate = m2.group(1).strip()
            if _TERM_RE.fullmatch(candidate):
                return candidate

    # Last resort: find the first snake_case token.
    m3 = re.search(r"\b([a-zA-Z][a-zA-Z0-9_]{1,80})\b", t)
    if m3 and _TERM_RE.fullmatch(m3.group(1)):
        return m3.group(1)
    return None

