"""The harvest pipeline: poll depth (ATS) + breadth (aggregator) sources,
filter -> score -> dedup into one ranked list, and grow the watchlist from any
newly-discovered ATS slugs. Returns scored postings; the store writes them."""

from __future__ import annotations

import json
import urllib.error
from concurrent.futures import ThreadPoolExecutor

from . import config
from .dedup import company_block, dedup_key, find_hit_key, norm, normalize_title
from .funnel import append_watchlist, funnel
from .scoring import is_remote, relevant, score_and_signals
from .sources import enabled_breadth, enabled_depth
from .util import age_int


def _consume(postings, hits, blocks, cfg, meta):
    for p in postings:
        if not relevant(p.get("title", ""), cfg):
            continue
        if not is_remote(p, cfg):
            continue
        age = age_int(p.get("posted", ""))
        if age is not None and age > cfg.max_age_days:
            continue
        if not p.get("url"):
            continue
        p.setdefault("company", "")
        p.setdefault("source", "")
        p.setdefault("industry", "")
        m = meta.get(norm(p["company"]))
        sc, sig = score_and_signals(p, cfg=cfg)  # one keyword scan for both
        tl = p["title"].lower()
        if m and m.get("frontier") and not any(d in tl for d in cfg.applied_door):
            sc -= cfg.frontier_penalty
            sig = "frontier-reach" + (", " + sig if sig else "")
        if m and m.get("local"):
            sc += cfg.local_bonus
            sig = "local" + (", " + sig if sig else "")
        if age is not None and age > cfg.stale_after_days:
            sc -= min(12, ((age - cfg.stale_after_days) // 10) * 2)
            sig = (sig + ", " if sig else "") + f"{age}d-old"
        if m and m.get("industry") and not p["industry"]:
            p["industry"] = m["industry"]
        p["score"] = sc
        p["signals"] = sig
        p["sources"] = {p["source"]} if p["source"] else set()

        match = find_hit_key(p, hits, blocks, cfg)
        if match is None:
            key = dedup_key(p)
            # Precompute the block + normalized title ONCE, on insert, so the
            # fuzzy pass never re-derives them (the O(n²) → linear fix). Index the
            # key under its company block for same-company-only comparison.
            blk = company_block(p)
            p["_blk"] = blk
            p["_nt"] = normalize_title(p.get("title", ""))
            p["dedup_key"] = key  # stash so the store/CLI don't recompute it
            hits[key] = p
            if blk:
                blocks.setdefault(blk, []).append(key)
        else:
            cur = hits[match]
            srcs = cur["sources"] | p["sources"]
            if (p["score"], len(p.get("text", ""))) > (
                cur["score"],
                len(cur.get("text", "")),
            ):
                winner = p
            else:
                winner = cur
            winner["sources"] = srcs
            # The retained key stays `match`; carry the precomputed block/title so
            # future fuzzy compares use the WINNER's title (a new winner `p` was
            # never inserted, so derive its `_nt`; `_blk` is the shared block).
            if winner is p:
                winner["_blk"], winner["_nt"] = (
                    cur["_blk"],
                    normalize_title(p.get("title", "")),
                )
            winner["dedup_key"] = match
            hits[match] = winner


def harvest(cfg=None, watchlist_path=None):
    """Run a full scan. Returns (rows, discovered, errors)."""
    cfg = cfg or config.active()
    companies = []
    if watchlist_path:
        try:
            with open(watchlist_path) as f:
                companies = json.loads(f.read()).get("companies", [])
        except Exception:
            pass

    meta, known_slugs = {}, set()
    for c in companies:
        meta[norm(c.get("name", ""))] = {
            "frontier": bool(c.get("frontier")),
            "local": bool(c.get("local")),
            "industry": c.get("industry", ""),
        }
        known_slugs.add((c.get("ats"), (c.get("slug") or "").lower()))
    known_companies = set(meta.keys())

    depth = enabled_depth(cfg)
    # `hits` = deduped roles keyed by dedup_key; `blocks` = company-block index
    # (block -> [key]) that keeps the fuzzy de-dup linear. Both persist across the
    # depth + breadth _consume passes; _consume mutates them ONLY on the main
    # thread (workers just fetch), so the shared state needs no lock.
    hits, blocks, errors = {}, {}, []

    def _fetch_company(c):
        ats, slug = c.get("ats"), c.get("slug")
        name = c.get("name", slug or "?")
        fetch = depth.get(ats)
        if not fetch:
            return (c, None, f"{name}: source '{ats}' not enabled")
        try:
            return (c, fetch(slug), None)
        except urllib.error.HTTPError as e:
            return (c, None, f"{name} ({ats}:{slug}): HTTP {e.code}")
        except Exception as e:  # noqa: BLE001
            return (c, None, f"{name} ({ats}:{slug}): {type(e).__name__}")

    if companies:
        with ThreadPoolExecutor(max_workers=12) as ex:
            for c, ps, err in ex.map(_fetch_company, companies):
                if err:
                    errors.append(err)
                    continue
                name, ats = c.get("name", c.get("slug") or "?"), c.get("ats")
                for p in ps:
                    p["company"], p["source"], p["industry"] = (
                        name,
                        ats,
                        c.get("industry", ""),
                    )
                _consume(ps, hits, blocks, cfg, meta)

    # Breadth sources are 10 independent third-party hosts — fetch them in
    # parallel (like depth) and consume single-threaded in a stable order. No
    # cross-host sleep: rate limits are per-host, and each source already sleeps
    # between its OWN repeated calls.
    def _fetch_breadth(item):
        name, fn = item
        try:
            return (name, fn(cfg.title_queries), None)
        except Exception as e:  # noqa: BLE001
            return (name, None, f"breadth:{name}: {type(e).__name__}")

    breadth_postings = []
    breadth = enabled_breadth(cfg)
    if breadth:
        with ThreadPoolExecutor(max_workers=min(len(breadth), 10)) as ex:
            for name, ps, err in ex.map(_fetch_breadth, breadth):
                if err:
                    errors.append(err)
                    continue
                breadth_postings += ps
                _consume(ps, hits, blocks, cfg, meta)

    discovered = []
    if cfg.funnel_auto_grow and watchlist_path:
        from pathlib import Path

        try:
            found = funnel(breadth_postings, known_companies, known_slugs, cfg)
            discovered = append_watchlist(Path(watchlist_path), found)
        except Exception as e:  # noqa: BLE001 — discovery must never sink a scan
            errors.append(f"funnel: {type(e).__name__}")

    rows = sorted(hits.values(), key=lambda p: p["score"], reverse=True)
    return rows, discovered, errors
