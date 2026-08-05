"""Structured output: the machine-facing half of the CLI.

`shortlist.csv` is the HUMAN artifact -- one inspectable file you can open in a
spreadsheet, sorted by score, with the columns a person reads. It is deliberately
lossy: nineteen flat columns, every value escaped against spreadsheet formula
injection, and no way to represent a list, a boolean, or the difference between
"unknown" and "empty".

That last part is why a second format exists rather than more CSV columns. The
0.7.0 record contract turns on `None` meaning "the source did not say", distinct
from `False` and from `""` -- and CSV cannot carry that distinction at all. A
consumer loading `remote` out of a CSV sees the empty string for both "not remote"
and "nobody knows", which is precisely the ambiguity the contract removes.

So: NDJSON (newline-delimited JSON, one object per line). It streams, it appends,
it types, every database and dataframe library reads it natively, and a partial
file is still valid up to the last complete line.

Two shapes are emitted:

  * `records()`  -- one object per role. `location` is nested; `salary` is NOT --
    it is still the display string the adapters produce. An earlier version of this
    line said "location and salary are objects", which was true of neither the code
    below nor any release. Structuring salary is real work (currency, period, and
    whether anyone actually stated it) and is not done yet.
  * `manifest()` -- ONE object per harvest describing the run itself

The manifest matters more than it looks. A store fed only rows cannot answer "why
did Tuesday have four hundred fewer jobs" -- was a source down, was a key missing,
did someone change the config? That information exists today only as text printed
to a terminal and then lost. Emitting it as data makes a harvest auditable after
the fact.
"""

from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")  # every timestamp in job-radar is Eastern


def _nested(r: dict) -> dict:
    """One harvested posting -> the emitted record.

    Nests `location`, and keeps each derived `*_basis` next to the value it explains
    so a consumer that disagrees with our inference can override it rather than
    re-deriving everything.

    NOTE the shape split, because two docs in this repo describe it differently and
    only one of them is the wire format: `engine.harvest()` returns a FLAT dict (that
    is what a library consumer like jobfitr receives, and what engine._coerce enforces
    key-by-key). This function is the only place that nests anything, and it applies
    only to NDJSON. If those two ever need to agree, this mapping is the one place to
    change.

    `department` rides along unchanged. It is deprecated, not gone: jobfitr pins a
    released version and reads it today, so removing it here would break a consumer
    at a minor version. It goes at 1.0.
    """
    return {
        "id": r.get("id") or None,
        "dedup_key": r.get("dedup_key") or None,
        "title": r.get("title") or None,
        "company": r.get("company") or None,
        "employer_org": r.get("employer_org"),
        "function": r.get("function"),
        "org_unit": r.get("org_unit"),
        "seniority": r.get("seniority"),
        "tags": r.get("tags"),
        "location": {
            "raw": r.get("location") or None,
            "city": r.get("city"),
            "state": r.get("state"),
            "country": r.get("country"),
        },
        "remote": r.get("remote"),
        "remote_basis": r.get("remote_basis"),
        "posted": r.get("posted") or None,
        "employment_type": r.get("employment_type") or None,
        "salary": r.get("salary") or None,
        "url": r.get("url") or None,
        "text": r.get("text") or None,
        "source": r.get("source") or None,
        "sources": sorted(r["sources"]) if isinstance(r.get("sources"), set) else None,
        "score": r.get("score"),
        "signals": r.get("signals") or None,
        # DEPRECATED -- see the docstring. Preserved byte-identical.
        "department": r.get("department") or None,
    }


def records(rows) -> str:
    """Rows -> NDJSON text. One JSON object per line, no trailing blank line."""
    return "\n".join(json.dumps(_nested(r), ensure_ascii=False) for r in rows)


def manifest(rows, errors, discovered, cfg, started_at=None) -> str:
    """One JSON object describing the RUN, not its rows.

    `sources_ok` / `sources_failed` are what let a consumer distinguish "the market
    was quiet" from "four adapters were down", which is invisible from row counts
    alone.
    """
    by_source: dict[str, int] = {}
    for r in rows:
        for s in r.get("sources") or ([r["source"]] if r.get("source") else []):
            by_source[s] = by_source.get(s, 0) + 1
    return json.dumps(
        {
            "kind": "job-radar.manifest",
            "finished_at": datetime.now(_ET).strftime("%Y-%m-%dT%H:%M:%S"),
            "started_at": started_at,
            "rows": len(rows),
            "rows_by_source": dict(sorted(by_source.items())),
            "sources_failed": len(errors),
            "errors": list(errors),
            "companies_discovered": len(discovered or ()),
            "config": {
                "remote_only": cfg.remote_only,
                "location": cfg.location,
                "max_age_days": cfg.max_age_days,
                "min_score": cfg.min_score,
                "title_queries": list(cfg.title_queries),
            },
        },
        ensure_ascii=False,
    )
