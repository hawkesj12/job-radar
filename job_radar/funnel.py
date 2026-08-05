"""The slug-discovery funnel: when a breadth hit's apply URL exposes an ATS slug
for a company not yet on the watchlist, probe it to confirm it's real, then
append it -- so the depth list grows itself over time.

Confirming costs ONE cheap request (sources.LIVENESS), not a full harvest of the
board: this only ever needed to know whether the slug resolves to >=1 open role."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

from . import config
from .dedup import board_entry, entry_key, norm
from .scoring import relevant
from .sources import DEPTH_EXTRA_FIELDS, liveness_for
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
        # board_entry, not ats_from_url: a Workday board needs tenant + host + site,
        # and a 2-tuple cannot carry them. Under the old parser Workday, Rippling and
        # Teamtailor all returned None here, so the funnel silently skipped every
        # candidate on the three ATSs -- including the direct Workday apply links
        # google_jobs already returns.
        entry = board_entry(p.get("url", ""))
        if not entry:
            continue
        key = entry_key(entry)
        if key in known_slugs or key in candidates:
            continue
        candidates[key] = (comp, entry)

    # Slice to the PROBE budget before probing, then run that bounded slice
    # concurrently. Order matters: the budget is what makes concurrency safe here.
    # An earlier decision deliberately kept this serial, on the grounds that
    # parallelizing would multiply load on third-party ATS endpoints -- correct at
    # the time, because the loop then probed EVERY candidate on every run. The
    # probe budget (added since) caps the request COUNT whether they go out one at
    # a time or eight at a time, so concurrency now only buys wall clock and adds
    # no load at all. 150 dead candidates measured at ~60 seconds of serial
    # requests to add zero companies.
    #
    # `max_new_per_run` is applied AFTER, to the live results: it counts successes,
    # so it never bounded the probing (a dead slug never incremented it) and it
    # cannot be used to stop early.
    if dry:  # no probing at all — just report what WOULD be probed
        return [
            {**entry, "name": name, "industry": "(discovered)"}
            for name, entry in list(candidates.values())[: cfg.funnel_max_new_per_run]
        ]

    batch = [
        {**entry, "name": name}
        for name, entry in candidates.values()
        if liveness_for(entry["ats"])
    ][: cfg.funnel_max_probes_per_run]  # deferred, not lost — next scan probes on

    def _probe(c):
        # The extra fields go to the liveness call, and for Workday they are not
        # optional: `live_workday` DEFAULTS host="wd1", site="", so calling it with a
        # bare slug does not raise -- it builds a wrong URL, 404s, and the candidate
        # is discarded as a dead board. A silently wrong probe, not a loud failure.
        extra = {k: c[k] for k in DEPTH_EXTRA_FIELDS.get(c["ats"], ()) if c.get(k)}
        try:
            return c if liveness_for(c["ats"])(c["slug"], **extra) else None
        except NET_ERRORS:
            return None  # dead/unreachable slug (a real bug would still surface)

    with ThreadPoolExecutor(max_workers=min(8, len(batch) or 1)) as ex:
        live = [c for c in ex.map(_probe, batch) if c]

    return [
        {**c, "industry": "(discovered)", "source": "discovered"}
        for c in live[: cfg.funnel_max_new_per_run]
    ]


def append_watchlist(wl_path, new_entries):
    """Append verified new companies. The temp-file + os.replace is atomic on its
    own, so no lock is needed for a single-process CLI (a lock file only risked
    getting stuck after a crash and permanently disabling discovery)."""
    if not new_entries:
        return []
    if wl_path.name.endswith(".example.json"):
        return []  # never mutate a shipped template
    doc = json.loads(wl_path.read_text(encoding="utf-8"))
    # entry_key, not (ats, slug): one Workday tenant can run several sites, and two
    # sites are two boards. The 2-tuple treated them as one and kept only the first.
    existing = {entry_key(c) for c in doc.get("companies", [])}
    fresh = [e for e in new_entries if entry_key(e) not in existing]
    if fresh:
        doc.setdefault("companies", []).extend(fresh)
        atomic_write_text(wl_path, json.dumps(doc, indent=2) + "\n")
    return fresh
