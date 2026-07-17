"""De-duplication: an exact company+normalized-title key, a fuzzy secondary pass
(same role re-titled across sources), and ATS-slug extraction from a job URL
(the discovery funnel's input)."""

from __future__ import annotations

import re

from . import config

try:
    from rapidfuzz import fuzz as _rf_fuzz
except ImportError:  # optional; degrade to exact-match dedup
    _rf_fuzz = None

_SENIORITY = re.compile(
    r"^(senior|sr\.?|staff|lead|principal|junior|jr\.?|mid|entry[- ]level)\s+", re.I
)
_CORP_SUFFIX = re.compile(r"\b(inc|llc|ltd|corp|co|company|the)\b")


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def normalize_title(t: str) -> str:
    t = _SENIORITY.sub("", (t or "").lower())
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def dedup_key(p: dict) -> str:
    return norm(p.get("company", "")) + "|" + normalize_title(p.get("title", ""))


def company_block(p: dict) -> str:
    return _CORP_SUFFIX.sub("", norm(p.get("company", ""))).strip()


def fuzzy_title_match(a: str, b: str, cfg=None) -> bool:
    cfg = cfg or config.active()
    if _rf_fuzz is None or not a or not b:
        return False
    return _rf_fuzz.token_set_ratio(a, b) >= cfg.fuzzy_title_threshold


def find_hit_key(p: dict, hits: dict, blocks: dict, cfg=None):
    """Resolve p to an existing hit: exact key first, then a fuzzy-title near-dup
    within the same company block. Returns the matching key or None (new role).

    `blocks` is a company-block index (block -> [key]) so the fuzzy pass only
    compares against hits in the SAME company, not the whole set — turning an
    O(n) scan per posting into O(hits-in-this-company). The compared hits carry
    their normalized title precomputed on insert (`_nt`), so nothing is re-derived
    inside the loop. Together this keeps de-dup linear instead of O(n²)."""
    key = dedup_key(p)
    if key in hits:
        return key
    if _rf_fuzz is None:
        return None
    blk = company_block(p)
    if not blk:
        return None
    ptitle = normalize_title(p.get("title", ""))
    for k in blocks.get(blk, ()):
        cur = hits.get(k)
        if cur is not None and fuzzy_title_match(ptitle, cur.get("_nt", ""), cfg):
            return k
    return None


def ats_from_url(url: str):
    """Map a job/apply URL to (ats, slug) when it points at a known ATS host."""
    if not url:
        return None
    u = url.lower()
    patterns = [
        (
            r"(?:job-)?boards(?:\.eu)?\.greenhouse\.io/(?:embed/job_app\?for=)?([^/?#]+)",
            "greenhouse",
        ),
        (r"jobs\.lever\.co/([^/?#]+)", "lever"),
        (r"jobs\.ashbyhq\.com/([^/?#]+)", "ashby"),
        (r"apply\.workable\.com/([^/?#]+)", "workable"),
        (r"jobs\.smartrecruiters\.com/([^/?#]+)", "smartrecruiters"),
    ]
    for rx, ats in patterns:
        m = re.search(rx, u)
        if m and m.group(1) not in ("embed", "j"):
            return (ats, m.group(1))
    return None
