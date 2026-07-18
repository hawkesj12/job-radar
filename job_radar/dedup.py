"""De-duplication: an exact company+normalized-title key, a fuzzy secondary pass
(same role re-titled across sources), and ATS-slug extraction from a job URL
(the discovery funnel's input)."""

from __future__ import annotations

import re

from rapidfuzz import fuzz as _rf_fuzz

from . import config

_CORP_SUFFIX = re.compile(r"\b(inc|llc|ltd|corp|co|company|the)\b")
# Work-arrangement / modifier tokens that DON'T distinguish one role from another
# ("AI Engineer" vs "AI Engineer - Remote" is the same role; "AI Engineer" vs
# "AI Engineer, Payments" is not). Stripped only for the fuzzy length comparison,
# so a noise-only difference still merges but a real extra token blocks the merge.
_TITLE_NOISE = frozenset(
    {"remote", "onsite", "hybrid", "wfh", "telecommute", "anywhere", "us", "usa"}
)


def _title_core(t: str) -> str:
    """A normalized title with work-arrangement noise tokens dropped (falls back to
    the full title if stripping would empty it)."""
    toks = [w for w in normalize_title(t).split() if w not in _TITLE_NOISE]
    return " ".join(toks) or normalize_title(t)


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def normalize_title(t: str) -> str:
    # Keep seniority (Staff/Senior/Lead are genuinely different roles). Cross-source
    # re-titling of the SAME role is still caught by the fuzzy pass; the fuzzy score
    # of two distinct levels (e.g. "staff … eng" vs "senior … eng") stays below the
    # match threshold, so they no longer collapse into one.
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def dedup_key(p: dict) -> str:
    return norm(p.get("company", "")) + "|" + normalize_title(p.get("title", ""))


def company_block(p: dict) -> str:
    return _CORP_SUFFIX.sub("", norm(p.get("company", ""))).strip()


def fuzzy_title_match(a: str, b: str, cfg=None) -> bool:
    cfg = cfg or config.active()
    if not a or not b:
        return False
    # Both gates must clear: token_set_ratio catches reordered/re-punctuated
    # retitles, and token_sort_ratio (length-sensitive, on the noise-stripped core)
    # rejects a bare-subset title merging into a longer, more-specific one — while
    # still merging when the only difference is a modifier like "Remote". See
    # fuzzy_title_sort_floor and _TITLE_NOISE.
    if _rf_fuzz.token_set_ratio(a, b) < cfg.fuzzy_title_threshold:
        return False
    return (
        _rf_fuzz.token_sort_ratio(_title_core(a), _title_core(b))
        >= cfg.fuzzy_title_sort_floor
    )


def find_hit_key(p: dict, hits: dict, blocks: dict, cfg=None):
    """Resolve p to an existing hit and hand back the values computed doing so.

    Returns `(match, key, blk, nt)`:
      * `match` — the existing key this posting dedups into, or None if it's new
      * `key` / `blk` / `nt` — p's dedup_key / company_block / normalized title,
        computed ONCE here so the caller reuses them on insert instead of
        re-deriving all three (they used to be recomputed engine-side — the
        redundant 2–4×-per-posting work this eliminates).

    `blocks` is a company-block index (block -> [key]) so the fuzzy pass only
    compares against hits in the SAME company, not the whole set — turning an
    O(n) scan per posting into O(hits-in-this-company). The compared hits carry
    their normalized title precomputed on insert (`_nt`), so nothing is re-derived
    inside the loop. Together this keeps de-dup linear instead of O(n²)."""
    key = dedup_key(p)
    blk = company_block(p)
    nt = normalize_title(p.get("title", ""))
    if key in hits:
        return key, key, blk, nt
    if blk:
        for k in blocks.get(blk, ()):
            cur = hits.get(k)
            if cur is not None and fuzzy_title_match(nt, cur.get("_nt", ""), cfg):
                return k, key, blk, nt
    return None, key, blk, nt


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
