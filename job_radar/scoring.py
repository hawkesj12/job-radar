"""The deterministic fit engine: relevance gate, remote gate, and the weighted
keyword score (BM25 length-normalized). No AI here -- same input, same output,
every time. The optional LLM re-rank (llm.py) layers on top of this."""

from __future__ import annotations

import re
from functools import lru_cache

from . import config
from .util import has

_TOKEN_RE = re.compile(r"[a-z0-9]+")


@lru_cache(maxsize=None)
def _kw_index(keys: tuple[str, ...]):
    """Split fit-weight keys into SINGLE-token keys (a lone [a-z0-9]+ run — present
    iff the token is in the text's token set, exactly equivalent to a whole-word
    regex) and MULTI-token keys (contain a space/hyphen — still need the regex),
    each multi paired with its first alnum token for a cheap prefilter. Memoized
    per keyword set (the keys are static per config)."""
    singles, multis = set(), []
    for k in keys:
        if _TOKEN_RE.fullmatch(k):
            singles.add(k)
        else:
            toks = _TOKEN_RE.findall(k)
            multis.append((k, toks[0] if toks else ""))
    return frozenset(singles), tuple(multis)


def _present(text: str, tokset: set, fw: dict, singles, multis) -> list:
    """[(weight, keyword)] for every fit-weight keyword present in `text` — exactly
    equivalent to `[(w, kw) for kw, w in fw.items() if has(kw, text)]`, but resolves
    single-token keywords by O(1) set membership and only runs the whole-word regex
    for a multi-token keyword when its first token is present (a prefilter that can
    only skip provably-absent keywords, so the result is unchanged)."""
    hits = [(fw[kw], kw) for kw in singles if kw in tokset]
    hits += [(fw[kw], kw) for kw, first in multis if first in tokset and has(kw, text)]
    return hits


# ── remote detection ────────────────────────────────────────────────────────
# A pure predicate shared with downstream consumers (jobfitr). Title/location
# match liberally; the BODY must hit a role-remoteness phrase AND not be negated,
# which recovers Adzuna/USAJOBS roles that are genuinely remote but only say so in
# the description (their APIs carry no reliable remote flag).
_REMOTE_RE = re.compile(r"remote|anywhere|work from home|\bwfh\b", re.I)
_REMOTE_BODY_RE = re.compile(
    r"\b(?:fully|100%|completely|permanently)\s+remote\b"
    r"|\bremote[- ](?:first|friendly|eligible|position|role|opportunity|work|based)\b"
    r"|\b(?:this|the)\s+(?:is\s+a\s+)?remote\s+(?:position|role|job|opportunity)\b"
    r"|\bwork[- ]from[- ]home\b|\bwork\s+from\s+home\b"
    r"|\btelecommut\w*|\btelework\w*"
    r"|\bremote\s+(?:within|in|across|throughout|anywhere)\b",
    re.I,
)
_REMOTE_NEG_RE = re.compile(
    r"\bno[t]?\s+(?:a\s+)?remote\b|\bno\s+remote\b|\bnon[- ]?remote\b"
    r"|\bon[- ]?site\s+only\b|\bin[- ]office\s+only\b"
    r"|\bnot\s+(?:a\s+)?remote\s+(?:position|role|job)\b",
    re.I,
)


def remote_posting(title: str, location: str, body: str = "") -> bool:
    """True when a role is remote. Title/location match liberally; the body must
    hit a role-remoteness phrase and not be negated. Pure -- no config, safe to
    share with jobfitr's tag derivation."""
    head = f"{title} {location}"
    if _REMOTE_NEG_RE.search(head):  # "Non-remote ...", "On-site only" -> not remote
        return False
    if _REMOTE_RE.search(head):
        return True
    if body and _REMOTE_BODY_RE.search(body) and not _REMOTE_NEG_RE.search(body):
        return True
    return False


def relevant(title: str, cfg=None) -> bool:
    cfg = cfg or config.active()
    t = title.lower()
    if any(has(x, t) for x in cfg.exclude_titles):
        return False
    return any(has(x, t) for x in cfg.title_signal)


def is_remote(p: dict, cfg=None) -> bool:
    cfg = cfg or config.active()
    if not cfg.remote_only:
        return True
    if not remote_posting(p.get("title", ""), p.get("location", ""), p.get("text", "")):
        return False
    b = f"{p.get('title', '')} {p.get('location', '')}".lower()
    return not any(x in b for x in cfg.exclude_locations)


def score_and_signals(p: dict, n: int = 7, cfg=None) -> tuple[int, str]:
    """Score a posting AND derive its top signal labels in ONE pass over
    `fit_weights` (each keyword is counted independently, so overlapping keywords
    like 'ai' and 'ai engineer' both contribute). `score()` and `top_signals()`
    are thin wrappers so the public API is unchanged; the engine calls this to
    avoid walking `fit_weights` over the blob twice."""
    cfg = cfg or config.active()
    fw = cfg.fit_weights
    singles, multis = _kw_index(tuple(fw))
    blob = f"{p.get('title', '')} {p.get('location', '')} {p.get('text', '')}".lower()
    blob_tokens = _TOKEN_RE.findall(blob)  # tokenize ONCE — reused for length + hits
    blob_hits = _present(blob, set(blob_tokens), fw, singles, multis)
    raw = sum(w for w, _ in blob_hits)
    # BM25-style length normalization: divide the body score by a saturating
    # length factor so a long JD can't accrue score just by being long, then cap.
    dl = len(blob_tokens)
    norm = (1 - cfg.score_len_b) + cfg.score_len_b * (dl / cfg.avg_jd_tokens)
    # Real BM25, not `raw / norm`. That earlier form is the k1 -> infinity limit,
    # which removes term-frequency saturation and lets `norm`'s 0.25 floor multiply a
    # short document's score by up to 4x. Since every keyword here contributes at
    # most once (presence-based, tf == 1), BM25's tf*(k1+1)/(tf + k1*norm) collapses
    # to the expression below, which damps the same length effect without the
    # runaway multiplier. See Config.score_k1 for the measurement that motivated it.
    k1 = cfg.score_k1
    body = min(
        raw * (k1 + 1) / (1 + k1 * norm) if norm > 0 else raw, cfg.blob_score_cap
    )
    tl = p.get("title", "").lower()
    # Title double-count, but CAPPED so a keyword-stuffed title can't run away.
    title_hits = _present(tl, set(_TOKEN_RE.findall(tl)), fw, singles, multis)
    body += min(sum(w for w, _ in title_hits), cfg.title_score_cap)
    body -= sum(w for kw, w in cfg.title_penalty.items() if has(kw, tl))
    # The agency penalty runs over company + the FULL description, so it was the
    # most expensive line in the program: 13 whole-word regex searches across ~4 KB
    # per posting, measured at 68% of total scoring CPU. `_present` above solves
    # exactly this for fit_weights and was never extended here.
    #
    # The token set MUST come from agency_blob itself, not from the display blob.
    # `_present` resolves a single-token keyword by pure set membership, so passing
    # the wider title+location+text tokens made an agency keyword in the TITLE or
    # LOCATION score as though it were in the body: a role called "Staffing
    # Engineer" was penalised as a staffing agency. Tokenizing agency_blob costs one
    # more pass over the text and is still far cheaper than 13 regex searches.
    agency_blob = f"{p.get('company', '')} {p.get('text', '')}".lower()
    agency_tokens = set(_TOKEN_RE.findall(agency_blob))
    ap_singles, ap_multis = _kw_index(tuple(cfg.agency_penalty))
    agency_hits = _present(
        agency_blob, agency_tokens, cfg.agency_penalty, ap_singles, ap_multis
    )
    # CAPPED, like the body and title scores above. Uncapped, a long JD accumulated
    # penalty without limit while its body score was normalized down.
    body -= min(sum(w for w, _ in agency_hits), cfg.agency_penalty_cap)
    sig = ", ".join(kw for _, kw in sorted(blob_hits, reverse=True)[:n])
    return round(body), sig


def score(p: dict, cfg=None) -> int:
    return score_and_signals(p, cfg=cfg)[0]


def top_signals(p: dict, n: int = 7, cfg=None) -> str:
    return score_and_signals(p, n=n, cfg=cfg)[1]
