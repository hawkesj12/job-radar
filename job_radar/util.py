"""Shared helpers: HTTP, text cleaning, dates, salary parsing, word matching."""

from __future__ import annotations

import html
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime

from . import config


def get_json(url: str):
    cfg = config.active()
    req = urllib.request.Request(url, headers={"User-Agent": cfg.user_agent})
    with urllib.request.urlopen(req, timeout=cfg.timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def q(s: str) -> str:
    return urllib.parse.quote(s)


def clean(raw: str) -> str:
    if not raw:
        return ""
    txt = re.sub(r"<[^>]+>", " ", raw)
    txt = html.unescape(txt)  # decode &amp; &#39; etc.
    return re.sub(r"\s+", " ", txt).strip()


def to_date(val) -> str:
    """Normalize an ISO string / epoch-seconds / epoch-ms to YYYY-MM-DD."""
    if not val:
        return ""
    if isinstance(val, (int, float)):
        ts = val / 1000 if val > 1e11 else val
        try:
            return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        except Exception:
            return ""
    return str(val)[:10]


_SAL_RE = re.compile(
    r"\$\s?\d{2,3}(?:,\d{3})?\s?[kK]?\s?(?:[-–—]|to)\s?\$?\s?\d{2,3}(?:,\d{3})?\s?[kK]?"
)


def salary_from_text(text: str) -> str:
    m = _SAL_RE.search(text or "")
    return re.sub(r"\s+", " ", m.group(0)).strip() if m else ""


def salary_range(lo, hi) -> str:
    try:
        lo = int(float(lo or 0))
        hi = int(float(hi or 0))
    except Exception:
        return ""
    if lo and hi:
        return f"${lo:,}–${hi:,}"
    if lo or hi:
        return f"${(lo or hi):,}"
    return ""


def has(kw: str, text: str) -> bool:
    """Whole-word match: 'ai' hits 'AI' but not 'training' / 'available'."""
    return re.search(rf"(?<![a-z0-9]){re.escape(kw)}(?![a-z0-9])", text) is not None


def env(key: str) -> str:
    import os

    return os.environ.get(key, "") or ""


def age_int(posted: str):
    if not posted:
        return None
    try:
        return (datetime.now() - datetime.strptime(posted[:10], "%Y-%m-%d")).days
    except Exception:
        return None
