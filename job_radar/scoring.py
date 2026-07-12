"""The deterministic fit engine: relevance gate, remote gate, and the weighted
keyword score (BM25 length-normalized). No AI here -- same input, same output,
every time. The optional LLM re-rank (llm.py) layers on top of this."""

from __future__ import annotations

import re

from . import config
from .util import has


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
    b = f"{p.get('title', '')} {p.get('location', '')}".lower()
    if not (has("remote", b) or has("remotely", b) or "work from anywhere" in b):
        return False
    return not any(x in b for x in cfg.exclude_locations)


def score(p: dict, cfg=None) -> int:
    cfg = cfg or config.active()
    fw = cfg.fit_weights
    blob = f"{p.get('title', '')} {p.get('location', '')} {p.get('text', '')}".lower()
    raw = sum(w for kw, w in fw.items() if has(kw, blob))
    # BM25-style length normalization: divide the body score by a saturating
    # length factor so a long JD can't accrue score just by being long, then cap.
    dl = len(re.findall(r"[a-z0-9]+", blob))
    norm = (1 - cfg.score_len_b) + cfg.score_len_b * (dl / cfg.avg_jd_tokens)
    body = min(raw / norm if norm > 0 else raw, cfg.blob_score_cap)
    tl = p.get("title", "").lower()
    body += sum(w for kw, w in fw.items() if has(kw, tl))  # title double
    body -= sum(w for kw, w in cfg.title_penalty.items() if has(kw, tl))
    agency_blob = f"{p.get('company', '')} {p.get('text', '')}".lower()
    body -= sum(w for kw, w in cfg.agency_penalty.items() if has(kw, agency_blob))
    return round(body)


def top_signals(p: dict, n: int = 7, cfg=None) -> str:
    cfg = cfg or config.active()
    blob = f"{p.get('title', '')} {p.get('location', '')} {p.get('text', '')}".lower()
    hits = sorted(
        ((w, kw) for kw, w in cfg.fit_weights.items() if has(kw, blob)), reverse=True
    )
    return ", ".join(kw for _, kw in hits[:n])
