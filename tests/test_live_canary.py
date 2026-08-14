"""Live canary: do the real APIs still return the shape our parsers read?

NOT part of CI. Every test here hits a third-party service over the network, so it
is marked `live` and deselected by default (see [tool.pytest.ini_options] in
pyproject.toml). It runs on a schedule from canary.yml, where a failure is a
notification rather than a blocked pull request.

Why it exists, and why the unit tests cannot replace it. `tests/test_sources.py`
asserts each adapter against a CAPTURED payload — which freezes the vendor's shape
as it was on the day the fixture was written. If Jobicy renames `jobTitle`
tomorrow, every fixture test still passes and the live harvest silently returns
blank titles. Nothing in a hermetic suite can detect that, because the thing that
changed is outside the repo. Only asking the real endpoint can.

The distinction this file draws, and the reason it is not just "run the harvest":

  * UNREACHABLE (timeout, 5xx, DNS)  -> skip. Someone else's outage is not our bug,
    and a canary that cries wolf on every blip gets muted, which is worse than
    having no canary.
  * REACHABLE BUT WRONG SHAPE        -> fail. The endpoint answered and the parser
    got nothing usable out of it. That is drift, and it is exactly what we want to
    hear about on a Monday morning instead of discovering through an empty
    shortlist three weeks later.

Only keyless sources are covered. Adzuna, USAJOBS and Google-for-Jobs need
credentials, and a scheduled public workflow should not carry them.
"""

import re

import pytest

from job_radar import config, iso3166, sources
from job_radar.util import NET_ERRORS

pytestmark = pytest.mark.live

# Sources that return their WHOLE board regardless of the query. An empty list from
# one of these is never "no matches" — it means the response no longer looks like
# what the parser expects, so it is a hard failure rather than a soft one.
# `braintrust` and `hn` were missing entirely, and braintrust is precisely where a
# vendor silently dropped a field on us: its `level` key -- which the adapter mapped
# to `department` -- returns on 0 of 20 rows today. Uncovered sources are where drift
# hides, so the list is now every keyless breadth adapter.
WHOLE_BOARD = [
    "jobicy", "arbeitnow", "remoteok", "themuse", "remotive", "braintrust", "hn",
]  # fmt: skip

# Query-driven sources. A niche query can legitimately return nothing, so these
# assert on SHAPE when rows come back rather than on row count.
# `remotive` moved to WHOLE_BOARD: every parameter on that endpoint is ignored, so
# it takes no query at all and an empty result is drift, not "no matches".
QUERY_DRIVEN = ["himalayas"]

BROAD_QUERY = ["engineer"]


def _cfg():
    c = config.Config()
    config.set_active(c)
    return c


def _check_shape(rows, name, expect_company=True):
    """A parsed row is only useful if it can be applied to and shown to a human:
    it needs somewhere to click and something to read.

    `expect_company` is False for DEPTH adapters, and that is a real architectural
    distinction rather than a leniency. A depth adapter is handed a slug and has no
    way to know the employer's display name, so `engine._consume` supplies it from
    the watchlist entry (engine.py `p.setdefault("company", "")`). A breadth adapter
    has no such context — if it does not carry the company, nothing downstream can.
    """
    assert rows, f"{name}: parsed 0 rows from a reachable endpoint"
    bad_url = [r for r in rows if not (r.get("url") or "").startswith("http")]
    bad_title = [r for r in rows if not (r.get("title") or "").strip()]
    assert not bad_url, f"{name}: {len(bad_url)}/{len(rows)} rows have no usable url"
    assert not bad_title, f"{name}: {len(bad_title)}/{len(rows)} rows have no title"

    # `text` and `posted` were NOT checked in the first version of this file, and
    # that was the hole: a vendor renaming its description field leaves url and
    # title intact, so the canary stayed green while every role scored zero on an
    # empty body — the exact "quieter shortlist that looks like a slow job market"
    # this file exists to catch. Judge on a MAJORITY rather than every row: some
    # boards genuinely post a role with no body, but most of them doing so is drift.
    blank_text = sum(1 for r in rows if not (r.get("text") or "").strip())
    blank_posted = sum(1 for r in rows if not (r.get("posted") or "").strip())
    assert blank_text <= len(rows) // 2, (
        f"{name}: {blank_text}/{len(rows)} rows have an empty body — "
        "text is the entire input to the fit score"
    )
    assert blank_posted <= len(rows) // 2, (
        f"{name}: {blank_posted}/{len(rows)} rows have no date — "
        "a blank date sinks a role in the freshness filter"
    )
    required = {"title", "url", "posted", "text"} | (
        {"company"} if expect_company else set()
    )
    for r in rows[:5]:
        missing = required - set(r)
        assert not missing, f"{name}: row missing {missing}"


@pytest.mark.parametrize("name", WHOLE_BOARD)
def test_whole_board_source_still_parses(name):
    _cfg()
    try:
        rows = sources.BREADTH_ALL[name](BROAD_QUERY)
    except NET_ERRORS as e:
        pytest.skip(f"{name} unreachable ({type(e).__name__}) — their outage, not ours")
    _check_shape(rows, name)


@pytest.mark.parametrize("name", QUERY_DRIVEN)
def test_query_driven_source_still_parses(name):
    """These two swallow network errors internally so a harvest fails soft, which
    meant every failure — outage OR drift — reached this test as an empty list and
    got reported as an ambiguous skip. So they could never go red on the one thing
    they exist to detect. `strict=True` makes an outage raise, so the skip below now
    means only "unreachable" and an empty list means only "drift"."""
    _cfg()
    try:
        rows = sources.BREADTH_ALL[name](BROAD_QUERY, strict=True)
    except NET_ERRORS as e:
        pytest.skip(f"{name} unreachable ({type(e).__name__}) — their outage, not ours")
    assert rows, (
        f"{name}: reachable but parsed 0 rows for a broad query — that is drift, "
        "not an empty market"
    )
    _check_shape(rows, name)


# ── depth adapters, against boards from the shipped starter watchlist ────────
# One live board per ATS. A 404 means that company moved or closed its board — a
# watchlist problem, not a parser problem — so it skips. A 200 whose body no longer
# parses is drift, and fails.
# Slugs taken from the SHIPPED starter watchlist, not invented — a probe against a
# board that was never real tests nothing and skips forever, looking healthy.
# That watchlist currently carries only Greenhouse and Ashby boards; add a Lever /
# SmartRecruiters / Workable probe here when one joins it.
DEPTH_PROBES = [
    ("greenhouse", ("anthropic",)),
    ("ashby", ("openai",)),
    ("lever", ("binance",)),
    ("rippling", ("rippling",)),
]


@pytest.mark.parametrize("ats,args", DEPTH_PROBES)
def test_depth_adapter_still_parses_a_live_board(ats, args):
    _cfg()
    try:
        rows = sources.DEPTH_ALL[ats](*args)
    except NET_ERRORS as e:
        pytest.skip(f"{ats}/{args[0]} unreachable ({type(e).__name__})")
    if not rows:
        pytest.skip(f"{ats}/{args[0]} returned no roles — board may have moved")
    _check_shape(rows, f"{ats}/{args[0]}", expect_company=False)


def test_liveness_probes_still_agree_with_a_real_board():
    """`liveness_for` promises a cheap role COUNT that matches what a full fetch
    would return. If the vendor changes the field that count is read from, discovery
    silently starts treating live boards as dead — the expensive kind of drift,
    because it removes companies instead of adding noise."""
    _cfg()
    cheap = sources.liveness_for("greenhouse")
    try:
        n = cheap("anthropic")
        full = len(sources.fetch_greenhouse("anthropic"))
    except NET_ERRORS as e:
        pytest.skip(f"greenhouse unreachable ({type(e).__name__})")
    assert isinstance(n, int), "liveness must return an int count"
    assert n > 0, "liveness reported 0 roles for a board that is definitely live"
    # Exact equality is too strict — a role can be posted between the two calls.
    assert abs(n - full) <= 5, f"liveness said {n}, full fetch said {full}"


# What each source was MEASURED to populate on 2026-08-05, with the sample size. This
# is the only detector in the repo for a vendor silently dropping a field: a fixture
# test asserts our parser against our own captured payload, so when the vendor stops
# sending something, every hermetic test still passes and the column just goes quiet.
#
# The concrete misses this exists to catch, all found by hand rather than by a test:
# braintrust's `level` (0/20 -- gone from the response, the mapping is dead),
# himalayas shipping the literal string "name" as companyName, Workable's absent
# `location` key (0/28), Teamtailor's jobLocation being a list (0/53).
#
# Keys are listed ONLY where the source genuinely sends them. A legitimately sparse
# field must not be listed -- a canary that goes red on a truthfully-absent value
# gets muted, and a muted canary is worse than none.
POPULATED = {
    "greenhouse": ("posted", "text"),
    "ashby": ("posted", "text", "remote_type", "city", "country", "salary_min"),
    "lever": ("posted", "text", "country", "remote_type"),
    "themuse": ("posted", "text", "city", "country"),
    # `remote_scope_raw` on the three sources that carry an eligibility boundary.
    # Measured live 2026-08-14: jobicy 100/100 rows, remotive 18/18, himalayas
    # 199/204 -- so a source going quiet here is the vendor dropping the field, not a
    # sparse day. Deliberately NOT `remote_areas`: himalayas' correct answer is very
    # often `[]`, which `_populated` counts as empty, and a canary that goes red on
    # the right answer gets muted.
    "jobicy": ("posted", "text", "remote_scope_raw"),
    "remotive": ("posted", "text", "remote_scope_raw"),
    "himalayas": ("posted", "text", "remote_scope_raw"),
    "remoteok": ("posted", "text"),
    "arbeitnow": ("posted", "text"),
    "braintrust": ("posted", "text"),
    "hn": ("posted", "text"),
}


def _populated(rows, key):
    return sum(1 for r in rows if r.get(key) not in (None, "", [], {}))


@pytest.mark.parametrize("name", sorted(POPULATED))
def test_a_field_a_source_really_sends_has_not_gone_quiet(name):
    """Drift shows up as a column that is suddenly empty on EVERY row while the
    endpoint stays healthy and every offline test stays green."""
    _cfg()
    from job_radar import engine

    try:
        if name in sources.DEPTH_ALL:
            slug = dict(DEPTH_PROBES)[name][0]
            raw = sources.DEPTH_ALL[name](slug)
        else:
            raw = sources.BREADTH_ALL[name](BROAD_QUERY)
    except NET_ERRORS as e:
        pytest.skip(f"{name} unreachable ({type(e).__name__})")
    if not raw:
        pytest.skip(f"{name} returned no rows — covered by the shape tests")
    rows = [engine._coerce(r) for r in raw]
    for key in POPULATED[name]:
        assert _populated(rows, key), (
            f"{name}: `{key}` is empty on all {len(rows)} rows, and this source was "
            "measured sending it. Either the vendor renamed/dropped the field or our "
            "mapping broke — both are silent everywhere else."
        )


# ─────────────────────────────────────────────────────────────────────────────
# The eligibility boundary: remote_areas / remote_regions / remote_scope_raw.
#
# THIS BLOCK EXISTS BECAUSE 0.8.0 SHIPPED THREE DEFECTS IN THIS EXACT PATH and every
# test above was green through all of them. `sources.stated_scope` had never met a
# live vendor response: its fixtures were hand-written, and the development corpus
# was a downstream store with no adapter-supplied structured fields, so the code was
# unreachable even in principle. The canary proved the adapters PARSE; nothing
# asserted on what they parsed the boundary INTO.
#
# What shipped anyway: "Worldwide" recorded as "we don't know" on a third of the
# remotive feed, and three of five continents dropped from a value that appears on
# 11% of it. Both are silent -- a wrong list still round-trips, still filters, and
# still looks like data.
#
# Only the three adapters that call stated_scope are covered. Every other source
# leaves these fields None, and asserting on them there would be asserting on
# silence.
SCOPE_SOURCES = ["remotive", "jobicy", "himalayas"]
_AREA_RE = re.compile(r"^[A-Z]{2}(-[A-Z0-9]{1,3})?$")
_TZ_QUALIFIER = re.compile(r"\btime\s?zones?\b", re.I)
# Built ONCE. This was `frozenset(iso3166.NAME_TO_ALPHA2.values())` written inside the
# loop that checks it -- 425 entries rebuilt per region per row, in a file where every
# other constant is module-level. Milliseconds, not a bottleneck; it is here because an
# invariant collection built inside its own loop reads as an accident.
_ISO_CODES = frozenset(iso3166.NAME_TO_ALPHA2.values())


_SCOPE_CACHE: dict = {}


def _scope_rows(name):
    """Live rows from one boundary-carrying source, or a skip.

    CACHED PER RUN, and that is not an optimization. Four assertions below read the
    same source, and himalayas is ~35 paged requests -- so the uncached version made
    five full crawls of one vendor per canary run and got 429'd into a skip, which is
    both rude and self-defeating: a rate-limited canary reports "unreachable" and
    tells you nothing. One fetch, four questions asked of it.
    """
    if name not in _SCOPE_CACHE:
        _cfg()
        from job_radar import engine

        try:
            raw = sources.BREADTH_ALL[name](BROAD_QUERY)
        except NET_ERRORS as e:
            _SCOPE_CACHE[name] = (None, f"{name} unreachable ({type(e).__name__})")
        else:
            _SCOPE_CACHE[name] = (
                ([engine._coerce(r) for r in raw], None)
                if raw
                else (None, f"{name} returned no rows — covered by the shape tests")
            )
    rows, why = _SCOPE_CACHE[name]
    if rows is None:
        pytest.skip(why)
    return rows


@pytest.mark.parametrize("name", SCOPE_SOURCES)
def test_the_boundary_fields_hold_their_shape_on_live_rows(name):
    """Structural invariants, checked against what the vendor actually sent today.

    The anti-mixing rule is the one that matters: `remote_regions` must never hold a
    country code. One column holding codes, subdivisions, region names and sentinels
    at once is precisely what 0.8.0 removed, and a vendor sending a new value is how
    it would grow back -- silently, since nothing downstream type-checks a list of
    strings.
    """
    from job_radar import vocab

    rows = _scope_rows(name)
    for r in rows:
        areas, regions = r.get("remote_areas"), r.get("remote_regions")
        raw = r.get("remote_scope_raw")
        assert areas is None or isinstance(areas, list), f"{name}: areas {areas!r}"
        assert regions is None or isinstance(regions, list), (
            f"{name}: regions {regions!r}"
        )
        for a in areas or ():
            assert _AREA_RE.match(a), (
                f"{name}: remote_areas holds {a!r}, which is not an ISO alpha-2 or "
                "3166-2 code. A vendor name reached the column unresolved."
            )
        for g in regions or ():
            assert g in vocab.REMOTE_REGION_TOKENS, (
                f"{name}: remote_regions holds {g!r}, outside the closed token set."
            )
            assert g not in _ISO_CODES, (
                f"{name}: remote_regions holds {g!r}, a COUNTRY CODE. Regions and "
                "countries are separate fields on purpose; this is the mixing bug."
            )
        # A parsed boundary with no source text means someone invented it -- with ONE
        # real exception this assertion found on its first live run: himalayas states
        # "open worldwide" as an EMPTY ARRAY, so there genuinely are no vendor words to
        # record and `[]`/None is the honest pair. An ENUMERATED boundary out of
        # nowhere is still fabrication, and so is any region.
        if raw is None:
            assert not areas and regions is None, (
                f"{name}: parsed a boundary ({areas!r}/{regions!r}) from no raw value."
            )


@pytest.mark.parametrize("name", SCOPE_SOURCES)
def test_a_stated_boundary_is_not_silently_dropped(name):
    """The vendor said something; did we understand ANY of it?

    This is the assertion whose absence let 0.8.0 ship. Both shipped defects present
    identically here -- a row with a real `remote_scope_raw` and nothing parsed out of
    it -- whether the cause is an unknown word ("Oceania") or a known one nobody
    mapped ("Worldwide"). It deliberately measures a RATIO rather than demanding a
    parse on every row: some vendor strings are genuinely prose we should not pretend
    to read, and a canary that goes red on one weird posting gets muted.

    `[]` counts as understood. It is the STATED-worldwide answer, and treating it as a
    miss would invert the very distinction this field exists to carry.
    """
    rows = _scope_rows(name)
    stated = [r for r in rows if r.get("remote_scope_raw")]
    if not stated:
        pytest.skip(f"{name} sent no boundary text on any of {len(rows)} rows")
    unparsed = [
        r
        for r in stated
        if r.get("remote_areas") is None and r.get("remote_regions") is None
    ]
    # 10%, not 25%. Measured 0 violations across 820 live rows on all three sources,
    # while defect (a) put remotive at 6 of 18 (33%) -- so the gate has 3x margin over
    # the failure and still tolerates one odd prose posting in a small feed. A
    # zero-tolerance gate would be higher signal and was argued for; it reddens on the
    # first unreadable string a vendor invents, which is how a canary gets muted.
    ratio = len(unparsed) / len(stated)
    assert ratio <= 0.10, (
        f"{name}: {len(unparsed)} of {len(stated)} rows carry boundary text we parsed "
        f"nothing out of ({ratio:.0%}). Samples: "
        f"{sorted({r['remote_scope_raw'] for r in unparsed})[:5]}. Either the vendor "
        "changed its vocabulary or ours is missing a token — 0.8.0 shipped with "
        "'Worldwide' unmapped on a third of one feed and three continents on another."
    )


def _unmapped_tokens(rows):
    """Boundary tokens the vocabulary understood nothing of. -> (total, missed, samples)

    The ratio test above asks "did we get ANYTHING out of this row"; this asks "did we
    get EVERYTHING". The difference is not academic -- it is exactly defect two of
    0.8.0: `"Americas, Europe, Asia, Africa, Oceania"` parsed to two regions, so the
    row looked understood while three continents fell on the floor. Only counting
    tokens sees that.

    COUNTS OCCURRENCES, NOT DISTINCT NAMES, and the first version of this got it wrong
    in a way that quietly gutted the gate: it returned a SET of missed names over a
    COUNT of total tokens -- different units on the two halves of one ratio. A single
    unmapped word repeated on every row counted once, so the more widespread the
    failure, the smaller it looked. Counterfactually replaying the defects proves the
    cost: "Worldwide" unmapped on 6 of 18 rows read as 2.4% (green, gate missed it
    entirely) instead of 14.6%, and the dropped continents as 11.4% against a 10% gate
    -- 1.14x margin -- instead of 20.0%. Distinct names are still returned, but only as
    SAMPLES for the failure message.
    """
    from job_radar import iso3166, vocab

    total, missed, samples = 0, 0, set()
    for r in rows:
        raw = r.get("remote_scope_raw")
        if not raw:
            continue
        # `[]` is stated-worldwide: one word covering every token by definition.
        if r.get("remote_areas") == []:
            continue
        for tok in (t.strip() for t in raw.replace("&", ",").split(",")):
            if not tok:
                continue
            total += 1
            resolved = (
                vocab.country_code(tok)
                or iso3166.alpha2(tok)
                or tok.upper() in vocab.REMOTE_REGION_TOKENS
                # A WORK-HOURS qualifier, not a place. remotive sends "European
                # timezones" and "USA timezones" alongside the real boundary tokens
                # (16.7% of its rows today), and stated_scope correctly declines to
                # read an eligibility boundary out of an overlap requirement. Named
                # explicitly rather than left to fall through: an earlier version of
                # this check credited them BY ACCIDENT, because "EUROPE" is a
                # substring of "EUROPEAN TIMEZONES", which would equally have
                # credited any garbage containing a mapped word.
                or _TZ_QUALIFIER.search(tok)
            )
            if not resolved:
                missed += 1
                samples.add(tok)
    return total, missed, samples


@pytest.mark.parametrize("name", SCOPE_SOURCES)
def test_every_word_of_a_boundary_is_understood_not_just_the_first_two(name):
    """Measured 2026-08-14, AFTER the 0.8.1 fixes: 0 unmapped of 41 (remotive), 128
    (jobicy), 660 (himalayas) live tokens. The gate sits at 10% because the failures
    it exists to catch were far above it -- "Worldwide" was 6 of remotive's 41 tokens
    (15%) and the missing continents were 3 of every 5 on 11% of a feed -- while
    normal churn is a single new country name in a hundred.

    A miss here is not a crash. It is a role open to a continent that no one searching
    that continent will ever see.
    """
    total, missed, samples = _unmapped_tokens(_scope_rows(name))
    # remotive's whole corpus is 41 tokens and it swung 31 -> 18 postings in 11 days,
    # so a ratio over a handful of tokens is noise wearing a percentage sign: ONE new
    # oddly-worded posting would be 1-of-4. Below 30 tokens, say nothing.
    if total < 30:
        pytest.skip(f"{name}: only {total} boundary tokens — too few to rate")
    # TWO GATES, because one ratio cannot see both failures. A RATIO answers "how many
    # rows are affected" and catches a catastrophe -- a vendor renaming "United States"
    # trips it instantly. But it goes BLIND exactly where the corpus is largest: the
    # three-continent defect was 20% of remotive's 35 tokens and would be roughly 1-2%
    # of himalayas' 660, passing a 10% gate silently and forever on the biggest source
    # in the set. Token repetition is why -- measured mean 8.6 occurrences per distinct
    # name on a 2,108-token corpus, with a handful of names dominating.
    #
    # So DISTINCT unmapped names answers the other question: how far has the vocabulary
    # drifted, independent of feed size. Baseline is 0 on all three sources today, and
    # normal churn is a single new country name, so 2 is one token of slack. Defect (b)
    # was three continents and fails this on every source regardless of size.
    assert missed / total <= 0.10, (
        f"{name}: {missed} of {total} boundary tokens resolved to nothing — "
        f"{sorted(samples)[:8]}. Each one is a place a searcher will not be shown. "
        "Add it to vocab._REMOTE_REGIONS (a multi-country region) or confirm it "
        "belongs in the ISO table (a country); do not widen this threshold."
    )
    assert len(samples) <= 2, (
        f"{name}: {len(samples)} DISTINCT boundary names resolved to nothing — "
        f"{sorted(samples)[:8]}. Only {missed}/{total} token occurrences, so the ratio "
        "gate above stays green; that is the point of this second assertion. Several "
        "different unmapped names means the vocabulary has drifted, not that one odd "
        "posting appeared."
    )


@pytest.mark.parametrize("name", SCOPE_SOURCES)
def test_an_unbounded_posting_does_not_also_name_a_boundary(name):
    """A tripwire that should sit silent for a long time, and that is the point.

    `remote_areas == []` asserts the vendor said "anywhere", and `_region_allowed`
    treats it as satisfying EVERY scope policy -- the most permissive value in the
    contract. So a row claiming worldwide while its own raw text names a country is a
    contradiction in the one direction that admits a posting into a filter meant to
    exclude it. That is defect three of 0.8.0 ("Anywhere in the US" reading as
    unbounded), and it is the ONLY defect of the three a live canary structurally
    cannot catch today: across 820 live values, ZERO strings contain an anywhere-word
    plus anything else. The vendors are not currently sending the shape that breaks
    it, so the hermetic table in test_sources.py is what actually gates it on a PR.

    This costs one comparison and fires the day a vendor starts sending it.
    """
    from job_radar import iso3166, vocab

    for r in _scope_rows(name):
        if r.get("remote_areas") != []:
            continue
        raw = r.get("remote_scope_raw") or ""
        named = [
            t.strip()
            for t in raw.replace("&", ",").split(",")
            if t.strip()
            and (
                vocab.country_code(t.strip())
                or iso3166.alpha2(t.strip())
                or t.strip().upper() in vocab.REMOTE_REGION_TOKENS
            )
        ]
        assert not named, (
            f"{name}: row claims stated-worldwide (remote_areas == []) while its own "
            f"raw value {raw!r} names {named}. [] satisfies every allowed_scopes "
            "policy, so this admits a bounded posting into a filter that excludes it."
        )
