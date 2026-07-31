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

import pytest

from job_radar import config, sources
from job_radar.util import NET_ERRORS

pytestmark = pytest.mark.live

# Sources that return their WHOLE board regardless of the query. An empty list from
# one of these is never "no matches" — it means the response no longer looks like
# what the parser expects, so it is a hard failure rather than a soft one.
WHOLE_BOARD = ["jobicy", "arbeitnow", "remoteok", "techtree"]

# Query-driven sources. A niche query can legitimately return nothing, so these
# assert on SHAPE when rows come back rather than on row count.
QUERY_DRIVEN = ["remotive", "himalayas"]

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
    _cfg()
    try:
        rows = sources.BREADTH_ALL[name](BROAD_QUERY)
    except NET_ERRORS as e:
        pytest.skip(f"{name} unreachable ({type(e).__name__}) — their outage, not ours")
    if not rows:
        pytest.skip(f"{name}: 0 rows for a query-driven source — ambiguous, not drift")
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
