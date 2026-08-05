"""The machine feed. Untested until now, which is how it came to emit three key
names nothing in the package produces.

These are SEAM tests. Every module here has unit tests and passes them; the
expensive bugs in this repo's history live between modules -- 0.5.1 (`_consume`
correct but outside `harvest`'s try blocks), 0.5.2 (`upsert` and `mark_status` each
correct, interleaved wrong), 0.5.3 (the CLI wiring two correct functions together
wrongly), and this one (`engine` correct, `emit` correct in isolation, the seam
between them carrying names from before a rename).
"""

from __future__ import annotations

import inspect
import json
import re

from job_radar import emit, engine, shortlist


def _keys_read_by_nested() -> set[str]:
    src = inspect.getsource(emit._nested)
    return set(re.findall(r'r\.get\("([a-z_]+)"\)', src)) | set(
        re.findall(r'r\["([a-z_]+)"\]', src)
    )


def test_emit_never_reads_a_key_nothing_produces():
    """THE DRIFT KILLER, and worth more than any single-bug fix here: it makes this
    class of defect structurally unrepresentable rather than merely fixed.

    `emit._nested` is an uncontrolled hand-written copy of the field list. When
    0.7.0 renamed function -> category, org_unit -> team and employer_org ->
    parent_company, the rename reached the contract and all nineteen adapters but not
    this function, so the machine feed emitted `employer_org: null` forever while
    `parent_company` -- which the adapters fill -- was never emitted at all. Nothing
    failed. The feed looked structurally perfect and was null where it mattered.

    A key `_nested` reads must come from somewhere real: the contract, a store
    column, or the short allowlist below of things the pipeline attaches later.
    """
    produced = set(engine._CONTRACT_FIELDS) | set(shortlist.COLUMNS)
    # Attached after _coerce rather than by it: `sources` is merged during dedup,
    # `text` is grafted by cli._emit_ndjson (too big for the CSV, not structure so
    # not a contract field), and the rest are store/scoring bookkeeping.
    attached = {"sources", "text", "score", "signals", "id", "dedup_key"}
    orphans = _keys_read_by_nested() - produced - attached
    assert not orphans, (
        f"emit._nested reads {sorted(orphans)}, which nothing in the package sets. "
        "Every row will carry null there. Renamed a contract field and missed emit?"
    )


def test_the_machine_feed_carries_the_whole_contract():
    """The inverse direction, and the one that caught the bigger half: 23 of 29
    contract fields were never emitted at all. Every salary field, every basis,
    remote_type, the title decomposition and `locations` existed in the record and
    stopped at the wire, which made `--format ndjson` a feed that advertised the
    0.7.0 contract and shipped six fields of it."""
    missing = set(engine._CONTRACT_FIELDS) - _keys_read_by_nested()
    assert not missing, (
        f"the contract promises {sorted(missing)} and NDJSON does not emit them"
    )


def test_a_real_row_survives_the_whole_pipeline_to_json():
    """End to end on the values, not just the key names: a harvested posting, coerced
    at the real boundary, emitted, and read back as JSON the way a consumer would."""
    row = engine._coerce(
        {
            "title": "Senior AI Engineer II, Platform",
            "company": "Acme",
            "url": "https://boards.greenhouse.io/acme/jobs/1",
            "location": "Louisville, KY",
            "text": "Build agentic LLM systems.",
            "source": "greenhouse",
            "salary_min": 180000.0,
            "salary_max": 220000.0,
            "salary_currency": "USD",
            "salary_period": "year",
            "salary_basis": "stated",
            "remote_type": "hybrid",
            "remote_basis": "stated",
        }
    )
    row["text"] = "Build agentic LLM systems."
    got = json.loads(emit.records([row]))

    assert got["title"]["raw"] == "Senior AI Engineer II, Platform"
    assert got["title"]["root"] == "AI Engineer", "title decomposition never shipped"
    assert got["title"]["level"] == "II"
    assert got["salary"]["min"] == 180000.0
    assert got["salary"]["period"] == "year"
    assert got["salary"]["basis"] == "stated"
    # hybrid is NOT remote, and the feed must be able to say so distinctly from
    # "nobody told us" -- the whole reason the contract separates None from False.
    assert got["remote"]["type"] == "hybrid"
    assert got["remote"]["is_remote"] is False
    assert got["text"], "the body is the entire input to the fit score"


def test_unknown_stays_null_and_never_becomes_a_plausible_value():
    """The contract's load-bearing rule, asserted on the wire. A source that said
    nothing must emit null -- not 0, not "", not False. A consumer writing
    `WHERE remote IS NOT NULL` depends on this being true of the bytes."""
    row = engine._coerce(
        {
            "title": "Engineer",
            "company": "Acme",
            "url": "https://example.com/1",
            "source": "greenhouse",
        }
    )
    got = json.loads(emit.records([row]))
    assert got["salary"]["min"] is None and got["salary"]["basis"] is None
    assert got["remote"]["type"] is None and got["remote"]["basis"] is None
    assert got["city"] is None if "city" in got else True
    assert got["location"]["city"] is None
    # A zero salary is the same lie as remote=False for unknown. RemoteOK sends
    # exactly this on all 100 rows of its feed.
    assert got["salary"]["min"] != 0


def test_records_emits_one_valid_json_object_per_line():
    rows = [
        engine._coerce(
            {
                "title": f"Engineer {i}",
                "company": "Acme",
                "url": f"https://example.com/{i}",
                "source": "greenhouse",
            }
        )
        for i in range(3)
    ]
    text = emit.records(rows)
    lines = text.split("\n")
    assert len(lines) == 3 and not text.endswith("\n")
    for ln in lines:
        json.loads(ln)
