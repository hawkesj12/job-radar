"""The harvest pipeline: poll depth (ATS) + breadth (aggregator) sources,
filter -> score -> dedup into one ranked list, and grow the watchlist from any
newly-discovered ATS slugs. Returns scored postings; the store writes them."""

from __future__ import annotations

import json
import re
import urllib.error
from concurrent.futures import ThreadPoolExecutor

from . import config
from .dedup import find_hit_key, norm
from .funnel import append_watchlist, funnel
from .scoring import is_remote, relevant, score_and_signals
from .sources import DEPTH_EXTRA_FIELDS, enabled_breadth, enabled_depth
from .util import age_int

# A valid ATS slug is the last path segment of a board URL — alphanumerics plus
# -, _, . only. Reject anything else so a hand-edited watchlist can't inject path
# traversal (`../`) or a query into the fixed API URLs the slug is spliced into.
_SLUG_RE = re.compile(r"^[A-Za-z0-9._-]+$")


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

        # find_hit_key computes dedup_key/block/normalized-title once and returns
        # them, so the insert branch below reuses them instead of re-deriving.
        match, key, blk, nt = find_hit_key(p, hits, blocks, cfg)
        if match is None:
            p["_blk"] = blk  # block + normalized title, stashed for the fuzzy pass
            p["_nt"] = nt
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
                winner["_blk"], winner["_nt"] = cur["_blk"], nt  # p's own title
            winner["dedup_key"] = match
            hits[match] = winner


def harvest(cfg=None, watchlist_path=None):
    """Run a full scan. Returns (rows, discovered, errors)."""
    cfg = cfg or config.active()
    companies = []
    watchlist_err = None
    if watchlist_path:
        try:
            with open(watchlist_path, encoding="utf-8") as f:
                companies = json.loads(f.read()).get("companies", [])
        except (OSError, json.JSONDecodeError) as e:
            # A corrupt/unreadable watchlist must be LOUD -- silently dropping the
            # entire depth harvest (all your companies) is the one place this tool
            # would betray its own fail-fast rule. Surfaced via `errors` below.
            watchlist_err = f"watchlist {watchlist_path}: {type(e).__name__}"

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
    if watchlist_err:
        errors.append(watchlist_err)

    def _fetch_company(c):
        ats, slug = c.get("ats"), c.get("slug")
        name = c.get("name", slug or "?")
        fetch = depth.get(ats)
        if not fetch:
            return (c, None, f"{name}: source '{ats}' not enabled")
        if not slug or not _SLUG_RE.match(slug):
            return (c, None, f"{name}: invalid slug {slug!r}")
        # Most ATSs key on the slug alone; Workday needs host + site too. Pull only
        # the fields that adapter declared, and fail LOUD on a missing one rather
        # than fetching a wrong-but-valid URL.
        extra = {}
        for field in DEPTH_EXTRA_FIELDS.get(ats, ()):
            val = c.get(field)
            if not val or not _SLUG_RE.match(str(val)):
                return (c, None, f"{name} ({ats}): missing/invalid {field}={val!r}")
            extra[field] = val
        try:
            return (c, fetch(slug, **extra), None)
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
