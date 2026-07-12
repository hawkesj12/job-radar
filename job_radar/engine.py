"""The harvest pipeline: poll depth (ATS) + breadth (aggregator) sources,
filter -> score -> dedup into one ranked list, and grow the watchlist from any
newly-discovered ATS slugs. Returns scored postings; the store writes them."""

from __future__ import annotations

import json
import time
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from . import config
from .dedup import dedup_key, find_hit_key, norm
from .funnel import append_watchlist, funnel
from .scoring import is_remote, relevant, score, top_signals
from .sources import enabled_breadth, enabled_depth
from .util import age_int

APPLIED_DOOR_DEFAULT = None  # uses cfg.applied_door


def _consume(postings, hits, cfg, meta):
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
        sc = score(p, cfg)
        sig = top_signals(p, cfg=cfg)
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

        match = find_hit_key(p, hits, cfg)
        if match is None:
            hits[dedup_key(p)] = p
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
            hits[match] = winner


def harvest(cfg=None, watchlist_path=None):
    """Run a full scan. Returns (rows, discovered, errors)."""
    cfg = cfg or config.active()
    companies = []
    if watchlist_path:
        try:
            companies = json.loads(open(watchlist_path).read()).get("companies", [])
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
    hits, errors = {}, []

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
                _consume(ps, hits, cfg, meta)

    breadth_postings = []
    for name, fn in enabled_breadth(cfg):
        try:
            ps = fn(cfg.title_queries)
        except Exception as e:  # noqa: BLE001
            errors.append(f"breadth:{name}: {type(e).__name__}")
            continue
        breadth_postings += ps
        _consume(ps, hits, cfg, meta)
        time.sleep(1)

    discovered = []
    if cfg.funnel_auto_grow and watchlist_path:
        found = funnel(breadth_postings, known_companies, known_slugs, cfg)
        from pathlib import Path

        discovered = append_watchlist(Path(watchlist_path), found)

    rows = sorted(hits.values(), key=lambda p: p["score"], reverse=True)
    return rows, discovered, errors
