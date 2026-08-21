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

  * `records()`  -- one object per role. `title`, `location`, `remote` and `salary`
    are nested objects, each keeping its `raw` vendor value beside the parsed parts,
    so a consumer that disagrees with our parse can re-read the original rather than
    losing it. (Two earlier versions of this line were wrong in opposite directions --
    first claiming salary was an object when it was a bare string, then claiming it
    was "not done yet" after the parsing landed. It is done: currency, period, and a
    basis saying whether anyone actually stated the figure.)
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

from . import attribution

_ET = ZoneInfo("America/New_York")  # every timestamp in job-radar is Eastern


def _sources(r: dict) -> list[str] | None:
    """Every adapter that produced this row, from whatever SHAPE the caller holds.

    ONE reader for a field that arrives in three shapes and was being read two
    different ways at two different exits, which is how they came to disagree:

      * a `set` -- what `engine._merge` builds and what a live harvest row carries
      * a `list` -- the same value after ANY json round trip, and what a caller that
        loaded our own NDJSON back hands us
      * absent, with the singular `source` holding `", ".join(sorted(tokens))` --
        what a STORE row carries, because `shortlist.COLUMNS` has no `sources`
        column at all and `_build_row` folds the merge into `source`

    `_nested` read only the first (`isinstance(..., set)`), so a list-valued
    `sources` emitted `null` -- the merge record silently destroyed by a round trip.
    `manifest` read the first two and then fell back to `[r["source"]]`, so a store
    row counted one row under a FABRICATED source named `"adzuna, greenhouse"`, a
    string no adapter has ever been called. Same field, two exits, two failures.

    Splitting the store's joined string back is lossless for the SET, and provably
    so rather than by inspection: no registered source token contains a comma or a
    space (19 of 19, `sources.DEPTH_ALL | BREADTH_ALL`), so `", ".join` has exactly
    one inverse. What it does NOT recover is WHICH source won the merge -- the store
    never wrote that down, so the singular `source` becomes a REPRESENTATIVE rather
    than a winner. See `_nested`'s `source` key.

    THREE call sites read this, and all three are exits where the field lost its shape:
    `_nested`, `manifest`, and `cli`'s attribution credit line. The third was missed
    when the first two were fixed.
    """
    got = r.get("sources")
    if isinstance(got, (set, frozenset, list, tuple)):
        return sorted(str(s) for s in got if s)
    one = r.get("source")
    if not one:
        return None
    return sorted(t for t in str(one).split(", ") if t)


def _nested(r: dict, include_text: bool = True, omit_empty: bool = False) -> dict:
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

    `department` is GONE as of 0.9.0. Its org unit is `team`; see the record contract.
    """
    srcs = _sources(r)
    out = {
        "id": r.get("id") or None,
        "dedup_key": r.get("dedup_key") or None,
        "title": {
            "raw": r.get("title") or None,
            "root": r.get("title_root"),
            "level": r.get("title_level"),
            "qualifiers": r.get("title_qualifiers"),
        },
        "company": r.get("company") or None,
        "category": r.get("category"),
        "team": r.get("team"),
        "seniority": r.get("seniority"),
        "seniority_raw": r.get("seniority_raw"),
        "seniority_basis": r.get("seniority_basis"),
        "tags": r.get("tags"),
        "location": {
            "raw": r.get("location") or None,
            "city": r.get("city"),
            "state": r.get("state"),
            "country": r.get("country"),
            "all": r.get("locations"),
        },
        "remote": {
            # NO `is_remote`. A bool sat here until 0.9.0 and was exactly
            # `type == "remote"` with None preserved, so it was a second home for one
            # fact -- and the weaker home, since it cannot say `hybrid` and reported
            # 1,679 hybrid rows as `false`. Read `type`; it answers more precisely.
            "type": r.get("remote_type"),
            # WHERE a remote worker may sit. `areas` are ISO codes, `regions` are
            # multi-country tokens, and they are separate because they are different kinds
            # of value -- one key holding both is what this replaced. `areas: []` means the
            # posting STATED it is unbounded; `null` means it said nothing. JSON keeps that
            # distinction for free, which a CSV column could not.
            "areas": r.get("remote_areas"),
            "regions": r.get("remote_regions"),
            "raw": r.get("remote_scope_raw"),
            "basis": r.get("remote_basis"),
        },
        "posted": r.get("posted") or None,
        "posted_basis": r.get("posted_basis"),
        "expires": r.get("expires"),
        "harvested_at": r.get("harvested_at"),
        "employment_type": r.get("employment_type") or None,
        "employment_type_raw": r.get("employment_type_raw"),
        # WHETHER A PERSON CAN APPLY AT ALL. Grouped rather than flat because each
        # carries a basis, and a consumer that reads the state without the basis
        # cannot tell a sentence-scoped answer from a section-scoped one. `None`
        # throughout means the posting did not say -- 94% of rows for both.
        "sponsorship": {
            "state": r.get("sponsorship"),
            "basis": r.get("sponsorship_basis"),
        },
        "clearance": {
            "state": r.get("clearance"),
            "basis": r.get("clearance_basis"),
        },
        "salary": {
            "raw": r.get("salary") or None,
            "min": r.get("salary_min"),
            "max": r.get("salary_max"),
            "currency": r.get("salary_currency"),
            "period": r.get("salary_period"),
            "basis": r.get("salary_basis"),
            # WHAT the figure measures, beside HOW it was extracted. A consumer
            # filtering for base pay needs both: `basis` says we read it correctly,
            # `kind` says it is not an equity grant.
            "kind": r.get("salary_kind"),
            # KEPT APART from min/max on purpose. Adzuna predicts a salary with a
            # model on 93% of its rows; merging those into the same keys would make
            # a guess indistinguishable from a figure an employer committed to.
        },
        "url": r.get("url") or None,
        "direct_apply": r.get("direct_apply"),
        "text": r.get("text") or None,
        # Sits beside `text` on purpose: a consumer reading the body needs to know in
        # the same breath whether the SOURCE truncated it (`excerpt`) or whether we
        # built it out of structured fields (`synthesized`). `None` means nobody
        # characterized it -- there is no `full`, see vocab.TEXT_BASES.
        "text_basis": r.get("text_basis"),
        # NO `or None`, and that is not an oversight. `[] or None` is `None`, which
        # would collapse "we looked and the body had no headers" into "there was no
        # body" -- the exact two-state distinction this field exists to carry. Every
        # `or None` above is on a str-typed field where "" and None mean the same
        # thing; every list- or dict-typed field here already omits it, and `sections`
        # follows `remote_areas` rather than the line above it.
        "sections": r.get("sections"),
        # A REAL ADAPTER TOKEN OR NOTHING. A store row folds a merge into this field
        # as `", ".join(sorted(tokens))`, so a two-source row arrived here as the
        # string "adzuna, greenhouse" -- which is not a source, resolves against no
        # entry in `attribution`, and matches nothing a consumer can key on. The
        # store never recorded WHICH source won the merge, so this is a REPRESENTATIVE,
        # not the winner -- the first of the sorted tokens, chosen because it is
        # deterministic. That distinction is the whole honesty of the field: on a
        # merged row both adapters really did produce it, so one real token is
        # lossy-but-true rather than a fabricated winner. `sources` beside it carries
        # the whole set, and attribution is discharged off THAT, never off this key.
        # A single-source row is unaffected.
        "source": srcs[0] if srcs else None,
        "sources": srcs,
        "source_extra": r.get("source_extra"),
        "score": r.get("score"),
        "signals": r.get("signals") or None,
    }
    if not include_text:
        # DROPPED, not nulled. `text: null` is already taken: it means the source sent
        # no body (smartrecruiters, 250 of 250 rows), and collapsing "you asked us not
        # to send it" into that is the same two-states-in-one-value lie the contract
        # exists to remove. An absent key is the only value JSON has left.
        del out["text"], out["text_basis"]
    if omit_empty:
        out = _prune(out)
    return out


def _prune(o):
    """Drop None and "" keys, recursively, from the nested record.

    RECURSIVE because this shape nests: `location`, `remote`, `salary` and `title` are
    objects, and pruning only the top level would leave `salary: {raw: null, min: null,
    …}` -- eight nulls in a wrapper, which is most of what makes an empty record long.

    `[]` AND `{}` SURVIVE, same rule as `engine._shape`. `remote.areas: []` means the
    posting STATED it is open anywhere and `sections: []` means we read the body and
    found no headers; both are facts that took work to establish and neither is `null`,
    which means nobody said. Only None and "" go -- they already mean "nothing here".
    An object that prunes to empty is KEPT as `{}` rather than dropped, so a consumer
    reading `record["salary"]["min"]` gets None instead of a KeyError on the wrapper.
    """
    if isinstance(o, dict):
        return {k: _prune(v) for k, v in o.items() if v is not None and v != ""}
    if isinstance(o, list):
        return [_prune(v) for v in o]
    return o


def records(rows, include_text: bool = True, omit_empty: bool = False) -> str:
    """Rows -> NDJSON text. One JSON object per line, no trailing blank line.

    `include_text=False` DROPS the `text`/`text_basis` keys rather than nulling them,
    because `text: null` already means something else here -- the source sent no body
    at all (smartrecruiters, 250 of 250) -- and a reader cannot tell that from "the
    caller asked us not to send it". An absent key is the only honest way to say the
    second thing. `text_basis` leaves with it: it characterizes a body that is no
    longer in the record.
    """
    return "\n".join(
        json.dumps(
            _nested(r, include_text=include_text, omit_empty=omit_empty),
            ensure_ascii=False,
        )
        for r in rows
    )


def manifest(rows, errors, discovered, cfg, started_at=None) -> str:
    """One JSON object describing the RUN, not its rows.

    `sources_ok` / `sources_failed` are what let a consumer distinguish "the market
    was quiet" from "four adapters were down", which is invisible from row counts
    alone.
    """
    by_source: dict[str, int] = {}
    for r in rows:
        # `_sources`, not a local fallback to `[r["source"]]`. That fallback counted a
        # store row's joined `source` as one source literally named
        # "adzuna, greenhouse" -- a name no adapter has, in the one output a consumer
        # reads to tell "the market was quiet" from "four adapters were down".
        for s in _sources(r) or ():
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
            # THE OBLIGATION TRAVELS WITH THE DATA. Four of these sources grant API
            # access on the stated condition that you link back and name them, and
            # two say outright they will revoke access if you do not. A library
            # cannot discharge a DISPLAY obligation on behalf of whatever displays
            # the jobs, so it hands the terms to the consumer that can -- keyed by
            # the same `source` value on every row. See job_radar/attribution.py.
            "attribution": attribution.as_dicts(by_source),
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
