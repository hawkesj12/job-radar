"""Core tests: config, deterministic scoring, dedup, the upsert store, and the
LLM no-op guarantee. Run: pytest"""

import json
import os
import time
import types
import urllib.error
from dataclasses import replace
from pathlib import Path

import pytest

from job_radar import (
    cli,
    config,
    dedup,
    engine,
    funnel,
    llm,
    scoring,
    sections,
    seed,
    sources,
    shortlist,
    util,
    vocab,
)


def _cfg():
    c = config.Config()
    config.set_active(c)
    return c


# ── config ──────────────────────────────────────────────────────────────────
def test_config_defaults():
    c = config.load_config(None)
    assert c.max_age_days == 60 and c.min_score == 22
    assert c.llm.enabled is False


def test_to_date_converts_an_instant_to_eastern_and_leaves_a_calendar_day_alone():
    """The epoch branch converted to ET; the string branch three lines later did
    `str(val)[:10]`, keeping the VENDOR's calendar day. Measured against live vendor
    APIs on 3,732 corpus rows: ashby 16.3% of dates wrong, hn 10.5%, greenhouse 0.00%,
    lever 0.00%. Always one day too NEW, so the role dodged the staleness penalty too.

    The guard is the point: a date-only or zone-less string is NOT an instant and must
    not shift, or this fix moves the 68% of the corpus that is correct today."""
    # an instant: 02:00 UTC is the previous day in Eastern
    assert util.to_date("2026-05-23T02:00:50.464+00:00") == "2026-05-22"
    assert util.to_date("2026-08-21T02:17:57Z") == "2026-08-20"
    # NOT instants -- no offset, so no timezone to convert from
    assert util.to_date("2026-05-22") == "2026-05-22"
    assert util.to_date("2026-05-22T14:00:00") == "2026-05-22"
    # already Eastern: same day in, same day out
    assert util.to_date("2026-05-22T14:00:00-04:00") == "2026-05-22"
    # `.astimezone()` raises OverflowError, NOT ValueError, at the edge of the
    # datetime range -- and "0001-01-01T00:00:00Z" is in catalog/_raw/4dayweek.json.
    # An uncaught raise here takes out a whole harvest.
    assert util.to_date("0001-01-01T00:00:00Z") == "0001-01-01"
    # unchanged behaviour
    assert util.to_date(1785719389) == "2026-08-02"
    assert util.to_date("not a date") == ""
    assert util.to_date("") == ""


def test_default_config_enables_every_registered_adapter():
    """The defaults are 'whatever sources.py registers', not a second hand-kept list.
    Asserting on the RESOLVED set (not the raw field) is what makes a newly added
    fetcher enabled by construction — the old copy in config.py silently disabled one."""
    c = config.load_config(None)
    assert c.depth_sources is None and c.breadth_sources is None
    assert set(sources.enabled_depth(c)) == set(sources.DEPTH_ALL)
    assert {k for k, _ in sources.enabled_breadth(c)} == set(sources.BREADTH_ALL)
    assert "workday" in sources.enabled_depth(c)  # the adapter the old copy dropped


def test_an_explicit_subset_still_narrows():
    c = config.load_config(None)
    c.depth_sources = ["greenhouse"]
    c.breadth_sources = ["adzuna", "nonexistent-source"]
    assert set(sources.enabled_depth(c)) == {"greenhouse"}
    assert {k for k, _ in sources.enabled_breadth(c)} == {"adzuna"}


def test_config_override(tmp_path):
    p = tmp_path / "cfg.yaml"
    p.write_text("filters:\n  max_age_days: 14\n  min_score: 40\n")
    c = config.load_config(p)
    assert c.max_age_days == 14 and c.min_score == 40
    assert c.fit_weights  # untouched sections keep defaults


def test_google_jobs_config_block_is_actually_read(tmp_path):
    """A documented knob that no loader reads is worse than an undocumented one.
    The shipped example config advertises sources.google_jobs.{key_env,pages}."""
    p = tmp_path / "cfg.yaml"
    p.write_text("sources:\n  google_jobs:\n    key_env: MY_SERP_KEY\n    pages: 3\n")
    c = config.load_config(p)
    assert c.serpapi_key_env == "MY_SERP_KEY"
    assert c.google_jobs_pages == 3


def test_new_scoring_and_funnel_knobs_are_actually_read(tmp_path):
    """Both knobs added for the v0.5.0 blockers are documented in the shipped example
    config, and a documented knob with no loader is worse than an undocumented one —
    it reads as a supported setting and silently does nothing. `score_k1` shipped
    exactly that way for the length of one commit."""
    p = tmp_path / "cfg.yaml"
    p.write_text(
        "scoring:\n  score_k1: 2.5\nsources:\n  funnel:\n    max_probes_per_run: 7\n",
        encoding="utf-8",
    )
    c = config.load_config(p)
    assert c.score_k1 == 2.5
    assert c.funnel_max_probes_per_run == 7


def test_shipped_example_config_enables_every_adapter(tmp_path):
    """`job-radar init` writes this file verbatim, and an explicit list is a SUBSET
    filter — so anything missing here is silently off for every new user. Workday
    was omitted from `ats` for three releases while the README called it the
    enterprise tier.

    Reads the PACKAGED copy, via the same `cli._packaged` path `init` itself uses.
    The first version of this test read the repo-root copy instead — so deleting
    `workday` from the file that actually ships left the suite green, and the guard
    protected a file no user ever receives."""
    p = tmp_path / "shipped.yaml"
    p.write_text(cli._packaged("job-radar.example.yaml"), encoding="utf-8")
    c = config.load_config(p)
    assert set(sources.DEPTH_ALL) == set(c.depth_sources)
    assert set(sources.BREADTH_ALL) == set(c.breadth_sources)


def test_shipped_example_config_does_not_weaken_the_non_us_filter(tmp_path):
    """The example SET `exclude_locations` to six names, and a YAML list REPLACES the
    default rather than extending it -- so `job-radar init` handed every new user a
    filter weaker than the built-in one, which is the opposite of an example's job.
    The key is commented out; this fails if it is ever set to a strict subset again."""
    p = tmp_path / "shipped.yaml"
    p.write_text(cli._packaged("job-radar.example.yaml"), encoding="utf-8")
    c = config.load_config(p)
    assert set(c.exclude_locations) >= set(config.DEFAULT_NON_US)


def test_the_non_us_filter_covers_every_non_us_country_in_the_vocabulary():
    """The invariant that keeps two country lists from drifting apart again.

    `DEFAULT_NON_US` was 34 hand-written names against vocab's 60, and the 26 it lacked
    leaked 260 remote rows bounded to countries the user cannot work in. It now derives
    from the vocabulary, and this fails the moment a country is added to the map without
    reaching the filter."""
    missing = {
        name
        for name, code in vocab._COUNTRY_CODES.items()
        if code != "US" and name not in set(config.DEFAULT_NON_US)
    }
    assert not missing, missing


def test_non_us_exclusion_matches_whole_words_not_substrings():
    """`x in blob` dropped 202 rows by collision on a 31,790-row harvest, and the two
    worst were US employers: "india" matches indianA and "apac" matches cAPACity."""
    c = config.Config(remote_only=True)
    assert (
        scoring.is_remote({"title": "AI Engineer", "location": "Remote - India"}, c)
        is False
    )
    # ...but Indiana is in the United States, and so is a Capacity Planning role.
    assert (
        scoring.is_remote(
            {"title": "AI Engineer", "location": "Indianapolis, IN (Remote)"}, c
        )
        is True
    )
    assert (
        scoring.is_remote(
            {"title": "Capacity Planning Engineer", "location": "Austin, TX (Remote)"},
            c,
        )
        is True
    )
    # "European Union" must still be caught -- word boundaries make it unreachable from
    # the token "europe" alone, which is why `european` is listed beside it.
    assert (
        scoring.is_remote(
            {"title": "AI Engineer", "location": "European Union (Remote)"}, c
        )
        is False
    )


def test_a_us_marker_in_the_location_vetoes_a_non_us_exclusion():
    """A posting may list several eligible countries. Dropping it on the foreign token
    alone loses a job the user can actually take: "Remote (United States | Canada)" (32
    rows), "Americas (USA or Canada)" (16) and "Remote - US & Canada" (7) were all
    discarded on `canada`. 339 rows in one harvest. The veto reads the LOCATION only --
    titles carry "US" incidentally ("US client"), which would rescue foreign rows."""
    c = config.Config(remote_only=True)
    for loc in (
        "Remote (United States | Canada)",
        "Americas (USA or Canada) (Remote)",
        "Remote - US & Canada",
    ):
        assert (
            scoring.is_remote({"title": "AI Engineer", "location": loc}, c) is True
        ), loc
    # Canada ALONE is still excluded -- the veto needs a real US marker in the location.
    assert (
        scoring.is_remote({"title": "AI Engineer", "location": "Remote - Canada"}, c)
        is False
    )
    assert (
        scoring.is_remote(
            {"title": "AI Engineer for US client", "location": "Remote - Canada"}, c
        )
        is False
    )


# The repo-root copies of the example files are GONE, and so are the two tests that
# pinned them byte-equal to the packaged ones. There is now exactly one copy of each
# — job_radar/data/, the copy the wheel ships and `init` writes — so there is nothing
# left to drift. The equality test was the right guard for the wrong shape: it kept
# two writable copies of one file agreeing, when deleting the second copy removes the
# failure mode outright. `test_shipped_example_config_enables_every_adapter` above
# already reads the packaged copy, which is the one that matters.
#
# Deleted with them: test_the_ai_config_prompt_lists_every_adapter, which guarded a
# THIRD copy of the adapter lists inside prompts/build-config-with-ai.md (it drifted
# 19 days behind and shipped `workday`/`google_jobs`/`usajobs` switched off). That
# prompt is retired, so the copy it guarded no longer exists either.


# ── deterministic scoring + gates ────────────────────────────────────────────
def test_score_is_deterministic_and_discriminates():
    c = _cfg()
    ai = {
        "title": "AI Engineer",
        "location": "Remote",
        "text": "Build RAG and agentic LLM systems.",
        "company": "Acme",
    }
    junk = {
        "title": "Office Manager",
        "location": "Remote",
        "text": "Manage the office.",
        "company": "Acme",
    }
    assert scoring.score(ai, c) == scoring.score(ai, c)  # same input, same output
    assert scoring.score(ai, c) > scoring.score(junk, c)


def test_relevance_and_remote_gates():
    c = _cfg()
    assert scoring.relevant("AI Engineer", c) is True
    assert scoring.relevant("Warehouse Associate", c) is False  # no signal title
    assert scoring.is_remote({"title": "AI Engineer", "location": "Remote"}, c) is True
    assert (
        scoring.is_remote({"title": "AI Engineer", "location": "New York, onsite"}, c)
        is False
    )


def test_remote_posting_reads_body():
    # remote stated only in the body, title/location silent -> caught
    assert scoring.remote_posting(
        "Engineer", "United States", "This is a fully remote position."
    )
    # a body that negates remoteness stays onsite (no false positive)
    assert not scoring.remote_posting(
        "Engineer", "Austin, TX", "On-site only. This is not a remote role."
    )
    # nothing anywhere -> onsite
    assert not scoring.remote_posting("Engineer", "Austin, TX", "")


def test_a_us_state_scope_satisfies_a_us_region_filter():
    """`allowed_scopes: [US]` is ambiguous between "the US market" and "somewhere I can sit
    from my own address", and the token cannot tell them apart -- so `US` accepts `US-TX`
    and a user who means the strict reading names the subdivision. Making strict the
    mandatory reading would be a bigger behaviour change than this carries."""
    c = config.Config(remote_only=True, allowed_scopes=["US"], exclude_locations=[])

    def row(loc):
        return engine.derive_remote(
            engine._coerce(
                {"title": "AI Engineer", "company": "A", "url": "https://x/" + loc,
                 "source": "greenhouse", "location": loc}
            )
        )  # fmt: skip

    assert scoring.is_remote(row("Remote - TX"), c) is True
    assert scoring.is_remote(row("Remote - US & Canada"), c) is True
    # a US-INCLUSIVE region counts; a non-US one does not
    assert scoring.is_remote(row("Remote (North America)"), c) is True
    assert scoring.is_remote(row("Remote - EMEA"), c) is False
    assert scoring.is_remote(row("Remote - Brazil"), c) is False
    # stated-unbounded satisfies any policy; unstated needs the explicit opt-in
    assert scoring.is_remote(row("Remote - Anywhere"), c) is True
    assert scoring.is_remote(row("Remote"), c) is False
    c2 = config.Config(
        remote_only=True, allowed_scopes=["US", "UNSTATED"], exclude_locations=[]
    )
    assert scoring.is_remote(row("Remote"), c2) is True


def test_the_record_carries_areas_regions_and_the_raw_string():
    """The three fields, filled from one location string, each meaning one thing."""
    p = engine.derive_remote(
        engine._coerce(
            {"title": "AI Engineer", "company": "A", "url": "https://x/1",
             "source": "greenhouse", "location": "Americas (USA or Canada) (Remote)"}
        )
    )  # fmt: skip
    assert p["remote_areas"] == ["CA", "US"]
    assert p["remote_regions"] == ["AMERICAS"]
    assert p["remote_scope_raw"] == "Americas (USA or Canada) (Remote)"
    # an office address states no boundary, and the geography lands where it belongs
    q = engine.derive_remote(
        engine._coerce(
            {"title": "AI Engineer", "company": "A", "url": "https://x/2",
             "source": "greenhouse", "location": "Atlanta, GA - Remote"}
        )
    )  # fmt: skip
    assert q["remote_areas"] is None and q["remote_regions"] is None
    assert (q["city"], q["state"], q["country"]) == ("Atlanta", "GA", "US")


def test_an_adapter_that_supplied_the_raw_owns_the_parse():
    """`remote_scope_raw is None` is the adapter saying "I recorded no boundary evidence",
    and it is the only state in which the location string is ours to read.

    Before this, `None` meant both "unstated" and "derive me", so an adapter had no way to
    say "there is no boundary here, do not invent one". google_jobs could not stop
    `Anywhere` -- which under `&ltype=1` is Google's SEARCH MODE, not the posting's words --
    from becoming a stated-worldwide claim on 43 of 43 work-from-home rows. 11 of those 43
    state a US-only bound in their own title, body or URL, and through
    `scoring._region_allowed` an empty list is the one unconditional bypass in the scoring
    layer, so each satisfied a filter that excludes it.

    Measured at 781e504: 43 rows lose the `[]`, 348 adapter-supplied boundaries are kept,
    and ZERO rows lose a boundary the location legitimately stated."""
    from job_radar import engine

    # google_jobs now records Google's own word and keeps the boundary unstated.
    g = engine.derive_remote(
        engine._coerce(
            {"title": "AI Engineer", "company": "A", "url": "https://x/3",
             "source": "google_jobs", "location": "Anywhere",
             "remote_scope_raw": "Anywhere",
             "remote_type": "remote", "remote_basis": "stated"}
        )
    )  # fmt: skip
    assert g["remote_areas"] is None, "a search-mode token is not a stated boundary"
    assert g["remote_scope_raw"] == "Anywhere", "Google's word survives as evidence"
    assert g["remote_type"] == "remote", "the vendor's work_from_home flag is untouched"

    # The SAME string, from a source that recorded no raw, still parses as before -- the
    # rule keys on who spoke, not on the value.
    d = engine.derive_remote(
        engine._coerce(
            {"title": "AI Engineer", "company": "A", "url": "https://x/4",
             "source": "greenhouse", "location": "Anywhere"}
        )
    )  # fmt: skip
    assert d["remote_areas"] == []


def test_a_us_town_named_after_a_country_is_not_dropped():
    """Turkey TX, Peru IN, Greece NY, China ME, Italy TX and Egypt TX are real US places,
    and the non-US filter matches country NAMES against raw text. The pipeline already did
    this correctly one step earlier -- _coerce reads "Turkey, TX" as country=US -- and the
    gate then threw the row away on the word "turkey". The row's own parsed country wins,
    which is this package's standing rule applied to the last gate that ignored it."""
    c = config.Config(remote_only=True)
    for loc in ("Turkey, TX", "Peru, IN", "Greece, NY", "China, ME", "Egypt, TX"):
        p = engine.derive_remote(
            engine._coerce(
                {
                    "title": "AI Engineer",
                    "company": "A",
                    "url": "https://x/" + loc,
                    "source": "greenhouse",
                    "location": loc,
                    "text": "This is a remote position open across the US.",
                }
            )
        )
        assert p["country"] == "US", loc
        assert scoring.is_remote(p, c) is True, loc
    # ...and a genuinely foreign row is still excluded: only a parsed US country vetoes.
    fr = engine.derive_remote(
        engine._coerce(
            {
                "title": "AI Engineer",
                "company": "A",
                "url": "https://x/fr",
                "source": "greenhouse",
                "location": "Paris, France",
                "text": "This is a remote position.",
            }
        )
    )
    assert scoring.is_remote(fr, c) is False


def test_allowed_scopes_survives_the_yaml_load_path(tmp_path, capsys):
    """Every other remote_regions test builds a Config in Python, so the YAML path this
    key actually arrives through had no coverage at all. Both failure modes are silent
    board-emptiers: a missing bracket, and a plausible-but-wrong value."""
    p = tmp_path / "c.yaml"
    p.write_text("filters:\n  allowed_scopes: US\n", encoding="utf-8")
    c = config.load_config(p)
    assert c.allowed_scopes == ["US"], "a bare scalar must be repaired, not iterated"
    assert "should be a list" in capsys.readouterr().err

    # a well-formed list whose VALUE is wrong -- shape repair cannot catch this
    p.write_text("filters:\n  allowed_scopes: [USA, ANY]\n", encoding="utf-8")
    config.load_config(p)
    assert "unrecognised" in capsys.readouterr().err

    p.write_text("filters:\n  allowed_scopes: [US, ANY]\n", encoding="utf-8")
    assert config.load_config(p).allowed_scopes == ["US", "ANY"]
    assert capsys.readouterr().err == "", "a correct list must warn about nothing"


def test_remote_scope_only_emits_values_the_vocabularies_allow():
    """`remote_areas` is validated by FORMAT (a closed list would have to enumerate every
    subdivision on earth); `remote_regions` by MEMBERSHIP in a closed token set."""
    for loc in (
        "Remote - Brazil", "United States (Remote)", "Remote - EMEA", "Remote - TX",
        "Remote (North America)", "Remote - Anywhere", "Remote, UTC -8", "Remote",
        "Atlanta, GA - Remote", "Philippines (Remote)",
    ):  # fmt: skip
        areas, regions = vocab.remote_scope(loc)
        for a in areas or []:
            assert vocab.REMOTE_AREA_RE.match(a), (loc, a)
        for r in regions or []:
            assert r in vocab.REMOTE_REGION_TOKENS, (loc, r)


def test_both_gates_agree_about_a_us_inclusive_posting():
    """The two gates USED TO CONTRADICT each other on the same row, and the counterexample
    was already sitting in this file: `_location_excluded`'s US veto deliberately rescues
    these strings, and then `remote_scope` answered CA for "Remote - US & Canada" because
    its country pass ran before the US marker -- so `allowed_scopes=["US","ANY"]` threw the
    row away one gate later. 112 rows named the US explicitly and carried a foreign
    boundary. One fixture now pins both gates so they cannot drift apart again."""
    c = config.Config(remote_only=True, allowed_scopes=["US", "ANY"])
    for loc in (
        "Remote (United States | Canada)",
        "Americas (USA or Canada) (Remote)",
        "Remote - US & Canada",
    ):
        areas, regions = vocab.remote_scope(loc)
        assert "US" in (areas or []) or "AMERICAS" in (regions or []), loc
        p = engine.derive_remote(
            engine._coerce(
                {
                    "title": "AI Engineer",
                    "company": "A",
                    "url": "https://x/" + loc,
                    "source": "greenhouse",
                    "location": loc,
                }
            )
        )
        assert scoring.is_remote(p, c) is True, loc


def test_a_named_country_beats_the_anywhere_token():
    """ "Remote - Anywhere in Brazil" means anywhere WITHIN Brazil. Checking ANY first
    answered ANY and lost the boundary that matters, on 24 rows, 11 of them French."""
    assert vocab.remote_scope("Remote - Anywhere in Brazil") == (["BR"], None)
    assert vocab.remote_scope("Remote - Anywhere") == ([], None)


def test_the_body_literal_gates_change_no_verdict():
    """remote_signal gates each expensive body pattern behind a cheap substring test. The
    gate words are hand-derived, and a first version omitted `anywhere` and silently lost
    192 remote verdicts. This asserts each pattern's literal set is complete by checking a
    body that matches ONLY through that pattern's rarest literal."""
    cases = [
        ("This role is remote; you may work from anywhere in the US.", "remote"),
        ("Telecommuting is available for this position.", "remote"),
        ("Our hybrid schedule is three days in the office.", "hybrid"),
        ("This is not a remote position.", None),
    ]
    for body, want in cases:
        assert scoring.remote_signal("Engineer", "Austin, TX", body)[0] == want, body

    # Each of these carries exactly ONE of the declared literals and no other, so a set
    # that is short by that word fails here rather than silently dropping verdicts in the
    # field. "anywhere" is the one that already went missing once, at a cost of 192 rows.
    isolating = [
        ("You can work from anywhere.", "remote"),  # only `anywhere`
        ("Telecommuting is offered.", "remote"),  # only `telecommut`
        ("You will spend 3 days in the office each week.", "hybrid"),  # only `office`
        ("On-site only.", None),  # only `site`
    ]
    for body, want in isolating:
        assert scoring.remote_signal("Engineer", "Austin, TX", body)[0] == want, body


def test_a_full_week_in_the_office_is_onsite_not_hybrid():
    """`_HYBRID_RE`'s day-count alternatives never looked at the number, so "in office 5
    days a week" -- a plain statement of an ONSITE schedule -- was read as evidence of a
    split one. 38 rows in a 7,545-row harvest, 34 hybrid and 3 remote.

    CONCENTRATION, because it changes what the number means: 35 of the 38 are Postman, 2
    Anthropic, 1 OpenAI. Narrow and real, not a broad win.

    BLIND SPOT, stated so a reader finds it: the numeral branch cannot see a spelled-out
    count above five. 31 corpus bodies write "four days a week in the office" and every one
    is genuinely hybrid today, so the gap costs nothing measurable -- but this test cannot
    detect a posting that writes "seven days", and neither can the pattern."""
    from job_radar import scoring

    # Verbatim from the corpus, with row counts at 781e504.
    postman = "At Postman we value in person collaboration. We are in office 5 days a week for all roles based out of our offices."  # 35 rows
    assert scoring.remote_signal("Engineer", "San Francisco", postman) == (
        "onsite",
        "text",
    )
    # ONSITE must be tested BEFORE hybrid: this same string also matches `_HYBRID_RE` via
    # "days in the office", so behind hybrid the branch is unreachable.
    assert scoring._HYBRID_RE.search(postman.lower()) is not None

    for body in (
        "This role requires working in-office 5 days per week in San Francisco.",  # 1 row
        "Will be based in San Francisco, CA and be expected in office 5 days per week.",
    ):
        assert scoring.remote_signal("Engineer", "San Francisco", body)[0] == "onsite"

    # A genuine split week is untouched.
    assert (
        scoring.remote_signal("Engineer", "Austin", "We are in office 3 days a week.")[
            0
        ]
        == "hybrid"
    )


def test_a_qualified_telework_is_hybrid_not_remote():
    """`_ROLE_REMOTE_RE` carries a bare `\\btelecommut\\w*|\\btelework\\w*`, and nothing
    claimed the QUALIFIED phrasings before it -- so a percentage or a part-time telework
    came out `remote`. Every telework/telecommute occurrence in a 7,545-row harvest was
    read: 35 hits, 26 distinct contexts, and the qualified ones dominate.

    The bare stems are deliberately still there. "Telecommuting is available for this
    position" IS a role-level assertion and `test_the_body_literal_gates_change_no_verdict`
    pins it; the defect was the missing hybrid claim, not the stem."""
    from job_radar import scoring

    # Verbatim corpus shapes, counts at 781e504.
    for body in (
        "40 hrs/week 50% Telecommuting Permitted. Multiple Positions Available.",  # 9 rows
        "Up to 50% telework permitted. Multiple Positions Available.",
        "Part-time telecommuting is an option. Hybrid work from our office.",  # 2 rows
        "Telework Type: Part-Time Telework Work Location: Clay, NY",  # 2 rows
    ):
        assert scoring.remote_signal("Engineer", "Austin, TX", body) == (
            "hybrid",
            "text",
        ), body

    # ...and one whose own words say office is ONSITE, not merely "not remote". 4 rows.
    assert scoring.remote_signal(
        "Engineer",
        "Ogden, UT",
        "Telework Type: Full-Time Office/Project Work Location:",
    ) == ("onsite", "text")

    # The unqualified assertion still reads as remote -- the stem was never the bug.
    assert (
        scoring.remote_signal(
            "Engineer", "Austin, TX", "Full-time telecommuting is an option."
        )[0]
        == "remote"
    )


def test_a_location_that_IS_an_arrangement_word_is_read_as_one():
    """`vocab.remote_type` is the exact whole-string normalizer every adapter already uses
    for a vendor's `workplaceType`, and `remote_signal` never asked it about the LOCATION --
    only the loose `_REMOTE_RE`. Cloudflare writes its location as the literal `In-Office`
    and `Distributed`; both went unclassified. +22 onsite, +19 remote, 0 flips at 781e504.

    ALL 41 ROWS ARE ONE EMPLOYER ON ONE SOURCE. A real per-row fix on a shape only some
    vendors write, not a general capability.

    Exact-match is the safety property. Adding `distributed` to `_REMOTE_RE` -- which is
    applied to the TITLE -- would stamp remote on 22 currently-unclassified
    distributed-systems titles to fix 19 location rows."""
    from job_radar import scoring

    assert scoring.remote_signal("Engineer", "In-Office", "") == ("onsite", "location")
    assert scoring.remote_signal("Engineer", "Distributed", "") == (
        "remote",
        "location",
    )

    # The negatives that make it safe -- a substring match would take all four.
    for title, loc in (
        ("Senior Distributed Systems Engineer", "Austin, TX"),
        ("Software Engineer - Distributed Systems", "Austin, TX"),
    ):
        assert scoring.remote_signal(title, loc, "") == (None, None), title

    # LAST, not first: a location of exactly "Remote" whose body states a split week stays
    # hybrid, which is the documented "City (Remote)" demotion. 2 rows.
    assert scoring.remote_signal(
        "Engineer", "Remote", "Our hybrid schedule is three days in the office."
    ) == ("hybrid", "text")


def test_remote_signal_records_how_it_decided():
    """`is_remote` called remote_posting, took a bare bool and wrote NOTHING -- so a row
    admitted because its TITLE said remote was byte-identical in the record to a row
    nobody classified. 462 title-decided rows and 1,642 body-decided ones in a 31,790-row
    harvest, which is why a downstream consumer invented a basis outside REMOTE_BASES."""
    assert scoring.remote_signal("Senior AI Engineer - Remote", "Austin, TX", "") == (
        "remote",
        "title",
    )
    # the BOUNDARY is no longer remote_signal's job -- it moved to engine.derive_remote,
    # because a source can state a boundary without saying anything about remoteness.
    assert scoring.remote_signal("AI Engineer", "Remote - Brazil", "") == (
        "remote",
        "location",
    )
    assert scoring.remote_signal(
        "AI Engineer", "Austin, TX", "This is a remote role."
    ) == ("remote", "text")
    # every basis it can emit is inside the closed vocabulary
    for t, loc, body in [
        ("E - Remote", "Austin, TX", ""),
        ("E", "Remote - US", ""),
        ("E", "Austin, TX", "Telecommuting is available."),
        ("E", "Austin, TX", "Our hybrid schedule is 3 days in the office."),
    ]:
        _, basis = scoring.remote_signal(t, loc, body)
        assert basis in vocab.REMOTE_BASES, (t, loc, basis)


def test_remote_signal_does_not_read_employer_policy_as_a_remote_role():
    """The measured false positive: 93 rows carry "we are a fully remote global company",
    the identical sentence appearing on postings in Mexico, Brazil and Canada at once, and
    773 more merely mention remoteness with no claim at all. Neither says THIS role is
    remote, so both must come back unknown rather than True."""
    assert scoring.remote_signal(
        "Senior CX Engineer",
        "MEXICO",
        "We are a fully remote global company with employees in Canada and the US.",
    ) == (None, None)
    assert scoring.remote_signal(
        "E", "Austin, TX", "We are a remote-first company."
    ) == (None, None)
    assert scoring.remote_signal("E", "Austin, TX", "") == (None, None)


def test_remote_signal_lets_hybrid_win():
    """Two measured cases the old order structurally could not see, because _REMOTE_RE
    matched the head and returned before anything else was read."""
    # 41 rows: hybrid and remote in the SAME string.
    assert scoring.remote_signal("E", "Palo Alto - Hybrid  (Remote)", "")[0] == "hybrid"
    # 346 of 2,887 "City (Remote)" rows state a split week in the body, so the location
    # tag is contradicted on ~1 row in 8. A specific factual claim beats a suffix.
    assert scoring.remote_signal(
        "E", "New York (Remote)", "Hybrid: 2 days a week in the office."
    ) == ("hybrid", "text")
    # ...but a location that names a real BOUNDARY is a genuine eligibility statement and
    # is not demoted by the word hybrid appearing somewhere in a long description.
    assert scoring.remote_signal(
        "E", "Remote - Brazil", "We also run a hybrid schedule at our offices."
    ) == ("remote", "location")


def test_hybrid_is_not_matched_as_a_bare_word():
    """`\\bhybrid\\b` matched 5,491 bodies and the third-largest group was "hybrid
    vehicles" -- automotive JDs, not work arrangements. Tightening to work-context nouns
    took it to 2,995 with zero vehicle/cloud matches."""
    assert scoring.remote_signal(
        "Engineer", "Austin, TX", "You will design hybrid vehicles and battery systems."
    ) == (None, None)
    assert scoring.remote_signal(
        "Engineer", "Austin, TX", "Experience with hybrid cloud architecture required."
    ) == (None, None)
    # the real thing, including the label form that ends a line with no following noun
    assert (
        scoring.remote_signal("E", "Austin, TX", "Our hybrid policy: 3 days in.")[0]
        == "hybrid"
    )
    assert (
        scoring.remote_signal("E", "Austin, TX", "Work arrangement: Hybrid")[0]
        == "hybrid"
    )


def test_coerce_records_the_remote_decision_without_overwriting_a_source():
    """The fallback may only ADD, the same discipline as the geography fallback."""
    row = {
        "title": "AI Engineer - Remote",
        "company": "A",
        "url": "https://x/1",
        "source": "greenhouse",
        "location": "Austin, TX",
    }
    # derive_remote runs AFTER the relevance gate in _consume, not inside _coerce, because
    # it scans the body and 69% of postings are discarded a moment later.
    p = engine.derive_remote(engine._coerce(row))
    assert (p["remote_type"], p["remote_basis"]) == ("remote", "title")
    assert "remote" not in p, "the derived bool was removed at 0.9.0; read remote_type"
    # a source that sent its own structured signal keeps it
    kept = engine.derive_remote(
        engine._coerce(
            {
                "title": "AI Engineer - Remote",
                "company": "A",
                "url": "https://x/2",
                "source": "ashby",
                "location": "Austin, TX",
                "remote_type": "hybrid",
                "remote_basis": "stated",
            }
        )
    )
    assert (kept["remote_type"], kept["remote_basis"]) == ("hybrid", "stated")


def test_remote_region_filter_separates_boundary_from_arrangement():
    """ "Is this remote" and "may I sit where it is remote from" are different questions.
    Measured on a 31,790-row harvest: no filter 7,442 rows; ["US","ANY"] 4,247;
    ["US","ANY","UNSTATED"] 7,242."""

    def row(loc):
        return engine.derive_remote(
            engine._coerce(
                {
                    "title": "AI Engineer",
                    "company": "A",
                    "url": "https://x/" + loc,
                    "source": "greenhouse",
                    "location": loc,
                }
            )
        )

    us, br, anywhere, bare = (
        row("United States (Remote)"),
        row("Remote - Brazil"),
        row("Remote - Anywhere"),
        row("Remote"),
    )
    # unset = no region filter at all, exactly as before this existed
    c = config.Config(remote_only=True, allowed_scopes=None, exclude_locations=[])
    assert all(scoring.is_remote(p, c) for p in (us, br, anywhere, bare))
    # US-only: Brazil goes, and an UNSTATED boundary is NOT admitted by default
    c = config.Config(
        remote_only=True, allowed_scopes=["US", "ANY"], exclude_locations=[]
    )
    assert scoring.is_remote(us, c) is True
    assert scoring.is_remote(anywhere, c) is True
    assert scoring.is_remote(br, c) is False
    assert scoring.is_remote(bare, c) is False
    # ...admitted only on an explicit opt-in, because unknown is not "anywhere"
    c = config.Config(
        remote_only=True, allowed_scopes=["US", "ANY", "UNSTATED"], exclude_locations=[]
    )
    assert scoring.is_remote(bare, c) is True
    assert scoring.is_remote(br, c) is False
    # a US-INCLUSIVE multi-region scope satisfies a US request -- "Americas" contains the US
    c = config.Config(remote_only=True, allowed_scopes=["US"], exclude_locations=[])
    assert scoring.is_remote(row("Remote (North America)"), c) is True


def test_is_remote_gate_uses_body():
    # a body-only remote signal now passes the remote_only gate (recovers Adzuna/
    # USAJOBS roles that carry no remote flag in title/location)
    c = config.Config(remote_only=True, exclude_locations=[])
    p = {"title": "Engineer", "location": "United States", "text": "Fully remote role."}
    assert scoring.is_remote(p, c) is True


# ── dedup ─────────────────────────────────────────────────────────────────────
def test_dedup_key_keeps_seniority():
    # Staff and Senior are genuinely different roles — they must NOT collapse.
    a = {"company": "Acme Inc", "title": "Staff AI Engineer"}
    b = {"company": "Acme Inc", "title": "Senior AI Engineer"}
    assert dedup.dedup_key(a) != dedup.dedup_key(b)


def test_ats_from_url():
    assert dedup.ats_from_url("https://boards.greenhouse.io/airbnb/jobs/123") == (
        "greenhouse",
        "airbnb",
    )
    assert dedup.ats_from_url("https://jobs.lever.co/anchorage/abc") == (
        "lever",
        "anchorage",
    )
    assert dedup.ats_from_url("https://example.com/careers") is None


# ── store: the load-bearing upsert ───────────────────────────────────────────
def _post(company, title, score, url):
    return {
        "company": company,
        "title": title,
        "score": score,
        "url": url,
        "posted": "2026-07-10",
        "sources": {"remoteok"},
        "text": "x",
        "signals": "ai",
    }


def test_upsert_preserves_status_across_runs(tmp_path):
    csvp = tmp_path / "shortlist.csv"
    p = _post("Acme", "AI Engineer", 40, "https://x/1")
    # run 1: new
    merged = shortlist.upsert(csvp, [p], today="2026-07-10")
    assert merged[0]["status"] == "new" and merged[0]["_is_new"] is True
    rid = merged[0]["id"]
    # user applies
    assert shortlist.mark_status(csvp, rid, "applied") is True
    # run 2: same role reappears with a fresh score
    p2 = _post("Acme", "AI Engineer", 55, "https://x/1")
    merged2 = shortlist.upsert(csvp, [p2], today="2026-07-12")
    row = next(r for r in merged2 if r["id"] == rid)
    assert row["status"] == "applied"  # status PRESERVED
    assert row["first_seen"] == "2026-07-10"  # first_seen PRESERVED
    assert int(row["score"]) == 55  # score refreshed
    assert row["_is_new"] is False


def test_applied_is_sticky_when_role_leaves_feed(tmp_path):
    csvp = tmp_path / "shortlist.csv"
    merged = shortlist.upsert(
        csvp, [_post("Acme", "AI Engineer", 40, "https://x/1")], today="2026-07-10"
    )
    shortlist.mark_status(csvp, merged[0]["id"], "applied")
    # next run: the role is gone from the market (empty postings)
    merged2 = shortlist.upsert(csvp, [], today="2026-07-12")
    assert any(r["status"] == "applied" for r in merged2)  # history persists


def test_surface_excludes_applied_and_low_score(tmp_path):
    c = _cfg()
    csvp = tmp_path / "shortlist.csv"
    ps = [
        _post("A", "AI Engineer", 40, "u1"),
        _post("B", "AI Engineer", 5, "u2"),
        _post("C", "AI Engineer", 33, "u3"),
    ]
    merged = shortlist.upsert(csvp, ps, today="2026-07-12")
    shortlist.mark_status(
        csvp, next(r["id"] for r in merged if r["company"] == "A"), "applied"
    )
    merged = shortlist.load_all(csvp)
    shown = shortlist.surface(merged, c)
    names = {r["company"] for r in shown}
    assert names == {"C"}  # A applied (excluded), B below min_score (excluded)


# ── the AI layer's no-op guarantee ───────────────────────────────────────────
def test_llm_is_noop_when_disabled():
    c = _cfg()
    assert c.llm.enabled is False
    items = [{"key": "k", "title": "AI Engineer", "company": "Acme", "text": "..."}]
    assert llm.rerank(items, c) == {}  # never calls out when disabled


def test_a_scan_persists_its_harvest_with_the_llm_enabled(tmp_path, monkeypatch):
    """The LLM path must write the harvest, exactly like the plain path.

    It did not. `upsert(write=not llm_on)` skipped the write to save one rewrite,
    and `annotate()` then re-read the file — which therefore never had the rows —
    and wrote that back. A scan with the LLM on stored NOTHING while printing that
    it had tracked the roles: `apply <id>` could never find an id, `first_seen`
    never accumulated, and every role stayed "new" forever.

    This whole path had zero tests, which is why a 181-green suite said nothing
    about it. Drives the real `cmd_scan` rather than the store functions, because
    the defect lived in how the CLI wired them together, not in either one."""
    cfg = config.Config(remote_only=True, min_score=0)
    cfg.llm = replace(cfg.llm, enabled=True, rerank_top_n=5)
    config.set_active(cfg)
    out = tmp_path / "shortlist.csv"

    def one_role(queries):
        return [
            {
                "title": "AI Engineer",
                "company": "Acme",
                "location": "Remote",
                "url": "https://x/1",
                "posted": "2026-07-10",
                "text": "Build RAG agentic LLM systems.",
                "source": "remotive",
            }
        ]

    monkeypatch.setattr(engine, "enabled_depth", lambda c: {})
    monkeypatch.setattr(engine, "enabled_breadth", lambda c: [("f", one_role)])
    # the LLM itself is stubbed — this is about persistence, not the model
    monkeypatch.setattr(
        llm,
        "rerank",
        lambda items, c: {items[0]["key"]: {"llm_score": 91, "llm_note": "strong fit"}},
    )
    args = types.SimpleNamespace(
        out=str(out), watchlist=None, limit=25, verbose=False, strict=False, config=None
    )
    cli.cmd_scan(args, cfg)

    rows = shortlist.load_all(out)
    assert len(rows) == 1, "the LLM path stored nothing — the harvest was discarded"
    assert rows[0]["llm_score"] == "91"  # and the annotation landed on it
    assert shortlist.mark_status(out, rows[0]["id"], "applied") is True  # apply works


# ── regression tests for the three-critic review fixes ───────────────────────
def test_config_empty_section_does_not_crash(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(
        "profile:\nscoring:\nfilters:\n  min_score: 10\n"
    )  # empty section bodies
    c = config.load_config(p)
    assert c.min_score == 10 and c.title_queries  # loads, defaults intact


def test_config_nonmapping_yaml_falls_back(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("just some text\n")  # scalar top level
    c = config.load_config(p)
    assert c.max_age_days == 60  # generic defaults, no crash


def test_llm_profile_nonempty_without_remote():
    c = config.Config(remote_only=False)
    assert llm._profile(c).strip()  # was empty before the fix


def test_surface_hides_rejected(tmp_path):
    c = _cfg()
    csvp = tmp_path / "s.csv"
    merged = shortlist.upsert(
        csvp, [_post("A", "AI Engineer", 90, "u1")], today="2026-07-12"
    )
    shortlist.mark_status(csvp, merged[0]["id"], "rejected")
    assert (
        shortlist.surface(shortlist.load_all(csvp), c) == []
    )  # rejected never resurfaces


def test_csv_formula_injection_neutralized(tmp_path):
    csvp = tmp_path / "s.csv"
    shortlist.upsert(
        csvp, [_post("=cmd|'/c calc'!A1", "AI Engineer", 40, "u1")], today="2026-07-12"
    )
    row = shortlist.load_all(csvp)[0]
    assert row["company"].startswith("'=")  # prefixed, inert in a spreadsheet


def test_no_column_can_carry_a_live_formula(tmp_path):
    """Asserting ONE column proved `_csv_safe` works while never proving TEXT_COLS
    was complete -- and it wasn't: `posted` bypassed it, fed straight from vendor
    data via `to_date`, so any of ~500 boards could put a formula in the sheet.
    Loop every column an adapter can populate, so the next omission fails here."""
    csvp = tmp_path / "s.csv"
    hostile = "=cmd|'/c calc'!A1"
    p = _post(hostile, hostile, 40, "u1")
    # every field an adapter controls, all hostile at once
    p.update(
        {
            "location": hostile,
            "team": hostile,
            "employment_type": hostile,
            "salary": hostile,
            "industry": hostile,
            "posted": util.to_date(hostile),
        }
    )
    shortlist.upsert(csvp, [p], today="2026-07-12")
    raw = Path(csvp).read_text(encoding="utf-8")
    for line in raw.splitlines()[1:]:
        for cell in line.split(","):
            assert not cell.lstrip('"').startswith(("=", "+", "@")), (
                f"live formula reached the CSV: {cell!r}"
            )


def test_a_discovered_config_cannot_redirect_a_credential(
    tmp_path, monkeypatch, capsys
):
    """`./job-radar.yaml` is auto-loaded, so a config you did not write is honored
    just because you ran from its directory. `base_url` picks the destination host
    and `api_key_env` picks which secret to send — together, enough to mail your
    ANTHROPIC_API_KEY to a stranger. Discovered configs lose those keys; an
    explicit --config keeps them."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "job-radar.yaml").write_text(
        "llm:\n  enabled: true\n  base_url: https://evil.example/v1\n"
        "  api_key_env: ANTHROPIC_API_KEY\n",
        encoding="utf-8",
    )
    found = cli._resolve_config(None)
    assert found.llm.base_url == "", "a discovered config redirected the LLM call"
    assert found.llm.enabled is True, "non-redirect keys must still apply"
    assert "ignoring" in capsys.readouterr().err  # and it says so

    named = cli._resolve_config("job-radar.yaml")
    assert named.llm.base_url == "https://evil.example/v1"  # explicit = opted in


def test_env_strips_whitespace_so_a_key_never_carries_a_newline():
    """A key with a trailing newline is the ordinary accident (.env, $(cat key),
    CRLF). Unstripped it reaches an HTTP header, http.client raises with the key
    in the message, and the old llm.py printed that message to stdout."""
    c = config.Config()
    os.environ["JR_TEST_KEY"] = "sk-ant-secret\n"
    try:
        assert c.env("JR_TEST_KEY") == "sk-ant-secret"
    finally:
        del os.environ["JR_TEST_KEY"]


def test_llm_failure_never_prints_the_key(capsys):
    """The regression: llm.py printed `{e}`, and the exception carries the header
    value. Report the exception TYPE only, as every other error path here does."""
    secret = "sk-ant-DO-NOT-LEAK"
    c = config.Config()
    c.llm = replace(
        c.llm, enabled=True, provider="anthropic", api_key_env="JR_TEST_KEY"
    )
    config.set_active(c)
    os.environ["JR_TEST_KEY"] = secret + "\n"
    try:
        llm.rerank(
            [{"key": "k", "title": "AI Engineer", "company": "A", "text": "x"}], c
        )
    finally:
        del os.environ["JR_TEST_KEY"]
    out = capsys.readouterr().out
    assert secret not in out, f"API KEY LEAKED TO STDOUT: {out!r}"


def test_surface_tolerates_dirty_hand_edits(tmp_path):
    c = _cfg()
    csvp = tmp_path / "s.csv"
    shortlist.write_all(
        csvp,
        [
            {
                "id": "x",
                "dedup_key": "k",
                "score": "45.5",
                "age_days": "abc",
                "status": "new",
                "company": "A",
            }
        ],
    )
    shortlist.surface(shortlist.load_all(csvp), c)  # must not raise


def test_rerank_tolerates_null_fit(monkeypatch):
    c = config.Config()
    c.llm.enabled = True
    monkeypatch.setattr(c, "env", lambda k: "fake-key")
    monkeypatch.setattr(llm, "_call", lambda cfg, u: '[{"id":0,"fit":null,"note":"x"}]')
    out = llm.rerank([{"key": "k", "title": "t", "company": "c", "text": "jd"}], c)
    assert out["k"]["llm_score"] == 0  # null fit -> 0, no crash


# ── the location radius (200 miles around Louisville) ────────────────────────
def test_config_loads_radius(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text('filters:\n  location: "Louisville, KY"\n  radius_miles: 200\n')
    c = config.load_config(p)
    assert c.radius_miles == 200 and c.location == "Louisville, KY"


def _capture_url(monkeypatch, seen):
    from job_radar import sources

    monkeypatch.setenv("ADZUNA_APP_ID", "x")
    monkeypatch.setenv("ADZUNA_APP_KEY", "y")
    monkeypatch.setattr(sources.time, "sleep", lambda *a: None)

    def fake(url):
        seen["url"] = url
        return {"results": []}

    monkeypatch.setattr(sources, "get_json", fake)
    return sources


def test_adzuna_url_includes_radius(monkeypatch):
    seen = {}
    sources = _capture_url(monkeypatch, seen)
    config.set_active(config.Config(location="Louisville, KY", radius_miles=200))
    sources.search_adzuna(["registered nurse"])
    assert "distance=322" in seen["url"]  # 200 mi -> 322 km


def test_adzuna_no_radius_when_remote(monkeypatch):
    seen = {}
    sources = _capture_url(monkeypatch, seen)
    config.set_active(config.Config(location="remote", radius_miles=200))
    sources.search_adzuna(["ai engineer"])
    assert "distance=" not in seen["url"]  # a radius is meaningless for remote


# ── pure helpers (util) ───────────────────────────────────────────────────────
def test_to_date_handles_epoch_and_iso():
    assert util.to_date("2026-07-14T09:00:00Z") == "2026-07-14"
    assert util.to_date(1_752_000_000) == "2025-07-08"  # epoch seconds
    assert (
        util.to_date(1_752_000_000_000) == "2025-07-08"
    )  # epoch millis (same instant)
    assert util.to_date("") == "" and util.to_date(None) == ""


def test_salary_from_text_accepts_pay_rejects_funding():
    assert util.salary_from_text("Comp: $120k-$150k") == "$120k-$150k"
    assert util.salary_from_text("$120,000 - $150,000 annually").startswith("$120,000")
    assert util.salary_from_text("Rate $100-150/hr") == "$100-150"  # unit anchor
    assert util.salary_from_text("We raised $20-40 million in Series B") == ""
    assert util.salary_from_text("a $20-40 discount") == ""  # bare range, ambiguous


def test_has_is_whole_word():
    # callers pass already-lowercased text (see scoring.score); has() is case-exact
    assert util.has("ai", "senior ai engineer")
    assert not util.has("ai", "available training")  # not a substring hit


# clean() had NO test at all, which is how it shipped inverted for every release. The
# fixture below is real Greenhouse `content`, trimmed: HTML-escaped HTML, whose own
# text carries a second layer of escaping.
_GREENHOUSE_BODY = (
    "&lt;div class=&quot;content-intro&quot;&gt;&lt;p&gt;&lt;strong&gt;About Us"
    "&lt;/strong&gt;&lt;/p&gt;&lt;/div&gt;\n"
    "&lt;ul&gt;\n"
    "&lt;li&gt;Experience presenting to the C-Suite&lt;/li&gt;\n"
    "&lt;li&gt;Willing to travel as needed (&amp;lt;25%) to hit the goals&lt;/li&gt;\n"
    "&lt;li&gt;Bachelor&#39;s degree required&lt;/li&gt;\n"
    "&lt;/ul&gt;\n"
    "&lt;p&gt;Pay Range: $120,000 &amp;ndash; $150,000&amp;nbsp;&lt;/p&gt;"
)


def test_clean_decodes_entities_before_it_strips_tags():
    """The bug, stated as a test: this ran the strip FIRST, found no `<...>` in an
    escaped body, and then let unescape turn `&lt;div&gt;` into `<div>` -- so the
    function that removes markup was the one creating it. Greenhouse escapes 100% of
    its bodies, which is ~65% of a harvest."""
    out = util.clean(_GREENHOUSE_BODY)
    assert "<div" not in out and "<li" not in out and "<p>" not in out
    assert "About Us" in out and "Bachelor's degree required" in out


def test_clean_recovers_a_salary_that_tags_used_to_hide():
    """Not cosmetic. `salary_from_text` matched 0 of 809 live Greenhouse postings and
    424 of 809 after this fix, because the pay figures sat between tags."""
    assert util.salary_from_text(util.clean(_GREENHOUSE_BODY)).startswith("$120,000")


def test_clean_keeps_bullets_on_their_own_lines():
    """A block closer becomes a newline, not a space. The median Greenhouse posting is
    16 `<li>` items and nothing else separates them, so collapsing to spaces runs every
    requirement into one line."""
    lines = util.clean(_GREENHOUSE_BODY).splitlines()
    assert "Experience presenting to the C-Suite" in lines
    assert "Bachelor's degree required" in lines
    assert not any(ln != ln.strip() for ln in lines)  # no leading space from `<li>`
    # _GREENHOUSE_BODY alone does NOT pin _BLOCK_END: it has literal newlines between its
    # items, faithful to 2,554 of 2,697 real bodies, so the final whitespace collapse
    # recovers the breaks on its own and the test passed with _BLOCK_END disabled. This
    # run-together fragment is the 3-in-2,697 shape that only _BLOCK_END can separate.
    assert util.clean("&lt;li&gt;First&lt;/li&gt;&lt;li&gt;Second&lt;/li&gt;") == (
        "First\nSecond"
    )
    # `<br>` is the same case and was missing from the pattern entirely -- 7,610 of them
    # across 1,495 of 2,697 bodies.
    assert (
        util.clean("Remote-first&lt;br /&gt;Flexible hours")
        == "Remote-first\nFlexible hours"
    )


def test_clean_does_not_eat_prose_between_angle_brackets():
    """The regression that made the tag pattern strict. `<[^>]+>` is the obvious
    pattern and it deletes the clause in `(<25%) ... hit the goals` on real postings --
    once entities are decoded there is a literal `<` with prose after it. Measured on 12
    of 2,697 live bodies."""
    out = util.clean(_GREENHOUSE_BODY)
    assert "(<25%) to hit the goals" in out
    # ORDER MATTERS in this assertion and the first version of it got the order wrong:
    # with `&gt;50` first there is no `<`...`>` span at all, so the loose pattern passed
    # it and the test proved nothing. `<` first is the shape that discriminates -- the
    # loose pattern returns "Budgets 50".
    assert util.clean("Budgets &lt;2% of revenue while managing teams of &gt;50") == (
        "Budgets <2% of revenue while managing teams of >50"
    )


def test_an_inline_tag_leaves_no_space_where_it_stood():
    """`_TAG.sub(" ", ...)` replaced every tag with a space, which is right for a block
    tag and wrong for a bold or a link. Measured across 2,712 live bodies from 9 boards:
    4,580 spaces sitting before a punctuation mark, on 67.7% of rows with a body -- and
    a tag boundary landing mid-word split "the" into "t he". Both strings below are
    verbatim from live postings (himalayas, clickhouse).
    """
    assert util.clean("At <a href='#'>Smartsheet</a>, your ideas are heard") == (
        "At Smartsheet, your ideas are heard"
    )
    assert util.clean("the United States<span>, t</span>he typical salary") == (
        "the United States, the typical salary"
    )


def test_an_inline_tag_between_two_words_still_separates_them():
    """THE GUARD, and the reason the rule is not simply "delete inline tags". A word
    split by a tag always CONTINUES in lower case; two distinct words do not. So an
    uppercase letter after the run means these were two words and the space stays --
    without it `<strong>Requirements</strong>Must have` collapses to the single token
    "RequirementsMust".

    A module-level `re.I` on the pattern case-folds the `(?![A-Z])` lookahead too, which
    turns this guard into a no-op while leaving every test green; that is why the flag is
    scoped to the tag half. This test is what notices.
    """
    assert util.clean("<strong>Requirements</strong>Must have 5 years") == (
        "Requirements Must have 5 years"
    )
    # And the same shape through the section reader, where it decides a boundary.
    text, _secs = util.clean_with_sections(
        "<p><strong>Deploy</strong><strong>X</strong>Deploy</p>"
    )
    assert text == "Deploy X Deploy"


def test_clean_leaves_a_plain_text_body_alone():
    """Eleven of nineteen sources send plain text. The fix must be a no-op for them."""
    assert util.clean("  Senior  Engineer\n\n  Remote (US)  ") == (
        "Senior Engineer\nRemote (US)"
    )
    assert util.clean("") == "" and util.clean(None) == ""
    # A non-breaking space is whitespace to a reader and not to `[ \t]+`. 0.8.2 collapsed
    # it; the first version of this fix did not, leaving a literal U+00A0 on 67% of live
    # bodies -- enough to break the multi-token keyword "prompt engineering" on a posting
    # that used one between the words.
    assert util.clean("prompt&amp;nbsp;engineering") == "prompt engineering"


# ── sections: the structure clean() would otherwise destroy (0.9.0) ───────────
def test_sections_are_read_before_the_tags_are_stripped():
    """THE ORDERING INVARIANT, as an executable assertion. The same body yields
    structure from the raw vendor string and NOTHING once it has been cleaned, because
    a header is only a header while it is still wrapped in a tag."""
    text, secs = util.clean_with_sections(_GREENHOUSE_BODY)
    assert secs, "no sections from the raw body"
    assert util.clean_with_sections(text)[1] == [], (
        "cleaned text still yielded sections -- the invariant is not what it claims"
    )


def test_sections_offsets_index_the_records_own_text():
    text, secs = util.clean_with_sections(_GREENHOUSE_BODY)
    for s in secs:
        assert 0 <= s["start"] <= s["end"] <= len(text)
    # Non-overlapping and in document order: the lookup walks forward, so a heading
    # whose words recur later in the posting cannot capture the wrong span.
    bounds = [(s["start"], s["end"]) for s in secs]
    assert bounds == sorted(bounds)
    assert all(a[1] <= b[0] for a, b in zip(bounds, bounds[1:]))


def test_two_sections_with_identical_text_get_different_spans():
    """The forward `pos` cursor, pinned. Dropping it -- `text.find(seg)` instead of
    `text.find(seg, pos)` -- passes every other test in this file, because no fixture
    happens to repeat a section's text. Measured against the shipped code, dropping it
    moves 190 spans across 105 of 2,697 live bodies -- silently, and all in the same
    direction: a later occurrence collapses onto an earlier one."""
    text, secs = util.clean_with_sections(
        "&lt;p&gt;&lt;strong&gt;Requirements&lt;/strong&gt;&lt;/p&gt;"
        "&lt;p&gt;Five years of Python.&lt;/p&gt;"
        "&lt;p&gt;&lt;strong&gt;Nice to have&lt;/strong&gt;&lt;/p&gt;"
        "&lt;p&gt;Five years of Python.&lt;/p&gt;"
    )
    assert len(secs) == 2
    assert secs[0]["start"] != secs[1]["start"], (
        "the second span collapsed onto the first"
    )
    assert secs[0]["end"] <= secs[1]["start"]
    assert text[secs[1]["start"] : secs[1]["end"]] == "Five years of Python."


def test_an_unclosed_bold_tag_cannot_make_the_header_scan_quadratic():
    """`(.*?)` under re.S sends every unclosed `<strong>` scanning to end-of-string,
    and the next opener repeats it -- 16,000 of them in a 158 KB body took 7.6 seconds
    before the bound went on. Real postings carry at most ONE unclosed tag, so this is
    insurance, not a live fix; the test exists so the bound cannot be quietly removed.
    """
    body = ("<strong>x" * 4000) + ("filler " * 500)
    start = time.perf_counter()
    sections.split(body)
    assert time.perf_counter() - start < 2.0, "the header scan went quadratic again"


def test_the_header_bound_is_wide_enough_for_a_real_header():
    """The companion to the test above, and the one that matters more. That test proves
    a bound EXISTS; this proves it is wide enough. An over-tight bound fails silently --
    the header is simply never found, the section boundary disappears, and the body
    still parses. At `{0,60}` the suite stays green while 2,947 section boundaries
    vanish across 1,645 of 2,697 real bodies.

    The longest real header seen carries 739 characters of inner markup (p50 is 27), so
    a 740-character header must still extract. `{0,400}` is the tightest bound that
    fails this.
    """
    header = "Responsibilities " + ("and more duties " * 46)  # 753 chars
    assert len(header) > 739
    parts = sections.split(f"<p><strong>{header}</strong></p><p>Ship things.</p>")
    assert parts, "a real-length header was not extracted -- the bound is too tight"
    assert parts[0][1].strip() == header.strip()
    assert "Ship things." in parts[0][2]


def test_a_span_never_points_into_its_own_header():
    """The failure the design's docstring rules out, pinned. `pos` advanced only over
    section BODIES, so every header stayed inside the next search window -- and a
    section whose opening words also appear in the header above it matched THERE.
    Measured on 6,697 real bodies: 14 of 63,209 sections carried a span pointing into
    their own header, and `text[start:end]` returned the right STRING in every one, so
    nothing downstream could detect it. Only the position was wrong.

    A zero-length section is the same bug in its clearest form: it used to anchor
    BEFORE its own header rather than after it, on all 4,516 of them.
    """
    text, secs = util.clean_with_sections(
        "&lt;strong&gt;Deploy&lt;/strong&gt;&lt;strong&gt;X&lt;/strong&gt;Deploy"
    )
    assert text == "Deploy X Deploy"
    # The body under "X" is the SECOND "Deploy" at index 9, not the header at index 0.
    assert text[secs[1]["start"] : secs[1]["end"]] == "Deploy"
    assert secs[1]["start"] == 9
    # And the empty section under the first header sits after that header, not at 0.
    assert secs[0]["start"] == secs[0]["end"] == 6


def test_the_pre_header_intro_carries_a_null_header_not_an_empty_string():
    """`""` is not a contract value. The intro entry is the one section with no header
    of its own, so it is the only place this can regress."""
    _text, secs = util.clean_with_sections(
        "&lt;p&gt;We are hiring.&lt;/p&gt;"
        "&lt;p&gt;&lt;strong&gt;Requirements&lt;/strong&gt;&lt;/p&gt;&lt;p&gt;Python.&lt;/p&gt;"
    )
    assert secs[0]["header"] is None and secs[0]["type"] is None
    assert all(s["header"] is None or s["header"] for s in secs)


def test_a_section_that_cannot_be_located_gets_no_offsets_rather_than_wrong_ones(
    monkeypatch,
):
    """The load-bearing test, and the only one that pins the design's single self-check.

    Asserting `text[start:end] == section_text` proves nothing -- it is true by
    construction, because the offsets are produced BY searching for that text. What can
    actually go wrong is the splitter and `clean` disagreeing about the same bytes, and
    the contract is that such a section arrives with its `type` and `header` and no
    boundaries at all. Never with a plausible-looking guess.
    """
    monkeypatch.setattr(
        util.sections,
        "split",
        lambda body: [("requirements", "Experience", "text that is not in the body")],
    )
    text, secs = util.clean_with_sections(_GREENHOUSE_BODY)
    assert len(secs) == 1
    assert secs[0]["type"] == "requirements" and secs[0]["header"] == "Experience"
    assert "start" not in secs[0] and "end" not in secs[0]
    assert text == util.clean(_GREENHOUSE_BODY)  # the body itself is unaffected


def test_a_body_with_no_headers_yields_no_sections_not_a_copy_of_itself():
    """Eighteen of nineteen sources send plain text. The module this was ported from
    returned the WHOLE BODY as one unclassified section, which would have put a second
    complete copy of every description into every one of their records."""
    assert util.clean_with_sections("Plain prose. No markup at all.") == (
        "Plain prose. No markup at all.",
        [],
    )
    assert util.clean_with_sections(None) == ("", [])  # HN can send a null body
    assert util.clean_with_sections("") == ("", [])


def test_a_header_with_nothing_under_it_is_an_empty_span_not_a_failure():
    """4,516 of 31,258 entries on the nine-board corpus are one bold line immediately
    followed by the next. That is an empty section, and giving it ABSENT offsets would
    make it indistinguishable from the genuine disagreement above."""
    _text, secs = util.clean_with_sections(
        "&lt;strong&gt;Requirements&lt;/strong&gt;&lt;strong&gt;Benefits&lt;/strong&gt;"
        "&lt;p&gt;Health cover.&lt;/p&gt;"
    )
    empty = [s for s in secs if s["start"] == s["end"]]
    assert empty, "expected at least one zero-length section"
    assert all("start" in s for s in secs)


# ── which <strong> is a HEADING (0.9.0). Markup below is verbatim from live boards ──
def test_mid_sentence_emphasis_is_not_a_section_heading():
    """0.9.0 promoted every `<strong>` it found, so emphasis inside a sentence became a
    header and the REST OF THAT SENTENCE became its section: 5,753 sections across
    2,056 rows `[local 94-board harvest, 0.9.0]`, spans opening on the word "to".

    Body is verbatim clickhouse. The measurement that matters is not that the header is
    gone -- it is that the sentence is still one span.
    """
    body = (
        "<p>We are seeking an experienced <strong>Solutions Architect</strong> to help "
        "expand our presence in Germany.</p>"
    )
    text, secs = util.clean_with_sections(body)
    # No heading in the body at all, so `[]` -- the documented "this posting has no
    # headers" value, not an intro entry.
    assert secs == [], "mid-sentence emphasis was promoted to a heading"
    assert "Solutions Architect to help expand" in text


def test_a_label_value_paragraph_is_a_heading_even_though_prose_follows_it():
    """THE CLAUSE THAT MUST NOT BE TIGHTENED, and the reason the rule tests only what
    comes BEFORE the emphasis.

    Requiring the bold to be the whole line scores better on every span-quality metric
    -- and deletes this shape, which is 487 of 487 postings at one employer and took
    `eeo_legal` coverage from 100% to 0% on three boards when measured. A metric built
    from "does this span end mid-sentence" cannot see that cost, because a deleted
    section has no span to judge. Verbatim anthropic.
    """
    body = (
        "<p><strong>Visa Sponsorship: </strong>We are not currently able to sponsor "
        "visas for fellows.</p>"
    )
    _text, secs = util.clean_with_sections(body)
    assert [s["header"] for s in secs] == ["Visa Sponsorship:"]
    assert secs[0]["type"] == "eeo_legal"


def test_inside_a_list_item_a_heading_must_terminate_its_label():
    """A bullet's bold lead-in is a TERM, not a heading -- unless it terminates its
    label with a colon. The discriminator is morphology, not the container: excluding
    list items outright instead destroys 130 typed headers, 34 of them at a non-tech
    employer, because `<li><strong>Experience:</strong> 7+ years` is the same construct
    as the paragraph above with a different wrapper.

    All three bullets are verbatim (clickhouse, anthropic). The colon counts whether the
    employer put it inside the tag or outside it -- reading only the first spelling made
    the rule disagree with itself on 149 sections of one shape.
    """
    body = (
        "<ul>"
        "<li><strong>Experience:</strong> 7+ years in SRE.</li>"
        "<li><strong>Strategic Technical Partnership</strong>: Be a thought partner.</li>"
        "<li><strong>Close the delivery loop.</strong> Track forecast-versus-delivered.</li>"
        "</ul>"
    )
    _text, secs = util.clean_with_sections(body)
    assert [s["header"] for s in secs] == [
        "Experience:",
        "Strategic Technical Partnership",
    ], "a colonless bullet lead-in was read as a heading"


def test_two_adjacent_bold_runs_are_two_headings():
    """`<strong>A</strong><strong>B</strong>` with no block tag between them is real
    markup, not a fixture artifact: 160 occurrences across 52 of 2,752 live bodies and
    9 of 11 vendors. A block-initial rule that does not know this reads the FIRST bold's
    text as content disqualifying the second, and drops it.

    The `<h2>` form is verbatim anthropic. This also pins the lookup that finds the
    boundary: searching `finditer(body, 0, m.start())` truncates the string at `endpos`,
    so the lookahead cannot see the opener that follows -- and that opener sits at
    exactly `m.start()`. The rule then silently reverts and this test is the only thing
    that notices.
    """
    _text, secs = util.clean_with_sections(
        "<h2><strong>Key responsibilities</strong><strong><br></strong></h2>"
        "<ul><li>Lead the team.</li></ul>"
    )
    assert secs[0]["header"] == "Key responsibilities"
    _text, secs = util.clean_with_sections(
        "<p><strong>PLEASE NOTE</strong><strong>: Due to federal requirements.</strong></p>"
    )
    assert [s["header"] for s in secs] == [
        "PLEASE NOTE",
        ": Due to federal requirements.",
    ]


def test_a_bolded_value_after_a_bolded_label_is_not_a_heading():
    """The cost of treating two adjacent bold runs as two headings, paid down.

    The relaxation is right for `<strong>Key responsibilities</strong><strong><br>
    </strong>` and wrong the moment an employer bolds a label AND its value: this
    verbatim affirm compensation table otherwise yields sections headed "2", "$16,000-
    $24,000" and "$5,000", and databricks' metadata header yields one headed a
    recruiter's personal name. Two employers, two unrelated block types -- a shape, not
    one template.

    The discriminator is the colon rule at the second boundary, not a new idea: inside a
    leaf a bold is a heading only if it terminates its label; at an adjacency, only if
    the bold BEFORE it did not.
    """
    _text, secs = util.clean_with_sections(
        "<p><strong>Equity grade: </strong><strong>2<br></strong>"
        "<strong>New hire equity: </strong><strong>$16,000-$24,000<br></strong></p>"
    )
    assert [s["header"] for s in secs] == ["Equity grade:", "New hire equity:"], (
        "a bolded value was promoted to a section heading"
    )


def test_a_heading_with_no_letter_or_digit_is_not_a_heading():
    """275 sections corpus-wide were headed by ".", ":", "," or ">>" -- an employer
    bolding a separator. No convention makes that a heading, and a consumer rendering
    a section list gets a row titled "." """
    _text, secs = util.clean_with_sections(
        "<p><strong>.</strong></p><p><strong>Requirements</strong></p><p>Python.</p>"
    )
    assert [s["header"] for s in secs] == [None, "Requirements"]


def test_about_you_is_a_requirements_header_not_a_company_blurb():
    """The ordering bug, pinned. `about_company`'s `^about [a-z]` catches "About You",
    and it did so on 2,085 entries across 82 employers until it moved after
    `requirements`. The real company header must NOT move with it."""
    assert sections.classify("About You") == "requirements"
    assert sections.classify("About you and the role") == "requirements"
    assert sections.classify("About Anthropic") == "about_company"
    assert sections.classify("About Us") == "about_company"
    # THE OTHER HALF, and the larger one. `about_company`'s bare `^about [a-z]` also
    # claimed "About the Role", which is a RESPONSIBILITIES header -- 5,256 entries
    # across 326 employers, 2.5x the "About You" case above. Ordering cannot fix it,
    # because responsibilities runs LAST; only the negative lookahead can. Both guards
    # are load-bearing and this is the one place that says so.
    assert sections.classify("About the Role") == "responsibilities"
    assert sections.classify("About this role") == "responsibilities"
    assert sections.classify("About the job") == "responsibilities"
    assert sections.classify("About the opportunity") == "responsibilities"


@pytest.mark.parametrize(
    "header",
    [
        "You have:",  # 855 entries / 50 employers
        "You bring to the team:",
        "What we are looking for",  # 332 / 50
        "Who we\u2019re looking for",  # 156 / 22 -- CURLY apostrophe, the common form
        "Bonus if:",  # 179 / 60
        "We\u2019d love to hear from you if you have:",  # 94 / 5
    ],
)
def test_the_five_measured_pattern_additions_classify(header):
    """Each was measured as UNCLASSIFIED against a 730-employer corpus rather than
    guessed at, and verified corpus-wide to steal nothing from `responsibilities`."""
    assert sections.classify(header) == "requirements"


def test_an_employer_header_that_means_nothing_stays_unclassified():
    """55% of header entries classify to nothing, and that is the honest answer --
    forcing marketing copy into a bucket asserts something the employer never said."""
    assert sections.classify("Monthly family dinner night") is None
    assert sections.classify("Building something special") is None
    # THE 70-CHARACTER CEILING, and it has to be tested with a string that WOULD
    # otherwise classify -- the first version of this line used `"x" * 80`, which
    # matches no pattern at any length, so it passed with the ceiling raised to 700.
    # One employer's template bolds an entire paragraph; without the ceiling that
    # paragraph becomes a section header.
    long_but_matching = (
        "We are looking for someone with responsibilities spanning the entire "
        "platform organisation and its partner teams"
    )
    assert len(long_but_matching) > 70
    assert sections.classify(long_but_matching[:60]) is not None  # short: classifies
    assert sections.classify(long_but_matching) is None  # long: refused by the ceiling


def test_clean_is_idempotent():
    """Verified on all 809 bodies of a live board; pinned here so a future pass that
    decodes or strips one more time cannot start eating text on re-entry.

    The bound this asserts is TWO layers of escaping, which is what real sources send
    (`&amp;nbsp;` on 711 of 809 bodies, `&amp;amp;` on all 809). A body escaped three
    times would still leave one entity behind for a second call to decode -- that case
    does not occur in the corpus, and chasing it with a fixed-point loop was measured
    and rejected: unbounded decoding destroys prose on 12 of 2,697 bodies.
    """
    once = util.clean(_GREENHOUSE_BODY)
    assert util.clean(once) == once


# ── source parser (the brittle provider-JSON → posting mapping) ───────────────
def test_greenhouse_parser_maps_fields(monkeypatch):
    fake = {
        "jobs": [
            {
                "title": "AI Engineer",
                "location": {"name": "Remote - US"},
                "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
                "updated_at": "2026-07-10T00:00:00-04:00",
                "departments": [{"name": "Engineering"}],
                "content": "<p>Build &amp; ship LLM systems.</p>",
            }
        ]
    }
    monkeypatch.setattr(sources, "get_json", lambda url: fake)
    out = sources.fetch_greenhouse("acme")
    assert len(out) == 1
    j = out[0]
    assert j["title"] == "AI Engineer"
    assert j["location"] == "Remote - US"
    assert j["url"].endswith("/acme/jobs/1")
    # THE FIXTURE CARRIES AN ET OFFSET BECAUSE GREENHOUSE DOES. Measured on 3,064 live
    # date values across three boards: 100% are -04:00/-05:00, 0 are `Z`. So this test
    # pins the guarantee that matters for 68% of the corpus -- an ET-offset instant
    # must NOT shift when `to_date` converts. The conversion path itself is exercised
    # by test_to_date_converts_an_instant_to_eastern..., which has a real `Z` case; a
    # `Z` fixture here would freeze a shape this vendor has never sent and would stop
    # catching the regression that would actually hurt.
    assert j["posted"] == "2026-07-10"
    assert j["team"] == "Engineering"
    assert "&" in j["text"] and "<p>" not in j["text"]  # html unescaped + stripped


# ── engine.harvest end-to-end (monkeypatched sources, no network) ─────────────
def test_harvest_end_to_end(monkeypatch):
    cfg = config.Config(remote_only=True, min_score=0)
    config.set_active(cfg)

    def fake_breadth(queries):
        return [
            {
                "title": "AI Engineer",
                "company": "Acme",
                "location": "Remote",
                "url": "https://x/1",
                "posted": "2026-07-12",
                "text": "Build RAG agentic LLM systems.",
                "source": "fake",
            },
            {  # same role, different source + slight retitle -> should dedup
                "title": "AI Engineer - Remote",
                "company": "Acme",
                "location": "Remote",
                "url": "https://x/2",
                "posted": "2026-07-12",
                "text": "Build RAG agentic LLM systems.",
                "source": "fake2",
            },
            {  # excluded by title
                "title": "Office Manager",
                "company": "Acme",
                "location": "Remote",
                "url": "https://x/3",
                "posted": "2026-07-12",
                "text": "Manage the office.",
                "source": "fake",
            },
        ]

    monkeypatch.setattr(engine, "enabled_depth", lambda c: {})
    monkeypatch.setattr(engine, "enabled_breadth", lambda c: [("fake", fake_breadth)])
    rows, discovered, errors = engine.harvest(cfg, watchlist_path=None)
    titles = {r["title"] for r in rows}
    assert "Office Manager" not in titles  # relevance gate
    ai = [r for r in rows if "AI Engineer" in r["title"]]
    assert len(ai) == 1  # the two AI rows deduped into one
    assert errors == []


def test_google_jobs_wins_canonical_link_on_equal_score_dedup(monkeypatch):
    """When the SAME role is seen from google_jobs and another source at an equal
    fit score, the merged row keeps google_jobs' direct-to-company link — the score
    itself stays source-agnostic; source preference only breaks the tie."""
    cfg = config.Config(remote_only=True, min_score=0)
    config.set_active(cfg)
    body = "Build RAG agentic LLM systems."

    def fake_breadth(queries):
        return [
            {  # aggregator copy — consumed first
                "title": "AI Engineer",
                "company": "Acme",
                "location": "Remote",
                "url": "https://adzuna.example/redirect/1",
                "posted": "2026-07-12",
                "text": body,
                "source": "adzuna",
            },
            {  # google copy, direct-to-company link, same role + same body/score
                "title": "AI Engineer - Remote",
                "company": "Acme",
                "location": "Remote",
                "url": "https://acme.wd5.myworkdayjobs.com/job/AI-Engineer",
                "posted": "2026-07-12",
                "text": body,
                "source": "google_jobs",
            },
        ]

    monkeypatch.setattr(engine, "enabled_depth", lambda c: {})
    monkeypatch.setattr(engine, "enabled_breadth", lambda c: [("fake", fake_breadth)])
    rows, _, _ = engine.harvest(cfg, watchlist_path=None)
    ai = [r for r in rows if "AI Engineer" in r["title"]]
    assert len(ai) == 1  # deduped into one
    assert ai[0]["url"] == "https://acme.wd5.myworkdayjobs.com/job/AI-Engineer"
    assert ai[0]["sources"] == {"adzuna", "google_jobs"}  # both credited


@pytest.mark.parametrize("employer_first", [True, False])
def test_employer_copy_wins_over_a_shorter_aggregator_copy(monkeypatch, employer_first):
    """The merged row must carry the EMPLOYER's ATS link, even though the aggregator's
    stub scores higher.

    This is the product promise ("routes you to the source") and it was inverted:
    the tiebreak led with the fit score, and the score rewarded brevity, so an
    80-word aggregator stub beat the company's own 15,000-character posting. Measured
    on live boards, 70-88% of merged roles handed the user a redirect. Parametrized
    over arrival order because a tiebreak that depends on which fetch finished first
    is not a tiebreak."""
    cfg = config.Config(remote_only=True, min_score=0)
    config.set_active(cfg)
    full = "Build RAG agentic LLM systems. " + ("responsibilities and detail. " * 200)
    employer = {
        "title": "AI Engineer",
        "company": "Acme",
        "location": "Remote",
        "url": "https://boards.greenhouse.io/acme/jobs/1",
        "posted": "2026-07-12",
        "text": full,
        "source": "greenhouse",
    }
    aggregator = {
        "title": "AI Engineer",
        "company": "Acme",
        "location": "Remote",
        "url": "https://remoteok.com/redirect/9999",
        "posted": "2026-07-12",
        "text": "Build RAG agentic LLM systems.",  # the short, score-inflated stub
        "source": "remoteok",
    }
    order = [employer, aggregator] if employer_first else [aggregator, employer]
    # The stub really does score higher — the point is that it must not decide.
    assert (
        scoring.score_and_signals(aggregator, cfg=cfg)[0]
        > scoring.score_and_signals(employer, cfg=cfg)[0]
    )

    monkeypatch.setattr(engine, "enabled_depth", lambda c: {})
    monkeypatch.setattr(
        engine, "enabled_breadth", lambda c: [("fake", lambda q: order)]
    )
    rows, _, _ = engine.harvest(cfg, watchlist_path=None)
    assert len(rows) == 1
    assert rows[0]["url"] == "https://boards.greenhouse.io/acme/jobs/1"
    assert rows[0]["text"] == full  # and the complete body, not the stub
    assert rows[0]["sources"] == {"greenhouse", "remoteok"}


def test_apply_during_a_scan_is_not_lost(tmp_path):
    """`apply` must survive a concurrent scan. Both paths read the whole store,
    change it in memory and write it back, so an unserialized scan that read BEFORE
    the apply put the pre-apply rows back — silently un-applying the role, which is
    the one thing this store exists to prevent."""
    import threading
    import time

    c = _cfg()
    csvp = tmp_path / "s.csv"
    merged = shortlist.upsert(
        csvp, [_post("Acme", "AI Engineer", 40, "u1")], today="2026-07-10"
    )
    rid = merged[0]["id"]

    def slow_scan():  # a scan holding the store across a delay
        with shortlist._exclusive(csvp):
            rows = shortlist.load_all(csvp)
            time.sleep(0.25)
            shortlist.write_all(csvp, rows)

    t = threading.Thread(target=slow_scan)
    t.start()
    time.sleep(0.05)
    shortlist.mark_status(csvp, rid, "applied")
    t.join()
    assert shortlist.load_all(csvp)[0]["status"] == "applied"
    assert c  # cfg fixture used


def test_a_csv_saved_by_excel_still_works(tmp_path):
    """Excel writes a UTF-8 BOM on save. Read as plain utf-8 those bytes join the
    FIRST HEADER NAME, so the column becomes '\\ufeffid', every id lookup misses,
    and apply/dismiss fail with 'no role with id ...' on a file that looks perfect
    in a spreadsheet."""
    _cfg()
    csvp = tmp_path / "s.csv"
    merged = shortlist.upsert(
        csvp, [_post("Acme", "AI Engineer", 40, "u1")], today="2026-07-10"
    )
    rid = merged[0]["id"]
    csvp.write_bytes(b"\xef\xbb\xbf" + csvp.read_bytes())  # what Excel leaves behind
    assert list(shortlist.load_all(csvp)[0])[0] == "id"  # not '﻿id'
    assert shortlist.mark_status(csvp, rid, "applied") is True


def test_config_typo_exits_rather_than_loading_a_neighbour(tmp_path, monkeypatch):
    """A named config that is not there is a typo, not a cue to look elsewhere.
    It used to fall through to ./job-radar.yaml, so a slip in `--config` ran against
    a different config and said nothing."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "job-radar.yaml").write_text(
        "filters:\n  min_score: 99\n", encoding="utf-8"
    )
    with pytest.raises(SystemExit) as e:
        cli._resolve_config(str(tmp_path / "typo.yaml"))
    assert e.value.code == 2
    assert cli._resolve_config(None).min_score == 99  # discovery itself still works


def test_version_flag_reports_the_installed_version(capsys):
    """`--version` is the first thing anyone types at an unfamiliar CLI, and it is
    what a packaging smoke test calls to prove the entry point works at all. It must
    print job_radar.__version__ — the same string setuptools packages — and exit 0."""
    from job_radar import __version__

    with pytest.raises(SystemExit) as e:
        cli.main(["--version"])
    assert e.value.code == 0
    assert capsys.readouterr().out.strip() == f"job-radar {__version__}"


def test_version_flag_does_not_short_circuit_a_subcommand():
    """Attached to the top level only. On `common` it would ride along to every
    subparser, so `job-radar list --version` would print a version and exit 0 —
    looking like the subcommand ran."""
    with pytest.raises(SystemExit) as e:
        cli.main(["list", "--version"])
    assert e.value.code == 2  # argparse: unrecognized argument


def test_scoring_knobs_are_pinned_to_an_absolute_score():
    """A fixed posting must score a fixed number. Every other scoring test asserts a
    RELATIVE property (A > B), which stays true under a global rescale — so
    avg_jd_tokens could be 400 or 16000 and nothing went red. It was 400, wrong by
    4x, for three releases. This is the tripwire for that class of drift."""
    cfg = config.Config()
    config.set_active(cfg)
    p = {
        "title": "AI Engineer",
        "company": "Acme",
        "location": "Remote",
        "text": "Build RAG and agentic LLM systems with retrieval and evals. " * 20,
    }
    score, _ = scoring.score_and_signals(p, cfg=cfg)
    assert score == 41, (
        f"scoring output moved to {score}. If that was deliberate, update this "
        "number and say why in the CHANGELOG — the knobs (avg_jd_tokens, score_k1, "
        "score_len_b, the caps) all land here."
    )


def test_one_malformed_posting_cannot_kill_the_whole_harvest(monkeypatch):
    """A JSON `null` from any of ~500 third parties used to abort the entire run.

    `.get(k, "")` does not protect against it — the default fires only when the key
    is ABSENT, so a present-but-null title yields None and the first `.lower()`
    raises. Worse, `_consume` runs OUTSIDE both of `harvest`'s try blocks, so the
    crash escaped the per-source error handling, threw away a completed network
    harvest, and skipped the keep-your-existing-shortlist guard on the way out."""
    cfg = config.Config(remote_only=True, min_score=0)
    config.set_active(cfg)

    def hostile(queries):
        return [
            # every field null
            {
                "title": None,
                "company": None,
                "location": None,
                "url": None,
                "posted": None,
                "text": None,
                "source": "evil",
            },
            # present but the wrong TYPE — the other half of the same bug
            {
                "title": 123,
                "company": ["a"],
                "location": {"x": 1},
                "url": "https://x/2",
                "posted": 0,
                "text": None,
                "source": "evil",
            },
            # and a perfectly good role behind them, which must still arrive
            {
                "title": "AI Engineer",
                "company": "Acme",
                "location": "Remote",
                "url": "https://x/3",
                "posted": "2026-07-10",
                "text": "Build RAG agentic LLM systems.",
                "source": "evil",
            },
        ]

    monkeypatch.setattr(engine, "enabled_depth", lambda c: {})
    monkeypatch.setattr(engine, "enabled_breadth", lambda c: [("evil", hostile)])
    rows, _, errors = engine.harvest(cfg, watchlist_path=None)
    assert [r["title"] for r in rows] == ["AI Engineer"]
    assert errors == []  # a bad ROW is not a bad SOURCE


def test_harvest_surfaces_broken_source(monkeypatch):
    cfg = config.Config()
    config.set_active(cfg)

    def boom(queries):
        raise KeyError("schema changed")  # a real bug, not a network blip

    monkeypatch.setattr(engine, "enabled_depth", lambda c: {})
    monkeypatch.setattr(engine, "enabled_breadth", lambda c: [("boom", boom)])
    rows, discovered, errors = engine.harvest(cfg, watchlist_path=None)
    assert any("boom" in e for e in errors)  # surfaced, not swallowed as "no jobs"


# ── funnel: grows a real watchlist, never the shipped template ────────────────
def test_append_watchlist_grows_real_file(tmp_path):
    wl = tmp_path / "watchlist.json"
    wl.write_text('{"companies": []}')
    added = funnel.append_watchlist(
        wl, [{"name": "Acme", "ats": "greenhouse", "slug": "acme"}]
    )
    assert added and "Acme" in wl.read_text(encoding="utf-8")


def test_append_watchlist_refuses_template(tmp_path):
    ex = tmp_path / "watchlist.example.json"
    ex.write_text('{"companies": []}')
    added = funnel.append_watchlist(
        ex, [{"name": "Acme", "ats": "greenhouse", "slug": "acme"}]
    )
    assert added == [] and "Acme" not in ex.read_text(
        encoding="utf-8"
    )  # template untouched


# ── C3: a re-titled applied role keeps its status (matched on URL) ────────────
def test_upsert_rematches_retitled_role_by_url(tmp_path):
    csvp = tmp_path / "s.csv"
    merged = shortlist.upsert(
        csvp, [_post("Acme", "AI Engineer", 40, "https://x/1")], today="2026-07-10"
    )
    shortlist.mark_status(csvp, merged[0]["id"], "applied")
    # next run: the recruiter re-titled it (new dedup_key) but the URL is stable
    p2 = _post("Acme", "AI Engineer, Platform Team", 55, "https://x/1")
    merged2 = shortlist.upsert(csvp, [p2], today="2026-07-12")
    applied = [r for r in merged2 if r["status"] == "applied"]
    assert len(applied) == 1  # one row, status preserved (no duplicate 'new' row)
    assert applied[0]["first_seen"] == "2026-07-10"


# ── panel-review punch-list fixes ────────────────────────────────────────────
def test_store_roundtrips_unicode(tmp_path):
    """Non-cp1252 titles (CJK/emoji) survive write+read — the Windows
    UnicodeEncodeError guard (encoding='utf-8' on every file open)."""
    csvp = tmp_path / "s.csv"
    shortlist.upsert(
        csvp,
        [_post("Acme", "Senior AI Engineer 🚀 — 东京", 40, "https://x/1")],
        today="2026-07-10",
    )
    rows = shortlist.load_all(csvp)
    assert any("🚀" in r["title"] and "东京" in r["title"] for r in rows)


def test_total_outage_preserves_store(tmp_path, monkeypatch):
    """Every source failing (0 rows + errors) must NOT overwrite the store (which
    would wipe 'new' roles / reset first_seen) and must exit nonzero."""
    c = _cfg()
    csvp = tmp_path / "shortlist.csv"
    wl = tmp_path / "watchlist.json"
    wl.write_text('{"companies": []}', encoding="utf-8")
    merged = shortlist.upsert(
        csvp,
        [
            _post("Acme", "AI Engineer", 40, "https://x/1"),
            _post("Beta", "AI Engineer", 45, "https://x/2"),
        ],
        today="2026-07-10",
    )
    shortlist.mark_status(csvp, merged[0]["id"], "applied")
    before = csvp.read_bytes()
    monkeypatch.setattr(
        engine, "harvest", lambda cfg, w: ([], [], ["breadth:x: URLError"])
    )
    args = types.SimpleNamespace(
        out=str(csvp), watchlist=str(wl), verbose=False, limit=25
    )
    with pytest.raises(SystemExit) as ei:
        cli.cmd_scan(args, c)
    assert ei.value.code == 1
    assert csvp.read_bytes() == before  # nothing overwritten


def test_list_all_tolerates_garbage_score(tmp_path):
    """`list --all` sorts by _safe_int, so a hand-typed bad score can't crash it."""
    import csv as _csv

    c = _cfg()
    csvp = tmp_path / "s.csv"
    with open(csvp, "w", newline="", encoding="utf-8") as f:
        w = _csv.DictWriter(f, fieldnames=shortlist.COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerow(
            {
                "id": "abc",
                "score": "notanumber",
                "title": "AI Engineer",
                "company": "Acme",
                "status": "new",
                "url": "u",
                "dedup_key": "acme|ai engineer",
            }
        )
    args = types.SimpleNamespace(out=str(csvp), all=True, limit=25)
    cli.cmd_list(args, c)  # must not raise ValueError


def test_corrupt_watchlist_surfaces_error(tmp_path, monkeypatch):
    """A corrupt watchlist is LOUD (an error), and breadth still runs — no silent
    drop of the entire depth harvest."""
    c = _cfg()
    bad = tmp_path / "watchlist.json"
    bad.write_text("{ not valid json", encoding="utf-8")

    def fake_breadth(queries):
        return [
            {
                "title": "AI Engineer",
                "company": "Acme",
                "location": "Remote",
                "url": "https://x/1",
                "posted": "2026-07-12",
                "text": "Build RAG LLM systems.",
                "source": "fake",
            }
        ]

    monkeypatch.setattr(engine, "enabled_depth", lambda cfg: {})
    monkeypatch.setattr(engine, "enabled_breadth", lambda cfg: [("fake", fake_breadth)])
    rows, discovered, errors = engine.harvest(c, watchlist_path=str(bad))
    assert any("watchlist" in e for e in errors)  # surfaced, not swallowed
    assert any(r["company"] == "Acme" for r in rows)  # breadth still ran


def test_fuzzy_dedup_rejects_subset_keeps_reorder():
    """A bare title must not merge into a longer, more-specific one at the same
    company (distinct opening), but a reorder / noise-only retitle still merges."""
    c = _cfg()
    n = dedup.normalize_title
    assert dedup.fuzzy_title_match(n("AI Engineer"), n("AI Engineer - Remote"), c)
    assert not dedup.fuzzy_title_match(n("AI Engineer"), n("AI Engineer, Payments"), c)
    # ...and here is the LIMIT of this layer, pinned so it can't be mistaken for a
    # guarantee again. ", Ads" is the same kind of distinct opening as
    # ", Payments", and the ratio gates MERGE it -- they always did. Payments only
    # separates because its suffix is long enough to drag token_sort_ratio under
    # the floor, so this test appeared to prove a rule ("a bare title doesn't merge
    # into a more specific one") that was never true. String similarity cannot see
    # the difference; only a disqualifier can. The real guarantee now lives in
    # test_same_role_disqualifiers, and this assertion exists to keep anyone from
    # re-reading the line above as broader than it is.
    assert dedup.fuzzy_title_match(n("AI Engineer"), n("AI Engineer, Ads"), c)
    assert not dedup.same_role("AI Engineer", "AI Engineer, Ads")[0]
    assert dedup.fuzzy_title_match(
        n("Senior AI Engineer"), n("AI Engineer (Senior)"), c
    )


# Every case below is a role a user wanted. The 5 splits were all silently merged
# before the disqualifiers landed, and the merge DISCARDS the loser's apply URL,
# so each one was a deleted opening the user never saw.
SAME_ROLE_CASES = [
    ("AI Engineer", "AI Engineer, Ads", False, "short qualifier"),
    ("AI Engineer", "AI Engineer, Payments", False, "long qualifier"),
    ("AI Engineer II", "AI Engineer III", False, "roman level"),
    ("AI Engineer 2", "AI Engineer III", False, "arabic vs roman level"),
    ("AI Engineer (East)", "AI Engineer (West)", False, "parenthetical qualifier"),
    ("Senior AI Engineer", "Staff AI Engineer", False, "seniority"),
    ("AI Engineer", "AI  Engineer", True, "whitespace only"),
    ("AI Engineer - Remote", "Remote AI Engineer", True, "reorder + noise"),
    ("AI Engineer", "AI Engineer", True, "identical"),
    ("Senior AI Engineer", "AI Engineer (Senior)", True, "level in a parenthetical"),
    (
        "Engineer, Machine Learning",
        "Machine Learning Engineer",
        True,
        "comma inversion",
    ),
    ("AI Engineer (Remote)", "AI Engineer", True, "work-arrangement qualifier"),
]


@pytest.mark.parametrize("a,b,want,why", SAME_ROLE_CASES)
def test_same_role_disqualifiers(a, b, want, why):
    got, reason = dedup.same_role(a, b)
    assert got is want, f"{why}: {a!r} vs {b!r} -> {got} ({reason})"


@pytest.mark.parametrize("a,b,want,why", SAME_ROLE_CASES)
def test_same_role_disqualifiers_end_to_end(a, b, want, why):
    """The same cases through the REAL pipeline. Deliberately different sources on
    each side, so the job-id veto cannot be what does the work -- this proves the
    title marks fire on their own. Asserts both postings survive the pre-dedup
    filters first, because otherwise "one hit" would mean "one was filtered out"
    and the test would pass while proving nothing (an earlier draft of this test
    read exactly that way on an (EU) title, which exclude_locations drops)."""
    c = _cfg()
    body = "ai engineer python llm remote"
    ps = [
        {
            "title": a,
            "company": "Acme",
            "location": "Remote",
            "text": body,
            "url": "https://boards.greenhouse.io/acme/jobs/1",
            "source": "greenhouse",
            "posted": "",
        },
        {
            "title": b,
            "company": "Acme",
            "location": "Remote",
            "text": body,
            "url": "https://remoteok.com/l/99",
            "source": "remoteok",
            "posted": "",
        },
    ]
    for p in ps:
        assert scoring.relevant(p["title"], c) and scoring.is_remote(p, c), (
            f"{p['title']!r} never reached dedup -- this test would be vacuous"
        )
    hits: dict = {}
    engine._consume(ps, hits, {}, c, {})
    assert (len(hits) == 1) is want, f"{why}: got {len(hits)} hits"


def test_job_id_veto_splits_two_openings_on_one_board():
    """Two DIFFERENT job ids on the SAME board are two openings, however alike the
    titles read -- the strongest signal available, and one the matcher ignored."""
    c = _cfg()
    gh = "https://boards.greenhouse.io/acme/jobs/"

    def n_hits(u1, u2):
        ps = [
            {
                "title": t,
                "company": "Acme",
                "location": "Remote",
                "text": "ai engineer python llm remote",
                "url": u,
                "source": "greenhouse",
                "posted": "",
            }
            for t, u in (("AI Engineer - Remote", u1), ("Remote AI Engineer", u2))
        ]
        hits: dict = {}
        engine._consume(ps, hits, {}, c, {})
        return len(hits)

    assert n_hits(gh + "7", gh + "7") == 1  # one opening, re-titled
    assert n_hits(gh + "7", gh + "8") == 2  # two openings


def test_job_id_veto_never_blocks_a_cross_source_merge():
    """An aggregator redirect exposes no job id, so the veto stays silent and the
    employer copy still absorbs it. This is the merge the product is built on."""
    c = _cfg()
    ps = [
        {
            "title": "AI Engineer",
            "company": "Acme",
            "location": "Remote",
            "text": "ai engineer python llm remote",
            "url": u,
            "source": s,
            "posted": "",
        }
        for u, s in (
            ("https://boards.greenhouse.io/acme/jobs/7", "greenhouse"),
            ("https://remoteok.com/l/99", "remoteok"),
        )
    ]
    hits: dict = {}
    engine._consume(ps, hits, {}, c, {})
    assert len(hits) == 1
    assert "greenhouse.io" in next(iter(hits.values()))["url"]


def test_job_ref_returns_none_rather_than_guessing():
    """None disables the veto. An unrecognized URL must degrade to the old
    behaviour, never to a fabricated id that would split a real duplicate."""
    assert dedup.job_ref("https://example.com/careers/123") is None
    assert dedup.job_ref("https://boards.greenhouse.io/acme") is None  # no job id
    assert dedup.job_ref("") is None
    assert dedup.job_ref("https://boards.greenhouse.io/acme/jobs/42") == (
        "greenhouse",
        "acme",
        "42",
    )


def test_title_score_double_count_is_capped():
    """A keyword-stuffed TITLE can't run away — its double-count is bounded by
    title_score_cap."""
    stuffed = {
        "title": "AI Engineer LLM RAG Agentic Founding Remote Senior",
        "location": "Remote",
        "text": "x",
        "company": "Acme",
    }
    s0 = scoring.score(stuffed, config.Config(title_score_cap=0))
    s12 = scoring.score(stuffed, config.Config(title_score_cap=12))
    s100 = scoring.score(stuffed, config.Config(title_score_cap=100))
    assert s12 - s0 <= 12  # the cap bounds the title bonus
    assert s12 < s100  # ...and it actually bites on a stuffed title


def test_seed_degrades_gracefully(tmp_path, monkeypatch):
    """A Common Crawl hiccup raises a clean SeedError (not a raw traceback), and
    `seed` exits nonzero without crashing."""

    def boom():
        raise seed.SeedError("Common Crawl index unavailable (URLError)")

    monkeypatch.setattr(seed, "_latest_cdx", boom)
    with pytest.raises(seed.SeedError):
        seed.seed_universe("greenhouse", tmp_path / "wl.json", limit=10)

    args = types.SimpleNamespace(
        ats="greenhouse", max=10, verify=False, watchlist=str(tmp_path / "wl.json")
    )
    with pytest.raises(SystemExit) as ei:
        cli.cmd_seed(args, _cfg())
    assert ei.value.code == 1


# ── panel re-review follow-up (F1-F5 + minors) ───────────────────────────────
def _rand_posting(rnd, vocab):
    def words(k):
        return " ".join(rnd.choice(vocab) for _ in range(rnd.randint(0, k)))

    return {
        "title": words(6),
        "location": words(3),
        "text": words(40),
        "company": words(3),
    }


def test_scoring_matches_bruteforce_reference():
    """F3 equivalence GATE: the optimized score_and_signals must be byte-identical
    (score AND signal string) to the brute-force per-keyword reference for every
    posting. If this ever fails, the optimization changed the results and must not
    ship."""
    import random
    import re as _re

    c = _cfg()
    fw = c.fit_weights

    def ref(p):
        blob = (
            f"{p.get('title', '')} {p.get('location', '')} {p.get('text', '')}".lower()
        )
        bh = [(w, kw) for kw, w in fw.items() if util.has(kw, blob)]
        raw = sum(w for w, _ in bh)
        dl = len(_re.findall(r"[a-z0-9]+", blob))
        norm = (1 - c.score_len_b) + c.score_len_b * (dl / c.avg_jd_tokens)
        # BM25 with tf == 1 (presence-based scoring), matching production. Kept as
        # the explicit closed form rather than calling into scoring.py — a reference
        # that imports the thing it is checking proves nothing.
        body = min(
            raw * (c.score_k1 + 1) / (1 + c.score_k1 * norm) if norm > 0 else raw,
            c.blob_score_cap,
        )
        tl = p.get("title", "").lower()
        body += min(
            sum(w for kw, w in fw.items() if util.has(kw, tl)), c.title_score_cap
        )
        body -= sum(w for kw, w in c.title_penalty.items() if util.has(kw, tl))
        ab = f"{p.get('company', '')} {p.get('text', '')}".lower()
        # CAPPED, matching production. This line read `body -= sum(...)` — uncapped —
        # for the whole life of the agency_penalty_cap change, and the gate still
        # passed, because the vocab below could not produce a single agency keyword.
        # Forced to one, production scored 49 against this reference's -44.
        body -= min(
            sum(w for kw, w in c.agency_penalty.items() if util.has(kw, ab)),
            c.agency_penalty_cap,
        )
        sig = ", ".join(kw for _, kw in sorted(bh, reverse=True)[:7])
        return round(body), sig

    # The corpus has to be able to REACH every branch the reference models. It could
    # not: none of these words is an agency_penalty key ("staff" is not "staffing" —
    # `has` matches whole words), so the agency term was 0 on both sides of every one
    # of the 2000 comparisons and the branch was never compared at all. The agency
    # phrases below are what make this an equivalence gate rather than a ritual.
    vocab = list(fw) + [
        "python",
        "remote",
        "the",
        "platform",
        "payments",
        "nurse",
        "staff",
        "ai-first",
        "multi-agent",
        "ml/ai",
        "onsite",
        "systems",
        "team",
        # agency_penalty keys — singles and multi-word, so both the set-membership
        # and the first-token-prefilter paths in `_present` are exercised.
        "staffing",
        "recruiter",
        "c2c",
        "consultancy",
        "our client",
        "staffing agency",
        "staff augmentation",
        "contract-to-hire",
        "multiple clients",
        "talent solutions",
        "consulting firm",
        "staffing firm",
        # title_penalty keys, for the same reason.
        "research scientist",
        "ai researcher",
        "member of technical staff",
    ]
    rnd = random.Random(1234)
    for _ in range(2000):
        p = _rand_posting(rnd, vocab)
        assert scoring.score_and_signals(p, cfg=c) == ref(p)


def test_seed_wraps_real_connection_reset(monkeypatch):
    """F1: a real mid-stream connection reset (http.client.RemoteDisconnected, NOT a
    URLError) must become a clean SeedError — the gap that shipped a raw traceback."""
    import http.client

    def raiser(*a, **k):
        raise http.client.RemoteDisconnected("Remote end closed connection")

    monkeypatch.setattr(seed.urllib.request, "urlopen", raiser)
    with pytest.raises(seed.SeedError):
        seed.enumerate_tokens("greenhouse")


def test_smartrecruiters_url_has_no_hardcoded_query(monkeypatch):
    """F4: SmartRecruiters must fetch generically (relevance gate filters), not
    server-side filter to q=AI."""
    captured = {}

    def fake_get_json(url):
        captured["url"] = url
        return {"content": []}

    monkeypatch.setattr(sources, "get_json", fake_get_json)
    sources.fetch_smartrecruiters("acme")
    assert "q=AI" not in captured["url"]
    assert "acme" in captured["url"]


def test_remote_negation_in_title_or_location():
    """Minor: a negated title/location ('Non-remote', 'Onsite only') is not remote."""
    assert (
        scoring.remote_posting("Non-remote AI Engineer", "United States", "") is False
    )
    assert scoring.remote_posting("AI Engineer", "Onsite only", "") is False
    assert scoring.remote_posting("Remote AI Engineer", "United States", "") is True


def test_apply_bad_id_exits_nonzero(tmp_path):
    """Minor: apply/dismiss on a non-existent id exits nonzero (script-detectable)."""
    csvp = tmp_path / "s.csv"
    shortlist.upsert(csvp, [_post("Acme", "AI Engineer", 40, "u1")], today="2026-07-10")
    args = types.SimpleNamespace(out=str(csvp), id="ZZZZZZZ")
    with pytest.raises(SystemExit) as ei:
        cli.cmd_status(args, _cfg(), "applied")
    assert ei.value.code == 1


def test_fmt_shows_tier_tag():
    """F5: the tiers knob now drives a visible tier tag on each surfaced role."""
    c = _cfg()  # tier_strong=30, tier_look=22
    strong = {"id": "a", "score": "35", "title": "AI Engineer", "company": "Acme"}
    look = {"id": "b", "score": "25", "title": "AI Engineer", "company": "Acme"}
    plain = {"id": "c", "score": "10", "title": "AI Engineer", "company": "Acme"}
    assert "strong" in cli._fmt(strong, c)
    assert "worth a look" in cli._fmt(look, c)
    assert "strong" not in cli._fmt(plain, c) and "worth a look" not in cli._fmt(
        plain, c
    )


# ── deferred minors (atomic writes, --strict, SSRF guards) ───────────────────
def test_atomic_write_text_roundtrip_no_leftover(tmp_path):
    """util.atomic_write_text writes via a unique temp + os.replace and leaves no
    stray .tmp behind (so overlapping runs can't collide on a fixed name)."""
    p = tmp_path / "sub" / "f.json"
    util.atomic_write_text(p, '{"a": 1}\n')
    assert p.read_text(encoding="utf-8") == '{"a": 1}\n'
    assert list(p.parent.glob("*.tmp")) == []


def test_strict_exits_nonzero_on_partial_failure(tmp_path, monkeypatch):
    """--strict turns any source error into a nonzero exit (for scheduled runs);
    the same run without --strict exits 0."""
    c = _cfg()
    csvp = tmp_path / "s.csv"
    wl = tmp_path / "watchlist.json"
    wl.write_text('{"companies": []}', encoding="utf-8")
    row = _post("Acme", "AI Engineer", 40, "https://x/1")
    monkeypatch.setattr(
        engine, "harvest", lambda cfg, w: ([row], [], ["breadth:x: URLError"])
    )
    base = dict(out=str(csvp), watchlist=str(wl), verbose=False, limit=25)
    with pytest.raises(SystemExit) as ei:
        cli.cmd_scan(types.SimpleNamespace(strict=True, **base), c)
    assert ei.value.code == 1
    cli.cmd_scan(types.SimpleNamespace(strict=False, **base), c)  # no raise


def test_braintrust_does_not_follow_offsite_next(monkeypatch):
    """SSRF guard: a `next` URL pointing off Braintrust's host is never fetched."""
    calls = []

    def fake_get_json(url):
        calls.append(url)
        if len(calls) == 1:
            return {"results": [], "next": "https://evil.example.com/api/jobs/?p=2"}
        return {"results": [], "next": None}

    monkeypatch.setattr(sources, "get_json", fake_get_json)
    sources.search_braintrust(["ai"])
    assert len(calls) == 1  # stopped after page 1; off-site next not chased
    assert all("evil.example.com" not in u for u in calls)


def test_invalid_slug_is_rejected(tmp_path, monkeypatch):
    """A malformed watchlist slug (path traversal) is skipped with an error and
    never reaches the fetcher; valid slugs still run."""
    c = _cfg()
    called = []

    def fake_fetch(slug):
        called.append(slug)
        return []

    monkeypatch.setattr(engine, "enabled_depth", lambda cfg: {"greenhouse": fake_fetch})
    monkeypatch.setattr(engine, "enabled_breadth", lambda cfg: [])
    wl = tmp_path / "watchlist.json"
    wl.write_text(
        '{"companies": ['
        '{"name": "Bad", "ats": "greenhouse", "slug": "../../etc"},'
        '{"name": "Good", "ats": "greenhouse", "slug": "anthropic"}]}',
        encoding="utf-8",
    )
    rows, discovered, errors = engine.harvest(c, watchlist_path=str(wl))
    assert any("invalid slug" in e for e in errors)
    assert "../../etc" not in called  # never reached the fetcher
    assert "anthropic" in called  # valid slug still fetched


# ── harvest takes DATA, and writes nothing ───────────────────────────────────
def _depth_only(monkeypatch, captured):
    """Stub one depth adapter; disable breadth. captured collects the slugs fetched."""

    def fake(slug, **kw):
        captured.append((slug, kw))
        return [
            {
                "title": "AI Engineer",
                "location": "Remote",
                "url": f"https://x/{slug}",
                "posted": "2026-07-20",
                "text": "python",
                "salary": "",
            }
        ]

    monkeypatch.setattr(engine, "enabled_depth", lambda c: {"greenhouse": fake})
    monkeypatch.setattr(engine, "enabled_breadth", lambda c: [])


def test_harvest_accepts_a_company_array(monkeypatch):
    """jobfitr keeps its universe in SQLite, so the engine must take DATA, not a path."""
    c = _cfg()
    got = []
    _depth_only(monkeypatch, got)
    rows, discovered, errors = engine.harvest(
        c,
        companies=[
            {"name": "Anthropic", "ats": "greenhouse", "slug": "anthropic"},
            {"name": "Figma", "ats": "greenhouse", "slug": "figma"},
        ],
    )
    assert sorted(s for s, _ in got) == ["anthropic", "figma"]
    assert len(rows) == 2 and not errors


def test_harvest_still_reads_a_watchlist_file(tmp_path, monkeypatch):
    """The standalone CLI passes a path; that must keep working."""
    import json as _json

    c = _cfg()
    got = []
    _depth_only(monkeypatch, got)
    wl = tmp_path / "watchlist.json"
    wl.write_text(
        _json.dumps(
            {"companies": [{"name": "Figma", "ats": "greenhouse", "slug": "figma"}]}
        )
    )
    rows, _, errors = engine.harvest(c, str(wl))
    assert [s for s, _ in got] == ["figma"] and len(rows) == 1 and not errors


def test_an_explicit_empty_company_list_is_not_a_missing_one(monkeypatch):
    """companies=[] must mean 'no depth companies', not 'fall back to the file'."""
    c = _cfg()
    got = []
    _depth_only(monkeypatch, got)
    rows, _, _ = engine.harvest(c, "/nonexistent/watchlist.json", companies=[])
    assert got == [] and rows == []


def test_harvest_writes_no_files(tmp_path, monkeypatch):
    """REGRESSION: the engine used to append discovered companies straight into the
    caller's watchlist.json. A library must not silently write a file the caller owns
    — and a store-backed caller had nowhere for them to go."""
    import json as _json

    c = _cfg()
    c.funnel_auto_grow = True
    _depth_only(monkeypatch, [])
    monkeypatch.setattr(
        engine,
        "funnel",
        lambda *a, **k: [{"name": "New Co", "ats": "greenhouse", "slug": "newco"}],
    )
    wl = tmp_path / "watchlist.json"
    original = _json.dumps({"companies": []})
    wl.write_text(original)

    rows, discovered, errors = engine.harvest(c, str(wl))
    assert [d["slug"] for d in discovered] == ["newco"], "must RETURN what it found"
    assert wl.read_text(encoding="utf-8") == original, (
        "engine must not have written the watchlist"
    )


def test_cli_scan_persists_discovered_companies(tmp_path, monkeypatch):
    """The persistence the engine gave up has to land in the CLI, or the standalone
    tool silently stops growing its own watchlist."""
    import json as _json

    wl = tmp_path / "watchlist.json"
    wl.write_text(_json.dumps({"companies": []}))
    found = [{"name": "New Co", "ats": "greenhouse", "slug": "newco"}]
    monkeypatch.setattr(engine, "harvest", lambda *a, **k: ([], found, []))
    monkeypatch.setattr(cli.shortlist, "load_all", lambda p: [])

    args = types.SimpleNamespace(
        watchlist=str(wl),
        out=str(tmp_path / "s.csv"),
        limit=10,
        config=None,
        verbose=False,
        strict=False,
    )
    cli.cmd_scan(args, _cfg())
    assert [
        c["slug"] for c in _json.loads(wl.read_text(encoding="utf-8"))["companies"]
    ] == ["newco"]


# ── 0.4.0: liveness, the corrupt-watchlist crash, seed consolidation ─────────
def test_corrupt_watchlist_does_not_discard_a_good_harvest(tmp_path, monkeypatch):
    """REGRESSION: json.JSONDecodeError is a ValueError, not an OSError, so a corrupt
    watchlist.json escaped cmd_scan's handler — AFTER the whole network harvest and
    BEFORE the shortlist write. A file we only wanted to APPEND to could throw away
    the entire run's results."""
    c = _cfg()
    csvp = tmp_path / "s.csv"
    wl = tmp_path / "watchlist.json"
    wl.write_text('{"companies": [], "trunc', encoding="utf-8")  # truncated mid-write
    row = _post("Acme", "AI Engineer", 40, "https://x/1")
    monkeypatch.setattr(
        engine,
        "harvest",
        lambda cfg, w: ([row], [{"name": "New", "ats": "lever", "slug": "new"}], []),
    )
    cli.cmd_scan(
        types.SimpleNamespace(
            out=str(csvp), watchlist=str(wl), verbose=False, limit=25, strict=False
        ),
        c,
    )
    # The harvest survived the broken watchlist.
    assert csvp.exists(), "the shortlist was never written"
    assert "AI Engineer" in csvp.read_text(encoding="utf-8")


def test_funnel_confirms_with_liveness_not_a_full_harvest(monkeypatch):
    """The funnel only ever needed to know whether a discovered slug resolves to >=1
    role. It was downloading the company's entire board to find out."""
    c = _cfg()
    seen = []
    monkeypatch.setattr(
        funnel, "liveness_for", lambda ats: lambda slug, **kw: seen.append(slug) or 3
    )
    monkeypatch.setattr(
        sources, "fetch_lever", lambda *a, **k: pytest.fail("full adapter called")
    )
    posts = [_post("Newco", "AI Engineer", 40, "https://jobs.lever.co/newco/1")]
    added = funnel.funnel(posts, set(), set(), c)
    assert [e["slug"] for e in added] == ["newco"]
    assert seen == ["newco"]


def test_funnel_drops_a_slug_with_no_live_roles(monkeypatch):
    c = _cfg()
    monkeypatch.setattr(funnel, "liveness_for", lambda ats: lambda slug, **kw: 0)
    posts = [_post("Newco", "AI Engineer", 40, "https://jobs.lever.co/newco/1")]
    assert funnel.funnel(posts, set(), set(), c) == []


def test_funnel_stops_at_the_probe_budget_not_the_candidate_count(monkeypatch):
    """Dead candidates must stop costing requests once the probe budget is spent.

    `max_new_per_run` counts SUCCESSES, so a run where every slug is dead never
    incremented it and never hit its break — the funnel probed every candidate,
    serially, on every scan, with auto_grow on by default. 150 dead candidates
    measured at ~60 seconds of third-party traffic to add zero companies."""
    c = config.Config(funnel_max_probes_per_run=10, funnel_max_new_per_run=25)
    config.set_active(c)
    probed = []

    def dead(ats):
        def probe(slug, **kw):
            probed.append(slug)
            return 0  # every slug is dead — nothing is ever "added"

        return probe

    monkeypatch.setattr(funnel, "liveness_for", dead)
    posts = [
        _post(f"Co{i}", "AI Engineer", 40, f"https://jobs.lever.co/co{i}/1")
        for i in range(150)
    ]
    assert funnel.funnel(posts, set(), set(), c) == []
    assert len(probed) == 10, f"probed {len(probed)} of 150 — budget not enforced"


def test_funnel_probe_budget_does_not_cap_a_healthy_run_early(monkeypatch):
    """The budget must bound WASTE, not discovery: with live slugs, the run should
    still reach max_new_per_run rather than stopping at the probe budget."""
    c = config.Config(funnel_max_probes_per_run=50, funnel_max_new_per_run=5)
    config.set_active(c)
    monkeypatch.setattr(funnel, "liveness_for", lambda ats: lambda slug, **kw: 7)
    posts = [
        _post(f"Live{i}", "AI Engineer", 40, f"https://jobs.lever.co/live{i}/1")
        for i in range(40)
    ]
    assert len(funnel.funnel(posts, set(), set(), c)) == 5


def test_seed_writes_the_workday_triple(tmp_path, monkeypatch):
    """Workday needs tenant + host + site to be fetchable at all; seeding only the
    slug would write a company the adapter can never resolve."""
    wl = tmp_path / "watchlist.json"
    wl.write_text('{"companies": []}', encoding="utf-8")
    monkeypatch.setattr(
        seed,
        "enumerate_entries",
        lambda ats, max_rows=20000: [
            {"ats": "workday", "slug": "3m", "host": "wd1", "site": "Search"}
        ],
    )
    assert seed.seed_universe("workday", wl, limit=5, verify=False) == 1
    got = json.loads(wl.read_text(encoding="utf-8"))["companies"][0]
    assert (got["ats"], got["slug"], got["host"], got["site"]) == (
        "workday",
        "3m",
        "wd1",
        "Search",
    )


def test_seed_verify_probes_concurrently_and_stops_at_the_limit(tmp_path, monkeypatch):
    """The old --verify loop was serial AND used a full harvest fetch per board.
    It now batches through discover.probe, keeping the same early stop."""
    wl = tmp_path / "watchlist.json"
    wl.write_text('{"companies": []}', encoding="utf-8")
    entries = [{"ats": "greenhouse", "slug": f"co{i}"} for i in range(50)]
    monkeypatch.setattr(seed, "enumerate_entries", lambda a, max_rows=20000: entries)
    probed = []

    def fake_probe(batch, workers=8):
        probed.extend(e["slug"] for e in batch)
        return [{**e, "roles": 1, "outcome": "ok"} for e in batch]

    monkeypatch.setattr(seed.discover, "probe", fake_probe)
    assert seed.seed_universe("greenhouse", wl, limit=3, verify=True) == 3
    assert len(json.loads(wl.read_text(encoding="utf-8"))["companies"]) == 3
    assert probed, "probe was never called — verify silently did nothing"


def test_seed_rejects_an_ats_it_cannot_mine():
    with pytest.raises(ValueError, match="seed not supported"):
        seed.enumerate_entries("myspace")


# ── the store's encoding ──────────────────────────────────────────────────────
def test_utf16_store_loads_instead_of_killing_every_command(tmp_path):
    """Excel's "Unicode Text" save writes UTF-16. That used to raise a bare
    UnicodeDecodeError out of load_all -- which sits on the path of EVERY command,
    so `list`, `apply` and `dismiss` all died and the user could not reach their
    own file to fix it."""
    p = tmp_path / "shortlist.csv"
    p.write_text("id,score,status\nabc,30,new\n", encoding="utf-16")
    rows = shortlist.load_all(p)
    assert rows == [{"id": "abc", "score": "30", "status": "new"}]


def test_utf8_and_bom_stores_are_unaffected(tmp_path):
    """The Excel UTF-8-BOM path this function already handled must not regress."""
    for enc in ("utf-8", "utf-8-sig"):
        p = tmp_path / f"s-{enc}.csv"
        p.write_text("id,score\nabc,30\n", encoding=enc)
        assert shortlist.load_all(p) == [{"id": "abc", "score": "30"}]


def test_undecodable_store_fails_loud_but_useful(tmp_path):
    """Fail fast -- but name the file and the fix, not a byte offset."""
    p = tmp_path / "shortlist.csv"
    p.write_bytes(b"id,score\n\xff\xfe\x00garbage\x81\x8d,30\n")
    with pytest.raises(shortlist.ShortlistEncodingError) as e:
        shortlist.load_all(p)
    assert str(p) in str(e.value)
    assert "CSV UTF-8" in str(e.value)


def test_cli_prints_advice_for_an_unreadable_store(tmp_path, capsys):
    """The whole point: the user gets a sentence they can act on, exit code 1, and
    no traceback -- on a command that never even needed to parse the file."""
    p = tmp_path / "shortlist.csv"
    p.write_bytes(b"id,score\n\xff\xfe\x00garbage\x81\x8d,30\n")
    with pytest.raises(SystemExit) as e:
        cli.main(["list", "--out", str(p)])
    assert e.value.code == 1
    assert "CSV UTF-8" in capsys.readouterr().out


# ── the claims the README makes about scoring ─────────────────────────────────
def test_repeating_a_keyword_can_never_raise_the_score():
    """The anti-keyword-stuffing guarantee — stated as what is actually true.

    The README used to sell "term-frequency saturation (score_k1)", which was
    false: `_present` returns each keyword at most once, so tf is pinned at 1 and
    there is no term frequency left to saturate. score_k1 is a gain knob on length
    normalization.

    The real property is one-directional. Repeating a keyword cannot ADD score
    (each counts once), and because repetition lengthens the document it is mildly
    penalised by length normalization — measured 26 / 26 / 25 / 22 at 1x / 5x /
    50x / 500x. So the honest claim is "stuffing never pays", not "score is
    invariant"; the first draft of this test asserted invariance and went red,
    correctly."""
    c = config.Config()
    scores = [
        scoring.score(
            {
                "title": "AI Engineer",
                "location": "Remote",
                "text": "ai engineer python " + "rag " * n,
            },
            c,
        )
        for n in (1, 5, 50, 500)
    ]
    assert scores == sorted(scores, reverse=True), (
        f"repeating a keyword RAISED the score: {scores} — stuffing pays, which is "
        "the whole thing presence-based scoring exists to prevent."
    )


def test_score_k1_is_a_gain_knob_not_a_saturation_knob():
    """...and score_k1 does something real, so it isn't dead config either."""
    p = {"title": "AI Engineer", "location": "Remote", "text": "ai engineer python rag"}
    lo = scoring.score(p, config.Config(score_k1=0.1))
    hi = scoring.score(p, config.Config(score_k1=100))
    assert lo < hi


def test_research_title_penalties_are_reachable():
    """Reported as unreachable dead config; they are not, and the fix was to prove
    it rather than delete them. A BARE "Research Scientist" never reaches scoring
    because `relevant()` filters it first — but the prefixed forms that DO pass the
    relevance gate are exactly where the penalty is meant to bite."""
    c = config.Config()
    for kw in (
        "research scientist",
        "quantitative researcher",
        "member of technical staff",
    ):
        assert not scoring.relevant(kw, c), f"{kw!r} unexpectedly passes the gate bare"
        title = f"AI {kw.title()}"
        assert scoring.relevant(title, c), f"{title!r} should reach scoring"
        p = {"title": title, "location": "Remote", "text": "ai engineer python"}
        plain = {
            "title": "AI Engineer",
            "location": "Remote",
            "text": "ai engineer python",
        }
        assert scoring.score(p, c) < scoring.score(plain, c), (
            f"the {kw!r} penalty never fired on {title!r} — it really is unreachable"
        )


# ── HTTP connection pooling ───────────────────────────────────────────────────
class _FakeResp:
    def __init__(self, status=200, body=b"{}", headers=None):
        self.status, self._b = status, body
        self.reason, self.headers = "OK", headers or {}

    def read(self):
        return self._b

    def getheader(self, k):
        return self.headers.get(k)


class _FakeConn:
    """Counts how many CONNECTIONS were constructed, which is the thing pooling is
    supposed to reduce — not how many requests were made."""

    made: list = []

    def __init__(self, host, timeout=None):
        self.host, self.requests, self.closed = host, 0, False
        _FakeConn.made.append(self)

    # The script is a SHARED sequence of outcomes consumed across connections, not
    # copied per connection — otherwise a scripted failure would repeat on the
    # retry connection too and no retry could ever be observed to succeed.
    def request(self, method, target, body=None, headers=None):
        self.requests += 1
        if _FakeConn.script and _FakeConn.script[0] == "boom":
            _FakeConn.script.pop(0)
            raise ConnectionResetError("server closed an idle keep-alive socket")

    def getresponse(self):
        return _FakeResp(**(_FakeConn.script.pop(0) if _FakeConn.script else {}))

    def close(self):
        self.closed = True


@pytest.fixture
def fake_http(monkeypatch):
    _FakeConn.made = []
    _FakeConn.script = []
    monkeypatch.setattr(util.http.client, "HTTPSConnection", _FakeConn)
    monkeypatch.setattr(util.http.client, "HTTPConnection", _FakeConn)
    monkeypatch.setattr(util._POOL, "conns", {}, raising=False)
    config.set_active(config.Config())
    return _FakeConn


def test_same_host_requests_reuse_one_connection(fake_http):
    """The whole point: ~500 companies on a handful of ATS hosts stop paying a TLS
    handshake each. Measured 149ms cold vs 84ms reused."""
    for _ in range(5):
        util.get_json("https://boards.greenhouse.io/v1/boards/acme/jobs")
    assert len(fake_http.made) == 1
    assert fake_http.made[0].requests == 5


def test_different_hosts_do_not_share_a_connection(fake_http):
    for url in ("https://a.example/x", "https://b.example/y", "https://a.example/z"):
        util.get_json(url)
    assert len(fake_http.made) == 2  # a, b — and a was reused


def test_a_stale_keepalive_socket_retries_once_on_a_fresh_connection(fake_http):
    """A server closing an idle connection must look like nothing to the caller.
    Without this, pooling would invent outages that read as flaky job boards."""
    # The connection is handed a dead socket on first use, then a good response.
    fake_http.script = ["boom", {"body": b'{"ok": true}'}]
    assert util.get_json("https://a.example/x") == {"ok": True}  # caller sees nothing
    assert len(fake_http.made) == 2  # one fresh connection, exactly one retry
    assert fake_http.made[0].closed  # the dead one was dropped, not left in the pool
    assert util._POOL.conns[("https", "a.example")] is fake_http.made[1]


def test_http_errors_still_arrive_as_urllib_HTTPError(fake_http):
    """`engine._fetch_company` reads `.code` off this, and `discover.probe` branches
    on 401/403/404/429. Swapping the transport must not change the exception type."""
    fake_http.script = [{"status": 404}]
    with pytest.raises(urllib.error.HTTPError) as e:
        util.get_json("https://a.example/missing")
    assert e.value.code == 404


def test_network_failures_still_arrive_as_NET_ERRORS(fake_http):
    """Every source has `except NET_ERRORS` around its fetch. http.client raises
    OSError subclasses, which that tuple does NOT catch — so an untranslated
    failure would escape as an unhandled crash instead of "this source is down"."""
    fake_http.script = ["boom", "boom"]
    with pytest.raises(util.NET_ERRORS):
        util.get_json("https://a.example/x")


def test_redirects_are_followed(fake_http):
    """urllib followed them, so any source whose API redirects would silently break
    if the pooled client didn't."""
    fake_http.script = [
        {"status": 302, "headers": {"Location": "https://b.example/moved"}},
        {"body": b'{"moved": true}'},
    ]
    assert util.get_json("https://a.example/x") == {"moved": True}


def test_the_pool_is_thread_local(fake_http):
    """Connection reuse ACROSS threads is how pooling corrupts responses. Two
    threads must never be handed the same connection."""
    import threading

    seen = []

    def hit():
        util.get_json("https://a.example/x")
        seen.append(id(util._POOL.conns[("https", "a.example")]))

    ts = [threading.Thread(target=hit) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(set(seen)) == 4


def test_the_pool_is_bounded(fake_http):
    """A long run must not accumulate descriptors without limit."""
    for i in range(util._POOL_MAX + 4):
        util.get_json(f"https://h{i}.example/x")
    assert len(util._POOL.conns) <= util._POOL_MAX


# ── funnel: bounded, concurrent probing ───────────────────────────────────────
def _breadth(n):
    return [
        {
            "company": f"Co{i}",
            "title": "AI Engineer",
            "url": f"https://boards.greenhouse.io/co{i}/jobs/1",
        }
        for i in range(n)
    ]


def test_dead_candidates_stop_at_the_probe_budget(monkeypatch):
    """The budget counts ATTEMPTS. It used to count SUCCESSES, so a run where every
    candidate was dead never incremented it and probed all 150 serially — ~60
    seconds of someone else's rate limit to add zero companies."""
    calls = []
    monkeypatch.setattr(funnel, "liveness_for", lambda ats: lambda s: calls.append(s))
    cfg = config.Config(funnel_max_probes_per_run=10, funnel_max_new_per_run=25)
    assert funnel.funnel(_breadth(150), set(), set(), cfg) == []
    assert len(calls) == 10, f"probed {len(calls)} dead candidates, budget was 10"


def test_live_candidates_stop_at_the_new_company_budget(monkeypatch):
    monkeypatch.setattr(funnel, "liveness_for", lambda ats: lambda s: 3)
    cfg = config.Config(funnel_max_probes_per_run=50, funnel_max_new_per_run=5)
    assert len(funnel.funnel(_breadth(80), set(), set(), cfg)) == 5


def test_probing_is_concurrent_but_never_exceeds_the_budget(monkeypatch):
    """Concurrency here is only safe BECAUSE the budget bounds it: the slice is
    taken before the pool runs, so parallel probing adds wall-clock speed and zero
    extra load on third-party boards."""
    import threading
    import time

    peak, cur, lock = [0], [0], threading.Lock()

    def probe(_slug):
        with lock:
            cur[0] += 1
            peak[0] = max(peak[0], cur[0])
        time.sleep(0.02)  # hold the slot so genuine overlap is observable
        with lock:
            cur[0] -= 1
        return 1

    monkeypatch.setattr(funnel, "liveness_for", lambda ats: probe)
    cfg = config.Config(funnel_max_probes_per_run=20, funnel_max_new_per_run=25)
    funnel.funnel(_breadth(60), set(), set(), cfg)
    assert peak[0] > 1, "probes ran serially — the parallelism is not there"


def test_dry_run_never_probes(monkeypatch):
    def boom(ats):
        raise AssertionError("a dry run must not touch a third-party board")

    monkeypatch.setattr(funnel, "liveness_for", boom)
    out = funnel.funnel(_breadth(3), set(), set(), config.Config(), dry=True)
    assert len(out) == 3


def test_funnel_does_not_import_its_way_into_a_cycle():
    """discover.py must not import funnel — reusing a parallel prober across the two
    would deadlock imports."""
    import ast

    src = (Path(funnel.__file__).parent / "discover.py").read_text(encoding="utf-8")
    imported = {
        n.module or ""
        for n in ast.walk(ast.parse(src))
        if isinstance(n, ast.ImportFrom)
    } | {
        a.name
        for n in ast.walk(ast.parse(src))
        if isinstance(n, ast.Import)
        for a in n.names
    }
    assert not any("funnel" in m for m in imported), imported


# ── harvest: bounded memory ───────────────────────────────────────────────────
def test_harvest_bounds_the_results_it_holds_in_memory(monkeypatch):
    """`ex.map` submits every company up front, so completed results PILE UP
    unconsumed — all ~500 full sets of job descriptions alive at once, measured
    ~1.25 GB peak RSS for work that only ever needs a couple of dozen boards.

    The guarantee is SCALE INVARIANCE: how much is held at once is a function of
    the window and the pool, not of how many companies you have. So this measures
    the peak fetched-but-unconsumed count at two very different universe sizes and
    requires it not to grow with n. Two earlier versions of this test were worse
    and both are worth naming: one asserted "at most 12 fetches in flight", which
    the worker pool guarantees on its own and which PASSED with the window
    sabotaged to a billion; the next asserted a hand-guessed constant (40) and
    went red in CI at 45 — a correct number, since the true bound is the window
    plus whatever completed while the main thread was consuming. A guessed
    threshold tests the guess. This tests the property."""
    cfg = config.Config(remote_only=False, min_score=0)
    config.set_active(cfg)
    threading = __import__("threading")

    def run(n):
        lock = threading.Lock()
        made, eaten, gap, seen = [0], [0], [0], []

        def fake(slug, **kw):
            if slug == "c7":
                raise ValueError("boom")
            out = [
                {
                    "title": f"AI Engineer {slug}",
                    "url": f"https://boards.greenhouse.io/{slug}/jobs/1",
                    "posted": "",
                    "text": "ai engineer",
                    "location": "Remote",
                }
            ]
            with lock:
                seen.append(slug)
                made[0] += 1
                gap[0] = max(gap[0], made[0] - eaten[0])
            return out

        real_consume = engine._consume

        def counting_consume(postings, *a):
            with lock:
                eaten[0] += 1
            return real_consume(postings, *a)

        monkeypatch.setattr(engine, "_consume", counting_consume)
        monkeypatch.setattr(engine, "enabled_depth", lambda c: {"greenhouse": fake})
        monkeypatch.setattr(engine, "enabled_breadth", lambda c: [])
        companies = [
            {"name": f"C{i}", "ats": "greenhouse", "slug": f"c{i}"} for i in range(n)
        ]
        rows, _, errors = engine.harvest(cfg, companies=companies)
        assert sorted(seen) == sorted(c["slug"] for c in companies if c["slug"] != "c7")
        assert any("c7" in e for e in errors)  # a failing board still surfaces
        assert len(rows) == n - 1
        return gap[0]

    small, large = run(200), run(1600)
    assert large < small * 2, (
        f"held {small} results at n=200 but {large} at n=1600 — memory is scaling "
        "with the universe again, which is the 1.25 GB bug."
    )
    assert large < 200, f"held {large} results at once; the window is 24 + a pool of 12"


def test_starter_watchlist_ships_a_working_workday_triple():
    """Workday shipped as an enabled adapter with ZERO companies to poll for three
    releases, so the enterprise lane the README sells was dark out of the box.

    It is the one ATS whose key cannot be hand-written -- tenant + wdN host shard +
    site slug, and the site slug is unguessable ("NVIDIAExternalCareerSite"). So the
    starter list has to carry real triples or the adapter is unreachable until a user
    runs `seed`. This pins that they are present and SHAPED correctly; liveness is the
    canary's job, not a hermetic test's.
    """
    import json

    doc = json.loads(cli._packaged("watchlist.example.json"))
    wd = [c for c in doc["companies"] if c.get("ats") == "workday"]
    assert wd, "the starter watchlist ships no Workday companies — the lane is dark"
    for c in wd:
        missing = [f for f in sources.DEPTH_EXTRA_FIELDS["workday"] if not c.get(f)]
        assert not missing, f"{c['name']}: workday entry missing {missing}"
    # The _comment tells a hand-editing user which ATS values are legal; it listed
    # five of eight while three adapters were shipping.
    for ats in sources.DEPTH_ALL:
        assert ats in doc["_comment"], f"_comment does not mention the {ats} adapter"


def test_every_starter_entry_declares_its_required_fields():
    """engine._fetch_company fails LOUD on a missing extra field rather than fetching
    a wrong-but-valid URL, so a malformed starter entry is a silent dead company."""
    import json

    doc = json.loads(cli._packaged("watchlist.example.json"))
    for c in doc["companies"]:
        assert c.get("ats") in sources.DEPTH_ALL, f"{c} names an unregistered ats"
        assert c.get("slug"), f"{c} has no slug"
        for f in sources.DEPTH_EXTRA_FIELDS.get(c["ats"], ()):
            assert c.get(f), f"{c['name']} ({c['ats']}) is missing {f}"


def test_harvest_leaks_no_private_keys(monkeypatch):
    """`_blk` and `_nt` are de-dup scratch — a company block and a normalized title
    that `_consume` stashes so the fuzzy pass does not re-derive them per comparison.

    They were reaching every consumer. jobfitr stores what `harvest` returns, so two
    private keys were crossing a package boundary; anything a consumer can read, it
    can come to depend on, and then it is public whether it was meant to be or not.
    """
    from job_radar import engine, sources

    c = _cfg()
    c.remote_only = False
    c.breadth_sources = []
    monkeypatch.setattr(
        sources,
        "get_json",
        lambda u, *a, **k: {
            "totalFound": 1,
            "content": [{"name": "AI Engineer", "id": "1"}],
        },
    )
    monkeypatch.setattr(sources.time, "sleep", lambda s: None)
    rows, _, _ = engine.harvest(
        c, companies=[{"name": "Visa", "ats": "smartrecruiters", "slug": "Visa"}]
    )
    assert rows, "no rows to check"
    private = [k for r in rows for k in r if k.startswith("_")]
    assert not private, (
        f"private keys crossed the package boundary: {sorted(set(private))}"
    )


# ── the record shape: dedup key, locations, title decomposition ─────────────
def test_two_cities_are_two_jobs():
    """MEASURED, not assumed. Without location in the key, every opening a company
    posts under one title in several cities collapses to one row and the rest are
    discarded with their apply URLs. On a live Greenhouse board (databricks, 810
    postings, 2026-08-05) that was 250 postings gone, 83 of them distinct jobs —
    "Delivery Solutions Architect" was eight openings across eight countries and
    seven vanished."""
    austin = {
        "company": "Acme",
        "title": "AI Engineer",
        "location": "Austin, TX",
        "url": "https://x/1",
    }
    nyc = {
        "company": "Acme",
        "title": "AI Engineer",
        "location": "New York, NY",
        "url": "https://x/2",
    }
    assert dedup.dedup_key(austin) != dedup.dedup_key(nyc)


def test_work_arrangement_words_do_not_split_a_place():
    """ "Remote - India" and "India" are the same place. If work-arrangement words
    discriminated in the key, a source that decorates its location string would
    split a role away from a source that does not."""
    a = {
        "company": "Acme",
        "title": "Engineer",
        "location": "Remote - India",
        "url": "",
    }
    b = {"company": "Acme", "title": "Engineer", "location": "India", "url": ""}
    assert dedup.dedup_key(a) == dedup.dedup_key(b)


def test_one_posting_in_several_cities_stays_one_row():
    """The other half of the same problem. Greenhouse separates several places on
    ONE posting with `;` — 143 of 810 on the measured board. That is one job id, so
    it must stay one row; only the FIRST place enters the key and the rest ride in
    `locations`."""
    p = engine._coerce(
        {
            "company": "Acme",
            "title": "Account Executive",
            "location": "Bengaluru, India; Mumbai, India",
            "url": "https://x/1",
        }
    )
    assert [x["raw"] for x in p["locations"]] == ["Bengaluru, India", "Mumbai, India"]
    assert dedup.normalize_location(p["location"]) == "bengaluru india"


def test_gh_jid_is_recognised_on_a_custom_domain():
    """Greenhouse customers routinely serve the board from their own domain —
    `databricks.com/company/careers/open-positions/job?gh_jid=8559344002`. Because
    ats_from_url only matched boards.greenhouse.io, job_ref returned None there, so
    the "two different ids are two openings" veto was silently OFF on the largest
    boards in the corpus. The parameter is unambiguous wherever it appears."""
    u = "https://databricks.com/company/careers/open-positions/job?gh_jid=8559344002"
    assert dedup.ats_from_url(u) is None  # still not a board URL — slug is unknowable
    assert dedup.job_ref(u) == ("greenhouse", "", "8559344002")


def test_two_job_ids_at_one_company_are_two_rows():
    """The strongest evidence available, and it used to veto only the FUZZY pass
    while the exact-key branch merged the same postings anyway."""
    a = {
        "company": "Databricks",
        "title": "Solutions Architect",
        "location": "United States",
        "url": "https://databricks.com/careers/job?gh_jid=8559344002",
    }
    b = {
        "company": "Databricks",
        "title": "Solutions Architect",
        "location": "United States",
        "url": "https://databricks.com/careers/job?gh_jid=8428882002",
    }
    assert dedup.dedup_key(a) != dedup.dedup_key(b)


def test_a_url_without_an_id_still_merges_across_sources():
    """The id is additive, never required. An aggregator redirect carries no id and
    must still match the employer's copy — that cross-source merge is the product."""
    ats = {
        "company": "Acme",
        "title": "AI Engineer",
        "location": "Austin, TX",
        "url": "https://boards.greenhouse.io/acme/jobs/123",
    }
    agg = {
        "company": "Acme",
        "title": "AI Engineer",
        "location": "Austin, TX",
        "url": "https://remoteok.com/l/999",
    }
    assert dedup.dedup_key(agg).count("|") == 2  # no id component
    assert dedup.dedup_key(ats) != dedup.dedup_key(agg)  # ats copy carries its id


def test_title_root_separates_decoration_from_the_role():
    from job_radar import vocab

    d = vocab.decompose_title("Senior AI Engineer II, Ads (Remote)")
    assert d["title_root"] == "AI Engineer"
    assert d["seniority"] == "senior"
    assert d["title_level"] == "II"
    assert "ads" in d["title_qualifiers"]


def test_a_rank_word_that_is_the_job_is_not_stripped():
    """The rule that makes the parse safe: a seniority word is decoration ONLY when
    it leads the title and something real follows. Without the head-noun test,
    "Director of Engineering" becomes "of Engineering" and "Associate" becomes "" —
    a wrong root silently mis-matches every consumer that groups on it."""
    from job_radar import vocab

    for t in (
        "Director of Engineering",
        "Team Lead",
        "Chief of Staff",
        "VP of Sales",
        "Associate",
        "Principal",
        "Lead",
    ):
        assert vocab.decompose_title(t)["title_root"] == t, f"{t} lost its head noun"
    # ...but a genuine leading modifier still comes off
    assert vocab.decompose_title("Senior Director of Data Science")["title_root"] == (
        "Director of Data Science"
    )


def test_hybrid_is_expressible_and_unknown_is_not_onsite():
    """THREE states, in one field. A `remote` bool sat beside `remote_type` until
    0.9.0 and could only express two of them, so every hybrid role read as `false` --
    true, and indistinguishable from on-site to anything that only checked the flag."""
    r = engine._coerce({"title": "Engineer", "remote_type": "hybrid"})
    assert r["remote_type"] == "hybrid"
    assert engine._coerce({"title": "Engineer"})["remote_type"] is None  # != "onsite"
    assert "remote" not in r and "remote" not in engine._coerce({"title": "E"})


def test_absent_optional_text_is_none_not_empty_string():
    """`posted: ""` meant "we could not parse a date", which a consumer cannot tell
    from a job that has no date — the same lie as remote: False for unknown."""
    r = engine._coerce({"title": "Engineer", "posted": "", "salary": "", "text": ""})
    assert r["posted"] is None and r["salary"] is None and r["text"] is None
    assert r["title"] == "Engineer"  # required fields stay strings


# ── harvest depth: one named block instead of nine env vars (Strand G) ───────
def test_harvest_depth_is_settable_from_yaml(tmp_path):
    """The reason this block exists: the nine ceilings were module-level constants
    read at IMPORT time, so a YAML file — parsed later — could never set any of them.
    Tuning a harvest meant knowing nine undocumented environment variable names."""
    p = tmp_path / "c.yaml"
    p.write_text(
        "sources:\n"
        "  harvest_depth:\n"
        "    workday_max_pages: 3\n"
        "    workday_fetch_details: false\n"
        "    hn_threads: 1\n",
        encoding="utf-8",
    )
    hd = config.load_config(p).harvest_depth
    assert hd.workday_max_pages == 3
    assert hd.workday_fetch_details is False
    assert hd.hn_threads == 1
    assert hd.themuse_max_pages == 5, "an unset key must keep its default"


def test_env_vars_still_drive_the_defaults(monkeypatch):
    """The env vars were the ONLY interface before, so they keep working — the block
    takes them as its defaults rather than replacing them."""
    monkeypatch.setenv("WORKDAY_MAX_PAGES", "7")
    monkeypatch.setenv("RIPPLING_FETCH_DETAILS", "0")
    hd = config.HarvestDepth()
    assert hd.workday_max_pages == 7
    assert hd.rippling_fetch_details is False


def test_a_typo_in_a_depth_key_is_reported_not_silently_ignored(tmp_path, capsys):
    """Silence would be the worst option: a typo'd ceiling reads as "that setting had
    no effect", which is indistinguishable from a quiet job market. It warns and
    continues rather than raising, matching how this loader treats every bad input —
    a malformed config must not crash the CLI."""
    p = tmp_path / "c.yaml"
    p.write_text(
        "sources:\n  harvest_depth:\n    workday_max_page: 3\n", encoding="utf-8"
    )
    cfg = config.load_config(p)
    assert cfg.harvest_depth.workday_max_pages == 25, "the typo must not take effect"
    assert "workday_max_page" in capsys.readouterr().err


def test_every_depth_ceiling_is_reachable_through_the_block():
    """No ceiling may go back to hiding in a module constant: `sources._depth` is the
    only reader, so anything it asks for must exist on the block."""
    import inspect
    import re as _re

    from job_radar import sources as _sources

    asked = set(_re.findall(r'_depth\("([a-z_]+)"\)', inspect.getsource(_sources)))
    have = set(vars(config.HarvestDepth()))
    assert asked, "no depth lookups found — did the accessor get renamed?"
    assert asked <= have, f"sources asks for {sorted(asked - have)}, not on the block"


def test_employment_type_raw_never_holds_a_value_the_vendor_did_not_send():
    """Panel P7. `employment_type_raw` is documented as "what the vendor actually
    said", and the back-fill wrote the NORMALIZED value into it whenever raw was
    absent — so a row claimed a vendor quotation that never existed.

    USAJOBS is the real case: it maps PositionSchedule Code "1" to FULL_TIME itself,
    and `.Name` is empty on 47 of 50 measured rows. Absent is honest; inventing a
    quotation is the one thing a provenance field cannot do."""
    r = engine._coerce(
        {
            "title": "Engineer",
            "company": "Acme",
            "url": "https://example.com/1",
            "source": "usajobs",
            "employment_type": "FULL_TIME",  # already normalized by the adapter
            "employment_type_raw": None,
        }
    )
    assert r["employment_type"] == "FULL_TIME"
    assert r["employment_type_raw"] is None, "invented a vendor quotation"

    # A genuine vendor string still round-trips into the raw.
    r2 = engine._coerce(
        {
            "title": "Engineer",
            "company": "Acme",
            "url": "https://example.com/2",
            "source": "greenhouse",
            "employment_type": "Full-time",
        }
    )
    assert r2["employment_type"] == "FULL_TIME"
    assert r2["employment_type_raw"] == "Full-time"


# ── contract consistency, found by live probe across all 19 sources 2026-08-05 ──
def test_country_is_one_vocabulary_no_matter_which_adapter_filled_it():
    """Live probe found THREE vocabularies in one column: UPPER alpha-2 from most
    sources, LOWERCASE from smartrecruiters ('de', 'us' — 79/100 rows) and DISPLAY
    NAMES from workable ('United States', 28/28). Grouping by country split every
    country into pieces."""
    for raw, want in (("de", "DE"), ("United States", "US"), ("US", "US")):
        r = engine._coerce(
            {
                "title": "E",
                "company": "A",
                "url": "https://x/1",
                "source": "smartrecruiters",
                "country": raw,
            }
        )
        assert r["country"] == want, f"{raw!r} -> {r['country']!r}"


def test_employment_type_is_none_when_the_source_said_nothing():
    """It was `""` on 680 of 2,747 live rows (24%) — 100% of greenhouse, remoteok and
    teamtailor — and `""` is not a member of the closed vocabulary. Invisible in
    NDJSON because emit masks it with `or None`; only the flat dict a library consumer
    receives carried it, which is exactly who this contract is for."""
    r = engine._coerce(
        {"title": "E", "company": "A", "url": "https://x/1", "source": "greenhouse"}
    )
    assert r["employment_type"] is None
    assert r["employment_type"] != ""


def test_every_locations_element_has_the_same_keys():
    """644 of 3,153 live elements were `{raw, url}` only — no city/state/country key
    at all — so a consumer doing `l["city"]` raised on a fifth of the list."""
    multi = engine._coerce(
        {
            "title": "E",
            "company": "A",
            "url": "https://x/1",
            "source": "greenhouse",
            "location": "Waco, TX; Dublin, Ireland",
        }
    )
    single = engine._coerce(
        {
            "title": "E",
            "company": "A",
            "url": "https://x/2",
            "source": "greenhouse",
            "location": "Waco, TX",
        }
    )
    want = {"raw", "city", "state", "country"}
    for row in (multi, single):
        for el in row["locations"]:
            assert set(el) == want, f"element keys {sorted(el)} != {sorted(want)}"
    # Each place is parsed from ITS OWN string, not copied from the first.
    assert multi["locations"][1]["country"] == "IE"


def test_geography_is_derived_from_the_location_string_when_a_source_sends_none():
    """greenhouse filled city/state/country on 0 of 396 live rows while the strings it
    emits parse on 358 of them; rippling 0 of 193 where all 193 parse. One fallback
    here fixes every adapter at once instead of six times."""
    r = engine._coerce(
        {
            "title": "E",
            "company": "A",
            "url": "https://x/1",
            "source": "greenhouse",
            "location": "Sydney, Australia",
        }
    )
    assert (r["city"], r["country"]) == ("Sydney", "AU")

    # It may only ADD. A source that sent real structured geography always wins.
    keep = engine._coerce(
        {
            "title": "E",
            "company": "A",
            "url": "https://x/2",
            "source": "ashby",
            "location": "Sydney, Australia",
            "city": "Melbourne",
            "state": None,
            "country": "AU",
        }
    )
    assert keep["city"] == "Melbourne", "overwrote a source's own structured value"

    # An unreadable location stays None rather than becoming a guess.
    unknown = engine._coerce(
        {
            "title": "E",
            "company": "A",
            "url": "https://x/3",
            "source": "greenhouse",
            "location": "Kuala Lumpur",
        }
    )
    assert unknown["city"] is None and unknown["country"] is None


def test_salary_basis_values_are_inside_the_closed_vocabulary():
    """`vocab.google_salary` emits `parsed`, which was outside SALARY_BASES — the
    frozenset and the caller had been renamed apart. Checked through the FUNCTIONS
    rather than by grepping literals, which is how the mismatch survived a
    source-reading test."""
    assert vocab.salary(1, 2, "USD", "year")["salary_basis"] in vocab.SALARY_BASES
    got = vocab.google_salary("47–55 an hour")
    assert got["salary_basis"] in vocab.SALARY_BASES, got["salary_basis"]


def test_a_us_state_is_always_a_two_letter_code():
    """`state` is deliberately two vocabularies — a code inside the US, the source's
    own subdivision name outside it, because 'Greater London' and 'Attica' have no
    code. But the US half of that rule was FALSE on 580 measured rows: ashby sent
    'California' 518 times, so `state='California'` and `state='CA'` named the same
    place in one column and a US-state filter missed 566 of ashby's 737 rows."""
    r = engine._coerce(
        {
            "title": "E",
            "company": "A",
            "url": "https://x/1",
            "source": "ashby",
            "city": "San Francisco",
            "state": "California",
            "country": "US",
        }
    )
    assert r["state"] == "CA"

    # Outside the US, untouched — there is nothing to map to.
    uk = engine._coerce(
        {
            "title": "E",
            "company": "A",
            "url": "https://x/2",
            "source": "ashby",
            "state": "Greater London",
            "country": "GB",
        }
    )
    assert uk["state"] == "Greater London"

    # May only canonicalize, never discard: an unrecognised US subdivision (a county,
    # a metro) survives rather than being nulled.
    odd = engine._coerce(
        {
            "title": "E",
            "company": "A",
            "url": "https://x/3",
            "source": "adzuna",
            "state": "Cook County",
            "country": "US",
        }
    )
    assert odd["state"] == "Cook County"


def test_no_file_is_read_without_an_explicit_encoding():
    """Windows defaults to cp1252, not UTF-8, so a bare `read_text()` is a platform
    bug that only ever fires on one third of the CI matrix.

    This shipped: `tests/test_attribution.py` read `catalog/*.md` with no encoding and
    all four Windows cells failed with
    `UnicodeDecodeError: 'charmap' codec can't decode byte 0x81` — one byte, in one
    catalog profile, invisible on macOS and Linux. The catalog checkers had the same
    bug and were never reached, because pytest fails a job before them.

    Enforced by reading the source so the next one fails here, on any platform,
    instead of on someone's release day.
    """
    import re as _re
    from pathlib import Path as _Path

    root = _Path(__file__).resolve().parents[1]
    offenders = []
    for py in (
        list(root.glob("job_radar/*.py"))
        + list(root.glob("catalog/*.py"))
        + list(root.glob("tests/*.py"))
    ):
        src = py.read_text(encoding="utf-8")
        for i, line in enumerate(src.split("\n"), 1):
            if _re.search(r"\.read_text\(\s*\)", line):
                offenders.append(f"{py.relative_to(root)}:{i}  {line.strip()}")
    assert not offenders, (
        "read_text() with no encoding (cp1252 on Windows):\n" + "\n".join(offenders)
    )


# ── salary_from_display: the parser that made README:41's promise true ──────────
# Before this, `salary_basis="parsed"` was true on 12 rows corpus-wide while 3,500
# carried a fully formed pay range in `salary` with every structured column null.


def test_multiplier_distributes_leftward_or_salary_min_is_wrong_by_1300x():
    """`$200-260K` is 200,000-260,000. The K is written once and distributes left.

    THE REGRESSION THIS PINS: `_G_NUM` binds the multiplier per-number, so the
    unguarded pattern reads lo=200, hi=260,000 -- a `salary_min` of 200 on a $200K
    job, in the column README:39 calls what the employer COMMITTED to. 26 rows of
    the 2026-08-20 harvest. Asserted on the VALUE, not on "it parsed": a test that
    only checked salary_min is not None passes against the bug.
    """
    got = vocab.salary_from_display("$200-260K", period="year")
    assert got["salary_min"] == 200_000.0, got
    assert got["salary_max"] == 260_000.0, got
    # The guard must not touch a range that needs no help.
    plain = vocab.salary_from_display("$150,000 - $250,000", period="year")
    assert plain["salary_min"] == 150_000.0 and plain["salary_max"] == 250_000.0


def test_absurd_ratio_is_refused_rather_than_parsed():
    """`$150,000 - 250,000k` is a quarter of a BILLION. A wrong number carrying
    basis='parsed' is worse than the null it replaced, so the parse is refused."""
    assert vocab.salary_from_display("$150,000 - 250,000k")["salary_min"] is None
    assert vocab.salary_from_display("$306 - $390,000")["salary_min"] is None


def test_small_figures_need_a_stated_period_or_are_refused():
    """`$30-120` is an hourly rate or a thousands-shorthand and nothing in the string
    can tell. Emitting it period-less manufactures exactly the unusable rows this
    change exists to remove."""
    assert vocab.salary_from_display("$30-120")["salary_min"] is None
    with_period = vocab.salary_from_display("$30-120", period="hour")
    assert with_period["salary_min"] == 30.0 and with_period["salary_period"] == "hour"


def test_magnitude_vetoes_a_stated_period_it_cannot_believe():
    """A real Greenhouse posting labels a $140,000-$220,000 band an 'Estimated Hourly
    Pay Range'. Believing it writes `hour` on a six-figure salary and every aggregate
    downstream is silently wrong -- the exact harm vocab.salary()'s docstring names.

    Refusing a STATED period on magnitude is disbelieving a witness; INVENTING one
    from magnitude is guessing, and that stays forbidden."""
    got = vocab.salary_from_display("$140,000 — $220,000", period="hour")
    assert got["salary_min"] == 140_000.0
    assert got["salary_period"] is None, "an hourly six-figure band was believed"


def test_a_period_is_still_never_invented():
    """The rule that survives all of the above: no period in the text, no period in
    the record. vocab.salary() -- 'A period is never guessed.'"""
    assert vocab.salary_from_display("$200,000 - $250,000")["salary_period"] is None


def test_a_lone_figure_is_not_a_range():
    """`up to $200,000` and `$120,000` cannot be split into a floor and a ceiling,
    and writing a ceiling into salary_min is the failure this refuses outright."""
    assert vocab.salary_from_display("$120,000")["salary_min"] is None
    assert vocab.salary_from_display("up to $200,000")["salary_min"] is None


def test_currency_and_period_come_from_the_text_beside_the_figure():
    """The employer states the currency next to the number; reading it is not
    inference. `country` is the obvious alternative and is measurably wrong: on the
    3,500 target rows, country='CA' rows whose body names one code say CAD on 107 and
    USD on 18 -- a Canadian-located job paying USD is real, so a country rule is
    wrong in BOTH directions. 823 rows have no country at all."""
    p = {
        "salary": "$168,000 — $231,000",
        "text": "The range for candidates located in Canada is between: "
        "$168,000 — $231,000 CAD The Okta Experience is...",
    }
    engine.derive_salary(p)
    assert p["salary_currency"] == "CAD", p
    assert p["salary_min"] == 168_000.0

    q = {
        "salary": "$135,575 — $175,450",
        "text": "Learn more about our total rewards below. Annual Base Salary "
        "$135,575 — $175,450 USD Total Rewards...",
    }
    engine.derive_salary(q)
    assert q["salary_currency"] == "USD" and q["salary_period"] == "year"


def test_two_currencies_in_the_window_is_a_refusal_not_a_coin_flip():
    """A posting quoting a US band and a Canada band contains both codes. A
    document-wide scan takes whichever comes first and calls it evidence."""
    p = {
        "salary": "$100,000 — $150,000",
        "text": "US $100,000 — $150,000 USD or in Canada the equivalent in CAD.",
    }
    engine.derive_salary(p)
    assert p["salary_min"] == 100_000.0
    assert p["salary_currency"] is None, "picked one of two candidates"


def test_a_model_prediction_is_never_written_into_the_commitment_columns():
    """CAUGHT BY MEASURING, NOT BY REVIEW. This once wrote 109106.0 into salary_min
    with basis='parsed' on a row whose 109106.69 was a model output.

    THE MECHANISM CHANGED AT 0.9.0 AND THE PROTECTION DID NOT. It used to rest on the
    salary_estimated_* columns: `_adzuna_pay` put a prediction there, and
    `derive_salary` returned early when it saw one. Those columns were removed because
    nothing downstream ever read them -- NOT for being empty; they read 0 of 102,799
    only on a harvest with no Adzuna keys, while the live consumer's store holds 6,633.
    So the guard is now the EMPTY DISPLAY STRING. A
    predicted row carries `salary: ""`, and `derive_salary` returns at its display
    check before it can parse anything. Same property, one fewer column, and this test
    exists to keep it true however it is implemented."""
    p = {
        "salary": "",  # what _adzuna_pay emits for a predicted row
        "text": "$109,106\u2013$109,106 a year",
    }
    engine.derive_salary(p)
    assert p.get("salary_min") is None, "a prediction reached the commitment column"
    assert p.get("salary_basis") is None

def test_derive_salary_fills_but_never_overwrites_a_vendors_own_figures():
    """A vendor's structured object is better evidence than our parse of its prose,
    and basis='stated' must keep meaning what it says."""
    p = {
        "salary": "$1 - $2",
        "text": "$1 - $2",
        "salary_min": 200_000.0,
        "salary_max": 250_000.0,
        "salary_basis": "stated",
    }
    engine.derive_salary(p)
    assert p["salary_min"] == 200_000.0 and p["salary_basis"] == "stated"


def test_a_point_value_is_not_rendered_as_a_range():
    """220 of 220 Adzuna predictions rendered as `$188,569–$188,569`; a reader sees a
    range and reads a precise employer offer."""
    from job_radar.util import salary_range

    assert salary_range(109106, 109106) == "$109,106"
    assert salary_range(165000, 225000) == "$165,000–$225,000"


def test_a_hyphenated_quantity_is_not_a_pay_period():
    """FOUND BY HAND-READING OUTPUT, not by review: a real Greenhouse body reads
    '$186,400 — $233,000 USD PLEASE NOTE: our policy requires a 90-day waiting
    period', and `day` was harvested out of '90-day' -- $186,400 PER DAY. 66 of 872
    period assignments (7.6%) were absurd this way: 55 day, 9 week, 2 month.

    Asserted on `_adjacent_evidence` DIRECTLY, and on a magnitude the implied-annual
    veto cannot rescue. The obvious version of this test -- a six-figure band next to
    "90-day" -- passes with the hyphen guard REMOVED, because the veto catches it
    downstream for an unrelated reason. It would have pinned nothing.
    """
    # $45/hr is ~$11,700/yr if read as daily: plausible enough to survive the veto,
    # so only the hyphen guard can stop `day` here.
    _, period = engine._adjacent_evidence(
        "the rate is $45 - $65 USD. Our policy requires a 90-day waiting period.",
        "$45 - $65",
    )
    assert period is None, "harvested `day` out of `90-day`"

    # And end to end on the row that exposed it.
    p = {
        "salary": "$186,400 — $233,000",
        "text": "the range for this position is: $186,400 — $233,000 USD PLEASE NOTE: "
        "Our policy requires a 90-day waiting period before reconsideration.",
    }
    engine.derive_salary(p)
    assert p["salary_min"] == 186_400.0
    assert p["salary_period"] is None


def test_an_absurd_implied_annual_vetoes_the_period_whatever_the_unit():
    """The first version of this veto covered `hour` and `year` and missed day, week
    and month entirely. Implied-annual is the general form; per-unit thresholds only
    cover the units someone thought of."""
    for unit in ("day", "week", "month"):
        got = vocab.salary_from_display("$216,000 — $270,000", period=unit)
        assert got["salary_min"] == 216_000.0
        assert got["salary_period"] is None, f"believed {unit} on a six-figure band"
    # A genuinely large ANNUAL package is not vetoed for being large.
    big = vocab.salary_from_display("$1,400,000 - $1,900,000", period="year")
    assert big["salary_period"] == "year"


def test_the_period_is_read_from_slash_notation_too():
    """`/hr` is 30 of 4,703 display strings. 28 were rescued only because the body
    happened to spell the unit out nearby; reading the string does not need luck."""
    assert vocab.salary_from_display("$63-84/hr")["salary_period"] == "hour"
    assert vocab.salary_from_display("$120 - $170 /hour")["salary_period"] == "hour"


# ── title decomposition ────────────────────────────────────────────────────────
# `decompose_title` READS dedup._TITLE_NOISE / _QUAL_RE and must never edit them:
# `_TITLE_NOISE` is used by `different_openings` (dedup.py:244), so a change there
# moves MERGES, and a wrong merge deletes a job. Every table below is local to vocab.


def test_the_delimiter_no_longer_decides_whether_decoration_is_captured():
    """Comma-delimited titles had decoration captured on 99.9% (3,262 rows); dash-,
    pipe- and colon-delimited titles on 17.2% (1,388). Same decoration, opposite
    handling, decided by which key the employer pressed. The dash was not merely
    uncaptured -- it was DELETED and the tail welded into the root, producing a role
    name no employer wrote."""
    dash = vocab.decompose_title("Staff Software Engineer- Foundation Model Inference")
    comma = vocab.decompose_title("Staff Software Engineer, Foundation Model Inference")
    assert dash["title_root"] == comma["title_root"] == "Software Engineer"
    assert "foundation model inference" in (dash["title_qualifiers"] or [])

    tokyo = vocab.decompose_title("Forward Deployed Engineer - Tokyo")
    assert tokyo["title_root"] == "Forward Deployed Engineer"
    assert tokyo["title_qualifiers"] == ["tokyo"]
    assert (
        vocab.decompose_title("AI Engineer | Platform")["title_root"] == "AI Engineer"
    )



def test_title_root_never_manufactures_a_word_no_employer_wrote():
    """`vocab._WORD_RE` is the tokenizer AND `title_root` is rebuilt by joining what it
    finds, so a character absent from the class is DELETED and the word splits at the
    hole. Until 0.9.0 the class was ASCII-only and `Sênior` came out `S nior` -- the
    only field in the record that manufactured text rather than dropping it.

    THIS TEST EXISTED NOWHERE BEFORE. The full suite was green on both sides of this
    bug, so the fix arrived with no coverage at all and these assertions are the first
    thing that would notice it coming back.
    """
    # the bug itself, in four scripts an employer actually used
    for title, root in [
        ("Sênior Security Engineer", "Sênior Security Engineer"),
        ("Développeur Full Stack", "Développeur Full Stack"),
        ("Ingénieur DevOps", "Ingénieur DevOps"),
        ("Müşteri Temsilcisi", "Müşteri Temsilcisi"),
    ]:
        assert vocab.decompose_title(title)["title_root"] == root, title

    # NFD input reaches the same answer. `Sênior` decomposed is `S` + `e` + U+0302,
    # and without the combining range in the class it splits at the mark -- the exact
    # bug above, arriving through an encoding the corpus happens not to contain.
    import unicodedata

    nfd = unicodedata.normalize("NFD", "Sênior Security Engineer")
    assert nfd != "Sênior Security Engineer"  # the fixture really is decomposed
    assert vocab.decompose_title(nfd)["title_root"] == nfd

    # LATIN ONLY, ON PURPOSE. A bilingual title must still root at its English half:
    # the ASCII-only class was accidentally an English extractor, and widening to a
    # Unicode-wide `\w` regressed 85 titles by welding the other script back in.
    assert vocab.decompose_title("Data Engineer / データエンジニア")["title_root"] == (
        "Data Engineer"
    )
    assert vocab.decompose_title("Amazon Account Manager 亚马逊运营经理")[
        "title_root"
    ] == "Amazon Account Manager"

    # `+#/&` are load-bearing and predate all of this; `_` is still a separator, and
    # `×`/`÷` sit inside the Latin-1 letter block but are operators, not letters.
    assert vocab._WORD_RE.findall("C++ C# and/or R&D") == ["C++", "C#", "and/or", "R&D"]
    assert vocab._WORD_RE.findall("data_engineer") == ["data", "engineer"]
    assert vocab._WORD_RE.findall("3 × 4 ÷ 2") == ["3", "4", "2"]

    # HONEST LIMIT: this fixes the manufactured string, NOT the seniority. `_SENIORITY`
    # is keyed on ASCII, so `sênior` is still not a member.
    assert vocab.decompose_title("Sênior Security Engineer")["seniority"] is None
    assert vocab.decompose_title("Senior Security Engineer")["seniority"] == "senior"


def test_an_intra_word_hyphen_is_not_a_delimiter():
    """222 corpus titles carry one. Splitting on a bare hyphen shatters `Full-Stack`
    and `Go-to-Market`; requiring whitespace on BOTH sides misses the 122 titles
    written `Engineer- Backend` or `Engineer -Backend`. Whitespace on at least one
    side is the measured test."""
    assert vocab.decompose_title("Full-Stack Engineer")["title_root"] == (
        "Full Stack Engineer"
    )
    assert vocab.decompose_title("Go-to-Market Engineer")["title_root"] == (
        "Go to Market Engineer"
    )
    # one-sided space still splits
    assert (
        vocab.decompose_title("Sr Software Engineer -Public Sector")["title_root"]
        == "Software Engineer"
    )


def test_a_leading_segment_that_names_no_role_is_not_the_root():
    """Employers put a requisition number, a company or a product in front of the
    role. Segment 0 is the DOMAIN there and the role is further right -- 28 rows,
    every one rooted at a req number or a product before this."""
    for title, want in (
        ("Airbag - Senior Developer", "Developer"),
        ("G71 - Full Stack Engineer", "Full Stack Engineer"),
        ("Dynamo AI — Forward Deployed Engineer", "Forward Deployed Engineer"),
        ("RQ11391 - Solutions Designer - CRM - Senior", "Solutions Designer"),
    ):
        assert vocab.decompose_title(title)["title_root"] == want, title


def test_a_generic_role_noun_is_still_the_root_and_is_never_swapped_for_its_domain():
    """THE LINE THIS FIX MUST NOT CROSS, and it was measured before being drawn.
    Falling through on a GENERIC root swaps the role for the domain -- `Engineering
    Manager, Payments` would root at `Payments`, which is a worse claim than
    `Manager`. The fall-through fires only when segment 0 contains NO role noun at
    all. 395 corpus titles have the `<role>, <domain>` shape and must be untouched."""
    assert (
        vocab.decompose_title("Manager, Forward Deployed Engineering")["title_root"]
        == "Manager"
    )
    assert vocab.decompose_title("Engineering Manager, Payments")["title_root"] == (
        "Engineering Manager"
    )
    assert vocab.decompose_title("Staff Product Manager, Agentic AI")["title_root"] == (
        "Product Manager"
    )


def test_a_level_mark_follows_the_role_and_a_leading_numeral_is_a_count():
    """`2 Full Stack AI Engineer, 1 GTM` is a HEADCOUNT and was read as level II;
    `Tier III Service Desk Engineer` is a support tier read as level III, with the
    root mangled to `Tier Service Desk Engineer`."""
    headcount = vocab.decompose_title("2 Full Stack AI Engineer, 1 GTM")
    assert headcount["title_level"] is None
    tier = vocab.decompose_title("Tier III Service Desk Engineer")
    assert tier["title_level"] is None
    assert tier["title_root"] == "Service Desk Engineer"
    # a real level mark still reads
    assert vocab.decompose_title("Software Engineer II")["title_level"] == "II"


def test_decomposition_invents_nothing_and_destroys_nothing():
    """The conservation gate, as a regression guard rather than a discovery tool: it
    passes 100% today and would have caught none of the eight filed findings. What it
    DOES catch is a future change that fabricates a token or drops a discriminative
    one -- the `NO REMOTE` -> `NO` class."""
    for title in (
        "Senior Software Engineer, AI",
        "Forward Deployed Engineer - Tokyo",
        "Staff Backend Engineer - Databases Tempo | US | Remote",
        "Manager, Forward Deployed Engineering",
    ):
        d = vocab.decompose_title(title)
        title_toks = {w.lower() for w in vocab._WORD_RE.findall(title)}
        root_toks = {w.lower() for w in vocab._WORD_RE.findall(d["title_root"])}
        assert root_toks <= title_toks, f"fabricated a token: {title}"
        assert d["title_root"].strip(), f"empty root: {title}"


# ── 0.9.0: classification — employment_type from vendor metadata ────────────
def _row(**kw):
    """A minimal record that passes the boundary, plus whatever the test is about."""
    return engine._coerce(
        {"title": "Engineer", "company": "Acme", "url": "https://x/1", **kw}
    )


def test_employment_type_is_read_from_any_metadata_key_by_value_not_by_name():
    """Greenhouse metadata keys are EMPLOYER-authored, so a key alias list cannot be
    written once and stay right: 61 distinct keys carry a resolvable value in the live
    store, including `Employment Classification (UKG)` and `Full-Time/Part-Time
    Status`. Meanwhile `Job Type` and `Worker Type` — two of the four most obvious
    names — resolve to nothing, because their values are `Standard` and `Employee`.
    The value carries the meaning; the key never did."""
    for key in ("Employment Type", "Full-Time/Part-Time Status", "TH: Employment Type"):
        r = _row(source="greenhouse", source_extra={key: "Full-time"})
        assert r["employment_type"] == "FULL_TIME", f"{key} was not read"
        assert r["employment_type_raw"] == "Full-time"


def test_an_unresolvable_metadata_value_is_left_alone():
    """`Regular` (151 rows) and `Standard` (124) are not employment types. Only a real
    map hit is accepted — never the OTHER fallback, which would mean "some string
    existed", not "a type was stated"."""
    for value in ("Regular", "Standard", "Employee", "Pipeline"):
        r = _row(source="greenhouse", source_extra={"Job Type": value})
        assert r["employment_type"] is None, f"{value!r} was accepted as a type"


def test_a_vendors_own_field_beats_its_metadata_bag():
    """FILL-ONLY. The metadata scan must never overwrite a type the source stated
    outright, because a dedicated field is better evidence than a custom column."""
    r = _row(
        source="greenhouse",
        employment_type="Part Time",
        source_extra={"Employment Type": "Full-time"},
    )
    assert r["employment_type"] == "PART_TIME"
    assert r["employment_type_raw"] == "Part Time"


def test_two_disagreeing_metadata_fields_are_not_a_type():
    """11 rows carry two different resolvable values at once — two forms filled
    inconsistently, so nobody stated one coherent fact. `None` is the only answer
    consistent with "null means the source did not say": a source that said two
    things has not said one. Both raws are kept so the disagreement is auditable.

    Deliberately the SAME rule the hn parser needs for a posting whose location text
    states two arrangements — one field stating a span is a range, but two fields
    disagreeing is an absence."""
    r = _row(
        source="greenhouse",
        source_extra={"Employment Type": "Contractor", "Time Type": "Part Time"},
    )
    assert r["employment_type"] is None
    assert r["employment_type_raw"] == "Contractor | Part Time"


def test_two_metadata_fields_that_agree_are_still_a_type():
    """Agreement is not conflict. Two keys spelling the same answer differently is
    the common case, not the ambiguous one."""
    r = _row(
        source="greenhouse",
        source_extra={"Employment Type": "Full-time", "Time Type": "Full Time"},
    )
    assert r["employment_type"] == "FULL_TIME"


def test_permanent_is_skipped_under_a_key_that_names_a_term():
    """`permanent` is flagged by vocab's own comment as the map's shakiest entry — UK/EU
    usage contrasting with fixed-term, not with part-time. Under a key literally named
    `Contract Type` the employer is answering the term question, which makes that
    flagged failure more likely, not less. 36 rows in the live store."""
    assert (
        _row(source="gh", source_extra={"Contract Type": "Permanent"})[
            "employment_type"
        ]
        is None
    )
    # ...but the same key still yields every other value it carries.
    assert (
        _row(source="gh", source_extra={"Contract Type": "Freelance"})[
            "employment_type"
        ]
        == "CONTRACTOR"
    )


def test_a_qualified_contract_string_still_normalizes():
    """braintrust's adapter builds `f"contract ({contract_type})"`, so `_PUNCT`
    flattened it to "contract long" and 29 of 29 rows became OTHER while bare
    "contract" mapped to CONTRACTOR — the adapter defeating its own normalizer. The
    qualifier survives in the raw, because that is what the vendor sent."""
    assert vocab.employment_type("contract (long)") == ("CONTRACTOR", "contract (long)")
    assert vocab.employment_type("contract (short)") == (
        "CONTRACTOR",
        "contract (short)",
    )
    # A trailing parenthetical is only tried after a DIRECT lookup misses, so it can
    # never rewrite a value that already resolved.
    assert vocab.employment_type("Full-time (Remote)")[0] == "FULL_TIME"


# ── 0.9.0: classification — seniority ───────────────────────────────────────
def test_stated_seniority_is_case_folded():
    """One column held five vendor dialects at once: `seniority='senior'` returned
    2,229 rows and missed 319 more spelled `Senior` or `Senior Level`, 179 of them for
    letter case alone."""
    r = _row(source="himalayas", seniority="Senior")
    assert r["seniority"] == "senior"
    assert r["seniority_raw"] == "Senior"
    assert r["seniority_basis"] == "stated"


def test_seniority_is_never_reranked_onto_an_invented_ladder():
    """THE LINE THIS FIELD IS HELD TO. Every other normalizer here maps onto a
    vocabulary someone else published; there is no such enumeration for seniority, so
    a rung order would be this library's opinion.

    Probed live 2026-08-20: SmartRecruiters ships LinkedIn's published enumeration
    verbatim, in which `Associate` ranks ABOVE `Entry level`. The one ladder available
    to copy collapses `Associate` to an entry rung — so adopting it would contradict a
    vendor's own published ordering on the source where it fires on 60% of rows."""
    assert _row(source="smartrecruiters", seniority="Mid-Senior Level")[
        "seniority"
    ] == ("mid-senior level")
    assert _row(source="smartrecruiters", seniority="Associate")["seniority"] == (
        "associate"
    )
    assert _row(source="themuse", seniority="Mid Level")["seniority"] == "mid level"


def test_a_declined_seniority_is_none_but_keeps_its_raw():
    """ "Not Applicable" and "Any" mean the vendor answered and refused to classify.
    There is no level, so there is no basis for one — and it deliberately does NOT
    fall through to the title, because overriding an explicit refusal with our own
    guess is the opinion this field is kept clear of."""
    for value in ("Not Applicable", "Any"):
        r = _row(source="smartrecruiters", title="Senior Engineer", seniority=value)
        assert r["seniority"] is None, value
        assert r["seniority_basis"] is None, value
        assert r["seniority_raw"] == value


def test_seniority_raw_is_never_invented_for_a_title_derived_level():
    """Same rule as `employment_type_raw`: it means "what the VENDOR said". Nobody
    quoted anything on the title branch, so a raw there would be a fabricated
    quotation — the one thing a provenance field cannot do."""
    r = _row(source="greenhouse", title="Senior Software Engineer")
    assert r["seniority"] == "senior"
    assert r["seniority_basis"] == "title"
    assert r["seniority_raw"] is None


def test_an_adapter_supplied_seniority_raw_is_not_overwritten():
    """themuse sends the canonical token AND the display string; the adapter maps them
    to `seniority` and `seniority_raw` itself, and the boundary must leave both."""
    r = _row(source="themuse", seniority="senior", seniority_raw="Senior Level")
    assert r["seniority"] == "senior"
    assert r["seniority_raw"] == "Senior Level"


def test_a_title_that_contradicts_the_metadata_vetoes_the_fill():
    """A hand-read of 150 sampled fills found the metadata asserting a type the posting
    itself contradicts — `Store Lead - Part Time` carrying `Full Time` metadata, and
    `Clinical Lab Scientist (Contract)` carrying `Full-time`. 163 rows, 1.91% of the
    live fill, landing in `shortlist.csv` where a CLI user reads them."""
    r = _row(
        title="Store Lead - Part Time",
        source="greenhouse",
        source_extra={"Time Type": "Full Time"},
    )
    assert r["employment_type"] is None
    assert r["employment_type_raw"] is None


def test_a_title_that_agrees_does_not_veto():
    """Corroboration is not contradiction. Only a type the metadata did NOT claim
    withdraws the claim."""
    r = _row(
        title="Part Time Weekend Support Representative",
        source="greenhouse",
        source_extra={"Time Type": "Part Time"},
    )
    assert r["employment_type"] == "PART_TIME"


def test_a_title_the_metadata_never_claimed_still_fills_when_silent():
    """The overwhelmingly common case: the title says nothing about schedule at all,
    so there is nothing to contradict and the metadata stands."""
    r = _row(
        title="Senior Software Engineer, Backend",
        source="greenhouse",
        source_extra={"Employment Type": "Full-time"},
    )
    assert r["employment_type"] == "FULL_TIME"


def test_the_title_is_a_veto_and_never_a_rival_answer():
    """THE ASYMMETRY IS THE DESIGN. Reading a title as a competing answer was measured
    and abandoned: three attempts surfaced three false-positive classes, each a
    NON-TECH role invisible on the 94 tech boards this corpus is built from — `b2b`
    ("B2B Performance Marketing", a market), `trainee` ("Manager Trainee", a permanent
    trades role) and `contract` ("Contract Management Lead", a domain noun).

    Under a veto those cost one unfilled row each — `None`, which honestly means "not
    established" — instead of a wrong assertion. This test pins the DIRECTION of
    failure on a row that genuinely IS contradicted: the metadata's claim is
    withdrawn, and the title's reading must never take its place."""
    r = _row(
        title="Store Lead - Part Time",
        source="greenhouse",
        source_extra={"Time Type": "Full Time"},
    )
    assert r["employment_type"] != "PART_TIME", "the title was treated as an answer"
    assert r["employment_type"] is None  # withdrawn, not replaced
    assert r["employment_type_raw"] is None


def test_the_veto_does_not_fire_on_domain_usage():
    """THE LEAD'S CONDITION on this change, and it was the right one. An employment
    word can name what the job is ABOUT (`Contract Management Lead`), a market
    (`B2B Performance Marketing`) or a training grade (`Manager Trainee`) — all
    permanent roles. Measured on 8,526 live fills: without this guard, **54 of 163
    vetoes (33.1%) were these**, each a correct fill thrown away. Every one is a
    NON-TECH role shape, which is why none appear in the 94-board tech corpus."""
    for title in (
        "Contract Management Lead | Supply Chain",
        "Contractor Special Security Officer",
        "Senior Associate, B2B Performance Marketing",
        "Manager Trainee",
        "Commercial Door Technician Trainee",
    ):
        r = _row(
            title=title, source="greenhouse", source_extra={"Time Type": "Full Time"}
        )
        assert r["employment_type"] == "FULL_TIME", f"domain usage vetoed: {title}"


def test_a_second_axis_in_the_title_is_not_a_contradiction():
    """`Customer Operations Intern - Part-time` speaks INTERN *and* PART_TIME while the
    metadata says INTERN — one axis corroborated and a second added. The veto compares
    by OVERLAP, not difference, so it fires only when the two share no type at all."""
    r = _row(
        title="Customer Operations Intern - Part-time Fall 2026",
        source="greenhouse",
        source_extra={"Employment Type": "Intern"},
    )
    assert r["employment_type"] == "INTERN"


def test_two_names_for_one_arrangement_are_not_a_contradiction():
    """A VOCABULARY GRANULARITY MISMATCH, not a disagreement. An employer's form offers
    Full-time/Part-time with no per-diem option, so a nursing manager picks Part-time
    and writes `Per Diem` in the title; `(Fixed-Term Contract)` against TEMPORARY is
    the same shape. The title is more specific than the form allowed. Vetoing on it
    discarded 40 rows the metadata had right, and it restores the repo's own rule that
    a structured signal stands when nothing actually contradicts it.

    The class is 100% NON-TECH — nursing and creative contract work — and measures
    zero on the 94 tech boards this rule was designed against. Third such class in
    this lens."""
    r = _row(
        title="Per Diem Registered Nurse - Pre-Op/PACU",
        source="greenhouse",
        source_extra={"Employment Type": "Part-time"},
    )
    assert r["employment_type"] == "PART_TIME"
    r = _row(
        title="Patient Access Specialist (Fixed-Term Contract)",
        source="greenhouse",
        source_extra={"Worker Sub-Type": "Temporary"},
    )
    assert r["employment_type"] == "TEMPORARY"
    # ...and a genuine cross-axis contradiction still vetoes.
    assert (
        _row(
            title="Store Lead - Part Time",
            source="greenhouse",
            source_extra={"Time Type": "Full Time"},
        )["employment_type"]
        is None
    )


def test_a_record_is_ordered_for_a_person_to_read():
    """The owner opened the harvest output and could not read it. `company` came 21
    fields in, BELOW the 6,870-character `text` body; `title_root` sat 15 fields from
    the `title` it decomposes. Key order carried no decision at all -- it was whatever
    each adapter's dict literal listed, then whatever `_coerce` appended.

    `text` is 72% of the median record, so wherever the body sits, everything after it
    is past a wall of prose. It goes last.
    """
    p = engine._coerce(
        {
            "title": "Senior AI Engineer",
            "company": "Acme",
            "url": "https://x/1",
            "source": "greenhouse",
            "location": "Austin, TX",
            "text": "a body",
        }
    )
    p["score"], p["signals"], p["dedup_key"] = 10, "remote", "acme|ai engineer|us"
    engine._reorder(p)
    keys = list(p)

    assert keys[0] == "title", "the role comes first"
    assert keys.index("company") < keys.index("text"), (
        "company must not sit below the body -- that is the defect this fixes"
    )
    assert keys.index("title_root") == keys.index("title") + 1
    assert keys[-1] == "sections" and keys[-3] == "text", "the body goes LAST"
    for group in (("salary", "salary_min", "salary_max"), ("city", "state", "country")):
        spans = [keys.index(k) for k in group if k in keys]
        assert spans == sorted(spans) and spans[-1] - spans[0] == len(spans) - 1, (
            f"{group[0]}'s group is split across the record"
        )


def test_reordering_can_never_drop_a_key():
    """An allowlist here would mean adding a contract field and silently deleting it
    from every record until someone remembered `_READING_ORDER` -- the same class of
    failure as emit._nested's hand-written field list. Unknown keys are KEPT."""
    p = {"title": "T", "text": "b", "a_brand_new_field": 1, "zzz": None}
    before = dict(p)
    engine._reorder(p)
    assert p == before, "reordering changed a value"
    assert set(p) == set(before), "reordering lost a key"
    assert list(p)[0] == "title" and "a_brand_new_field" in p


def test_harvest_actually_orders_the_rows_it_returns(monkeypatch):
    """THE WIRING, not the helper. `test_a_record_is_ordered_for_a_person_to_read`
    calls `_reorder` directly, so deleting the call site in `harvest` leaves it green
    while every row a consumer receives goes back to adapter-literal order. This is
    the test that goes red for that, and it is the shape the record is actually
    delivered in.
    """

    def fake_breadth(_queries):
        # Adapter-literal order: `text` early, `company` late -- the defect verbatim.
        return [
            {
                "title": "AI Engineer",
                "text": "Build things with LLMs. Remote.",
                "location": "Remote",
                "url": "https://x/1",
                "posted": "2026-08-01",
                "company": "Acme",
                "source": "fake",
            }
        ]

    cfg = config.Config()
    cfg.remote_only = False
    cfg.min_score = -999
    cfg.max_age_days = 100000
    monkeypatch.setattr(engine, "enabled_depth", lambda c: {})
    monkeypatch.setattr(engine, "enabled_breadth", lambda c: [("fake", fake_breadth)])
    rows, _, _ = engine.harvest(cfg, watchlist_path=None)

    assert rows, "the fixture produced no row to judge"
    keys = list(rows[0])
    assert keys[0] == "title"
    assert keys.index("company") < keys.index("text"), (
        "harvest returned a row in adapter order -- the _reorder call site is missing"
    )
    assert keys[-1] == "sections", "the body must be last in a delivered row"


def test_the_output_shape_levers_reach_a_library_caller(monkeypatch):
    """THE WIRING TEST, and it exists because the first version of this failed it.

    `--no-text` originally passed straight from argparse into `emit.records`. That put
    the single largest lever on the record (the body is ~72% of its bytes) behind the
    CLI *and* inside `emit` -- the one module the only known consumer imports nowhere.
    A CLI answer to a library problem, which is the same mistake this release exists
    to correct one layer up.

    So the levers live on `Config`, which `harvest` installs process-wide, and this
    asserts what a LIBRARY caller sees from `engine.harvest()` -- never the CLI.
    """

    def fake_breadth(_queries):
        return [
            {
                "title": "AI Engineer",
                "company": "Acme",
                "url": "https://x/1",
                "location": "Remote",
                "posted": "2026-08-01",
                "text": "Build things with LLMs. Remote.",
                "source": "fake",
            }
        ]

    def harvest_with(**kw):
        cfg = config.Config()
        cfg.remote_only, cfg.min_score, cfg.max_age_days = False, -999, 100000
        for k, v in kw.items():
            setattr(cfg, k, v)
        monkeypatch.setattr(engine, "enabled_depth", lambda c: {})
        monkeypatch.setattr(engine, "enabled_breadth", lambda c: [("fake", fake_breadth)])
        rows, _, _ = engine.harvest(cfg, watchlist_path=None)
        assert rows, "the fixture produced no row to judge"
        return rows[0]

    base = harvest_with()
    assert "text" in base and any(v is None for v in base.values()), (
        "defaults must keep the body and keep unknown keys present as None"
    )

    lean = harvest_with(include_text=False)
    assert "text" not in lean and "text_basis" not in lean
    assert lean["title"] == base["title"], "a display lever must not change a value"

    dense = harvest_with(omit_empty=True)
    assert not any(v is None or v == "" for v in dense.values())
    assert len(dense) < len(base), "omit_empty removed nothing"
    # Same values for every key that survived -- this drops keys, never rewrites them.
    assert all(dense[k] == base[k] for k in dense)


def test_omit_empty_keeps_the_lists_that_mean_something():
    """`[]` is not `None` and the contract turns on the difference. `remote_areas: []`
    means the posting STATED it is open anywhere; `sections: []` means we read the body
    and it carried no headers. Both took work to establish. Only None and "" go."""
    cfg = config.Config()
    cfg.omit_empty = True
    p = {
        "title": "E",
        "remote_areas": [],
        "sections": [],
        "source_extra": {},
        "tags": None,
        "salary": "",
    }
    engine._shape(p, cfg)
    assert p["remote_areas"] == [] and p["sections"] == [] and p["source_extra"] == {}
    assert "tags" not in p and "salary" not in p


def test_output_levers_default_to_the_shape_that_was_always_emitted():
    """Both default OFF. `_CONTRACT_FIELDS` are ensured-present-and-None on purpose so
    a consumer can write `WHERE remote_type IS NOT NULL` and mean it; dropping a key
    changes `k in record` and `.keys()`, which is a contract change, not a display
    choice. Nobody opts into that by upgrading."""
    c = config.Config()
    assert c.include_text is True and c.omit_empty is False
    p = {"title": "E", "text": "body", "tags": None}
    engine._shape(p, c)
    assert p == {"title": "E", "text": "body", "tags": None}
