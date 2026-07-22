"""Seed the company universe from Common Crawl.

The funnel grows the watchlist *reactively* -- it only learns about a company
after one of its jobs shows up on a breadth board. This does it *proactively*:
one query to the Common Crawl CDX index enumerates (almost) every company that
hosts a public board on a given ATS, so you know their boards before any job
appears. Run it occasionally to bootstrap or widen your watchlist:

    job-radar seed greenhouse --max 300
    job-radar seed lever --max 200 --verify

`--verify` probes each new slug (slower) so only live boards are added; without
it, dead slugs are simply skipped at scan time.
"""

from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.request
from pathlib import Path

from . import config, discover
from .util import NET_ERRORS, atomic_write_text

# One error type for "Common Crawl is having a bad day", defined in discover (which
# owns the mining) and aliased here so `except seed.SeedError` keeps working.
SeedError = discover.DiscoveryError

# ATS_PATTERNS and _JUNK used to live here: a SECOND set of CDX patterns and slug
# regexes alongside discover's, and a third alongside dedup.ats_from_url. They
# disagreed — this copy still cut a greenhouse slug at `?` rather than `&`, so an
# embed URL yielded 'gemini&token=774&gh_jid=774' as a company name. Mining now
# happens in exactly one place; see discover._PATTERNS.


def _latest_cdx() -> str:
    cfg = config.active()
    req = urllib.request.Request(
        "https://index.commoncrawl.org/collinfo.json",
        headers={"User-Agent": cfg.user_agent},
    )
    try:
        with urllib.request.urlopen(req, timeout=cfg.timeout) as r:
            return json.loads(r.read().decode())[0]["cdx-api"]
    except (
        *NET_ERRORS,
        urllib.error.HTTPError,
        ConnectionError,
        http.client.HTTPException,
        KeyError,
        IndexError,
    ) as e:
        raise SeedError(f"Common Crawl index unavailable ({type(e).__name__})") from e


def enumerate_entries(ats: str, max_rows: int = 20000) -> list[dict]:
    """One CDX pass -> candidate watchlist entries for this ATS.

    Delegates to discover.mine, passing our own resolved index URL so there is no
    second collection lookup. Returns ENTRIES rather than bare slugs because
    Workday needs its host + site triple to be fetchable at all.
    """
    if ats not in discover._PATTERNS:
        raise ValueError(
            f"seed not supported for ATS '{ats}' "
            f"(try: {', '.join(sorted(discover._PATTERNS))})"
        )
    return discover.mine(ats, limit=max_rows, cdx_url=_latest_cdx())


def enumerate_tokens(ats: str, max_rows: int = 20000) -> set[str]:
    """The distinct slugs on this ATS. Kept for callers that only want names;
    `enumerate_entries` is the one that survives Workday."""
    return {e["slug"] for e in enumerate_entries(ats, max_rows)}


def seed_universe(
    ats: str, watchlist_path, limit: int = 300, verify: bool = False
) -> int:
    """Enumerate slugs from Common Crawl and append the new ones to the watchlist.
    Returns the number added."""
    wl = Path(watchlist_path)
    doc = (
        json.loads(wl.read_text(encoding="utf-8")) if wl.exists() else {"companies": []}
    )
    existing = {
        (c.get("ats"), (c.get("slug") or "").lower()) for c in doc.get("companies", [])
    }

    print(f"  querying Common Crawl for {ats} boards…")
    entries = enumerate_entries(ats)
    fresh = [
        e
        for e in sorted(entries, key=lambda e: e["slug"].lower())
        if (ats, e["slug"].lower()) not in existing
    ]
    print(f"  found {len(entries)} slugs, {len(fresh)} new")

    added: list[dict] = []
    if verify:
        # Probe concurrently, in sorted-order batches, stopping as soon as `limit`
        # boards have been confirmed. The old loop probed one board at a time with a
        # full harvest fetch each; this is the same early-stop over the same ordered
        # list, but parallel and against a cheap liveness call.
        for i in range(0, len(fresh), max(limit, 50)):
            if len(added) >= limit:
                break
            batch = fresh[i : i + max(limit, 50)]
            for e in discover.probe(batch, workers=8):
                added.append(_seeded(e))
                if len(added) >= limit:
                    break
    else:
        added = [_seeded(e) for e in fresh[:limit]]

    if added:
        doc.setdefault("companies", []).extend(added)
        atomic_write_text(wl, json.dumps(doc, indent=2) + "\n")
    return len(added)


def _seeded(entry: dict) -> dict:
    """A mined candidate -> a watchlist company. Workday's host + site ride along;
    without them the adapter cannot build a URL at all."""
    out = {
        "name": entry["slug"],
        "ats": entry["ats"],
        "slug": entry["slug"],
        "industry": "(seeded)",
    }
    for field in ("host", "site"):
        if entry.get(field):
            out[field] = entry[field]
    return out
