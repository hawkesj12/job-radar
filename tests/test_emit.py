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

from job_radar import config, emit, engine, shortlist


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


def test_an_empty_sections_list_survives_the_wire_as_an_empty_list():
    """`or None` on the sections line of `emit._nested` collapses `[]` into `null` and
    destroys the two-state contract: `[]` means we read the body and it had no headers,
    `null` means there was no body. A consumer cannot tell those apart afterwards.

    This assertion exists because that mutation passed the ENTIRE suite. The six-line
    comment on that line was the only thing protecting it, and a comment is not a gate.
    Every other `or None` in `_nested` is on a str-typed field, where "" and None do
    mean the same thing; `sections` follows `remote_areas`, which omits it.
    """
    assert emit._nested({"sections": []})["sections"] == []
    assert emit._nested({})["sections"] is None
    assert emit._nested({"sections": [{"type": None, "header": "x"}]})["sections"] == [
        {"type": None, "header": "x"}
    ]


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
    assert "is_remote" not in got["remote"], "the bool was removed at 0.9.0"
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


def test_a_merge_record_survives_every_shape_sources_arrives_in():
    """`sources` reached the wire through two exits that disagreed about its shape.

    `_nested` accepted only a `set`, so the SAME value after a json round trip -- a
    list -- emitted `null`, silently destroying the record that a cross-source dedup
    merge ever happened. `manifest` accepted set and list, then fell back to
    `[r["source"]]`, so a store row (which has no `sources` column at all) counted one
    row under a source literally named "adzuna, greenhouse".

    Dedup is where a mistake deletes a job, and these 35 multi-source rows in a
    7,568-row local harvest are the only record a merge happened anywhere.
    """
    base = {
        "title": "AI Engineer",
        "company": "Acme",
        "url": "https://x/1",
        "dedup_key": "acme|ai engineer|us",
    }
    want = ["adzuna", "greenhouse"]
    for shape in ({"adzuna", "greenhouse"}, ["greenhouse", "adzuna"], ("adzuna", "greenhouse")):
        row = {**base, "source": "greenhouse", "sources": shape}
        assert emit._nested(row)["sources"] == want, f"lost the merge from a {type(shape).__name__}"

    # THE STORE ROW: no `sources` key, the merge folded into the singular `source`.
    stored = {**base, "source": "adzuna, greenhouse"}
    got = emit._nested(stored)
    assert got["sources"] == want, "the store's joined source did not round-trip back"
    assert got["source"] == "adzuna", "`source` must be a real adapter token, never the joined string"

    counts = json.loads(emit.manifest([stored], [], [], config.Config()))["rows_by_source"]
    assert counts == {"adzuna": 1, "greenhouse": 1}, (
        f"manifest counted {sorted(counts)} -- a fabricated source name is not a source"
    )


def test_a_single_source_row_is_untouched_by_the_merge_recovery():
    """The split is on `", "`, and no registered source token contains a comma or a
    space (19 of 19), so it has exactly one inverse and cannot fire on a normal row."""
    row = {"title": "T", "company": "C", "url": "https://x/1", "source": "google_jobs"}
    got = emit._nested(row)
    assert got["source"] == "google_jobs" and got["sources"] == ["google_jobs"]
    assert emit._nested({**row, "source": ""})["source"] is None


def test_no_text_drops_the_body_keys_rather_than_nulling_them():
    """`text` is 72% of the median record's bytes and there was no way to ask for the
    record without it, which is why the harvest output was unreadable by hand.

    DROPPED, not nulled: `text: null` already means "the source sent no body"
    (smartrecruiters, 250 of 250 rows), so nulling it here would make a caller's
    display choice indistinguishable from a fact about the posting.
    """
    row = {
        "title": "AI Engineer", "company": "Acme", "url": "https://x/1",
        "source": "greenhouse", "text": "a long body", "text_basis": "excerpt",
    }  # fmt: skip
    full = json.loads(emit.records([row]))
    assert full["text"] == "a long body" and full["text_basis"] == "excerpt"

    lean = json.loads(emit.records([row], include_text=False))
    assert "text" not in lean and "text_basis" not in lean, (
        "the body keys must be ABSENT, not null -- null already means something else"
    )
    # Nothing else moves. The flag is a display choice, not a contract change.
    assert set(full) - set(lean) == {"text", "text_basis"}
    assert all(lean[k] == full[k] for k in lean)


def test_a_source_that_sent_no_body_still_says_so_with_null():
    """The other side of the same distinction: with the body INCLUDED, a source that
    sent nothing must still be able to say `null`, or the two states collapse."""
    row = {"title": "T", "company": "C", "url": "https://x/1", "source": "smartrecruiters"}
    got = json.loads(emit.records([row]))
    assert "text" in got and got["text"] is None
