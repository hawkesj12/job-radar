"""Seed the company universe from Common Crawl.

The funnel grows the watchlist *reactively* -- it only learns about a company
after one of its jobs shows up on a breadth board. This does it *proactively*:
one query to the Common Crawl CDX index enumerates (almost) every company that
hosts a public board on a given ATS, so you know their boards before any job
appears. Run it occasionally to bootstrap or widen your watchlist:

    job-radar seed greenhouse --limit 300
    job-radar seed lever --limit 200 --verify

`--verify` probes each new slug (slower) so only live boards are added; without
it, dead slugs are simply skipped at scan time.
"""

from __future__ import annotations

import json
import re
import urllib.request
from pathlib import Path

from . import config
from .sources import DEPTH_ALL

# ATS -> (CDX url-pattern, token-extract regex)
ATS_PATTERNS = {
    "greenhouse": (
        "boards.greenhouse.io/*",
        r"boards\.greenhouse\.io/(?:embed/job_app\?for=)?([^/?#]+)",
    ),
    "lever": ("jobs.lever.co/*", r"jobs\.lever\.co/([^/?#]+)"),
    "ashby": ("jobs.ashbyhq.com/*", r"jobs\.ashbyhq\.com/([^/?#]+)"),
    "workable": ("apply.workable.com/*", r"apply\.workable\.com/([^/?#]+)"),
    "smartrecruiters": (
        "jobs.smartrecruiters.com/*",
        r"jobs\.smartrecruiters\.com/([^/?#]+)",
    ),
}
_JUNK = {"embed", "job_app", "j", "jobs", "api", "static", "assets"}


def _latest_cdx() -> str:
    cfg = config.active()
    req = urllib.request.Request(
        "https://index.commoncrawl.org/collinfo.json",
        headers={"User-Agent": cfg.user_agent},
    )
    with urllib.request.urlopen(req, timeout=cfg.timeout) as r:
        return json.loads(r.read().decode())[0]["cdx-api"]


def enumerate_tokens(ats: str, max_rows: int = 20000) -> set[str]:
    """One CDX pass -> the set of distinct company slugs on this ATS."""
    if ats not in ATS_PATTERNS:
        raise ValueError(
            f"seed not supported for ATS '{ats}' (try: {', '.join(ATS_PATTERNS)})"
        )
    cfg = config.active()
    pattern, rx = ATS_PATTERNS[ats]
    cdx = _latest_cdx()
    url = f"{cdx}?url={pattern}&output=json&limit={max_rows}"
    req = urllib.request.Request(url, headers={"User-Agent": cfg.user_agent})
    tokens: set[str] = set()
    pat = re.compile(rx)
    with urllib.request.urlopen(req, timeout=cfg.timeout * 4) as r:
        for line in r:  # CDX streams newline-delimited JSON
            try:
                u = json.loads(line).get("url", "")
            except Exception:
                continue
            m = pat.search(u.lower())
            if m:
                tok = m.group(1).strip("/")
                if tok and tok not in _JUNK and not tok.startswith("%"):
                    tokens.add(tok)
    return tokens


def seed_universe(
    ats: str, watchlist_path, limit: int = 300, verify: bool = False
) -> int:
    """Enumerate slugs from Common Crawl and append the new ones to the watchlist.
    Returns the number added."""
    wl = Path(watchlist_path)
    doc = json.loads(wl.read_text()) if wl.exists() else {"companies": []}
    existing = {
        (c.get("ats"), (c.get("slug") or "").lower()) for c in doc.get("companies", [])
    }

    print(f"  querying Common Crawl for {ats} boards…")
    tokens = enumerate_tokens(ats)
    fresh = [t for t in sorted(tokens) if (ats, t.lower()) not in existing]
    print(f"  found {len(tokens)} slugs, {len(fresh)} new")

    added = []
    fetch = DEPTH_ALL.get(ats)
    for tok in fresh:
        if len(added) >= limit:
            break
        if verify and fetch:
            try:
                if not fetch(tok):
                    continue  # dead / empty board
            except Exception:
                continue
        added.append({"name": tok, "ats": ats, "slug": tok, "industry": "(seeded)"})

    if added:
        doc.setdefault("companies", []).extend(added)
        tmp = wl.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, indent=2) + "\n")
        import os

        os.replace(tmp, wl)
    return len(added)
