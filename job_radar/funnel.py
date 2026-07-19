"""The slug-discovery funnel: when a breadth hit's apply URL exposes an ATS slug
for a company not yet on the watchlist, probe it to confirm it's real, then
append it -- so the depth list grows itself over time."""

from __future__ import annotations

import json

from . import config
from .dedup import ats_from_url, norm
from .scoring import relevant
from .sources import DEPTH_ALL
from .util import NET_ERRORS, atomic_write_text


def funnel(breadth_postings, known_companies, known_slugs, cfg=None, dry=False):
    cfg = cfg or config.active()
    candidates = {}
    for p in breadth_postings:
        comp = p.get("company", "")
        if not comp or norm(comp) in known_companies:
            continue
        if not relevant(p.get("title", ""), cfg):
            continue
        got = ats_from_url(p.get("url", ""))
        if not got:
            continue
        key = (got[0], got[1].lower())
        if key in known_slugs or key in candidates:
            continue
        candidates[key] = comp

    added = []
    for (ats, slug), name in candidates.items():
        if len(added) >= cfg.funnel_max_new_per_run:
            break
        if dry:
            added.append(
                {"name": name, "ats": ats, "slug": slug, "industry": "(discovered)"}
            )
            continue
        fetch = DEPTH_ALL.get(ats)
        if not fetch:
            continue
        try:
            ps = fetch(slug)
        except NET_ERRORS:
            continue  # dead/unreachable slug — skip (a real bug would surface)
        if ps:  # >=1 posting -> the slug is real
            added.append(
                {
                    "name": name,
                    "ats": ats,
                    "slug": slug,
                    "industry": "(discovered)",
                    "source": "discovered",
                }
            )
    return added


def append_watchlist(wl_path, new_entries):
    """Append verified new companies. The temp-file + os.replace is atomic on its
    own, so no lock is needed for a single-process CLI (a lock file only risked
    getting stuck after a crash and permanently disabling discovery)."""
    if not new_entries:
        return []
    if wl_path.name.endswith(".example.json"):
        return []  # never mutate a shipped template
    doc = json.loads(wl_path.read_text(encoding="utf-8"))
    existing = {
        (c.get("ats"), (c.get("slug") or "").lower()) for c in doc.get("companies", [])
    }
    fresh = [e for e in new_entries if (e["ats"], e["slug"].lower()) not in existing]
    if fresh:
        doc.setdefault("companies", []).extend(fresh)
        atomic_write_text(wl_path, json.dumps(doc, indent=2) + "\n")
    return fresh
