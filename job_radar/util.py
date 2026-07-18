"""Shared helpers: HTTP, text cleaning, dates, salary parsing, word matching."""

from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from functools import lru_cache
from zoneinfo import ZoneInfo

from . import config

# Expected transient fetch failures (network down, timeout, a source returning
# non-JSON). Catching THESE and moving on is correct; catching everything hides a
# real schema-break bug (KeyError/AttributeError) as if the source had no jobs.
NET_ERRORS = (urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeError)

# All dates are Eastern Time (this is a US-centric job tool; naive local time gave
# off-by-one ages near the day boundary depending on the machine's zone).
_ET = ZoneInfo("America/New_York")


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
            return datetime.fromtimestamp(ts, tz=_ET).strftime("%Y-%m-%d")
        except Exception:
            return ""
    return str(val)[:10]


_SAL_RE = re.compile(
    r"\$\s?\d{2,3}(?:,\d{3})?\s?[kK]?\s?(?:[-–—]|to)\s?\$?\s?\d{2,3}(?:,\d{3})?\s?[kK]?"
)
# A range is only a SALARY if it reads like pay: it carries a k / thousands, or is
# immediately followed by a per-period unit. A magnitude word right after (million/
# billion/M/B) means it's funding or revenue, not comp — reject it.
_SAL_MAGNITUDE = re.compile(r"^\s*(?:million|billion|mn|bn|[mb])\b", re.I)
_SAL_UNIT = re.compile(
    r"^\s*(?:/|per\b)?\s*(?:hr|hour|yr|year|annum|annually|wk|week|mo|month|k\b)",
    re.I,
)


def salary_from_text(text: str) -> str:
    m = _SAL_RE.search(text or "")
    if not m:
        return ""
    matched = m.group(0)
    tail = (text or "")[m.end() :]
    if _SAL_MAGNITUDE.match(tail):  # "$20-40 million in Series B" -> funding
        return ""
    has_anchor = "k" in matched.lower() or "," in matched
    if not has_anchor and not _SAL_UNIT.match(tail):  # bare "$20-40" -> too ambiguous
        return ""
    return re.sub(r"\s+", " ", matched).strip()


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


@lru_cache(maxsize=None)
def _kw_re(kw: str) -> re.Pattern:
    """Compile a keyword's whole-word matcher once (keyword lists are static per
    config; this is called ~200×/posting, so caching the compile matters)."""
    return re.compile(rf"(?<![a-z0-9]){re.escape(kw)}(?![a-z0-9])")


def has(kw: str, text: str) -> bool:
    """Whole-word match, CASE-SENSITIVE: 'ai' hits 'ai' but not 'training' /
    'available'. Callers lowercase both the keyword and the text first (keyword
    lists are lowercase; the scored blob is `.lower()`-ed), so this never uppercases."""
    return _kw_re(kw).search(text) is not None


def today_et() -> str:
    """Today's date (YYYY-MM-DD) in Eastern Time — the tool's single zone, so
    first_seen can't sit off-by-one from age_int's ET-based math near midnight."""
    return datetime.now(_ET).strftime("%Y-%m-%d")


def age_int(posted: str):
    if not posted:
        return None
    try:
        d = datetime.strptime(posted[:10], "%Y-%m-%d").replace(tzinfo=_ET)
        return (datetime.now(_ET) - d).days
    except Exception:
        return None
