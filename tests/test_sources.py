"""Adapter tests: every source parses its vendor's real response shape, and every
registered adapter honours the posting contract.

Why this file exists. An adapter is a pure mapping from one vendor's JSON onto
job-radar's posting dict, and it is the single most fragile thing here — the shape
is owned by someone else and can change without notice. When it does, the adapter
does not crash: `.get()` returns "" and the role is silently blank or dropped. The
only symptom is a quieter shortlist, which is indistinguishable from a slow job
market. Six of the eleven breadth adapters had no test at all before this file,
despite CONTRIBUTING.md requiring one for every new source.

Fixtures here are trimmed captures of the real payloads, keeping the field names
and nesting verbatim — a fixture invented from the code proves only that the code
agrees with itself. Run: pytest tests/test_sources.py
"""

import tempfile
from pathlib import Path

import pytest

from job_radar import config, sources

# Every posting an adapter emits must carry these, because the engine, the scorer
# and the CSV store all read them unconditionally.
REQUIRED_KEYS = {
    "title",
    "location",
    "url",
    "posted",
    "department",
    "employment_type",
    "salary",
    "text",
}

# `company` is required of BREADTH adapters only, and that is architectural rather
# than lenient. A depth adapter is handed a slug and cannot know the employer's
# display name, so engine._consume supplies it from the watchlist entry
# (`p.setdefault("company", "")`). A breadth adapter has no such context — if it
# does not carry the company, nothing downstream can recover it.
BREADTH_REQUIRED_KEYS = REQUIRED_KEYS | {"company"}


def _cfg():
    c = config.Config()
    config.set_active(c)
    return c


def _usajobs_response(monkeypatch, payload):
    """USAJOBS builds its own urllib Request (it needs auth headers), so it does not
    pass through get_json and has to be stubbed one level lower."""
    import json as _json
    import urllib.request

    class _Resp:
        def read(self):
            return _json.dumps(payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    seen: list[str] = []

    def _open(req, timeout=0):
        seen.append(req.full_url if hasattr(req, "full_url") else str(req))
        return _Resp()

    monkeypatch.setattr(urllib.request, "urlopen", _open)
    return seen  # the request URLs, for tests that assert on what was SENT


def _assert_contract(postings, source_name=None, required=None):
    """The invariant every adapter owes its callers, asserted in one place."""
    required = required or BREADTH_REQUIRED_KEYS
    for p in postings:
        missing = required - set(p)
        assert not missing, f"missing keys {missing} in {p}"
        for k in required:
            assert isinstance(p[k], str), f"{k} is {type(p[k]).__name__}, expected str"
        if source_name:
            assert p.get("source") == source_name


# ── the six adapters that had no test ───────────────────────────────────────
def test_remotive_parser_maps_fields(monkeypatch):
    fake = {
        "jobs": [
            {
                "title": "AI Engineer",
                "company_name": "Acme",
                "candidate_required_location": "USA",
                "url": "https://remotive.com/remote-jobs/1",
                "publication_date": "2026-07-10T00:00:00",
                "category": "Software Development",
                "job_type": "full_time",
                "salary": "$150,000 - $180,000",
                "description": "<p>Build &amp; ship LLM systems.</p>",
            }
        ]
    }
    monkeypatch.setattr(sources, "get_json", lambda url: fake)
    monkeypatch.setattr(sources.time, "sleep", lambda s: None)
    out = sources.search_remotive(["AI Engineer"])
    assert len(out) == 1
    j = out[0]
    assert j["title"] == "AI Engineer" and j["company"] == "Acme"
    assert j["location"] == "USA (Remote)"  # Remotive is remote-only by definition
    assert j["posted"] == "2026-07-10"
    assert j["department"] == "Software Development"
    assert j["salary"] == "$150,000 - $180,000"  # vendor's own field wins
    assert "&" in j["text"] and "<p>" not in j["text"]  # html unescaped + stripped
    _assert_contract(out, "remotive")


def test_remotive_makes_one_request_and_leaks_no_query(monkeypatch):
    """Remotive gets exactly ONE unfiltered request, however many queries are passed.

    This test used to assert a cap of FOUR, encoding a promise that was wrong twice
    over (catalog/remotive.md, measured 2026-08-03): every parameter on this endpoint
    is ignored, so those four were four IDENTICAL requests rather than four searches;
    and the vendor's notice advises four requests per DAY, which four-per-run exceeds
    24x on an hourly schedule.

    Asserting the query does not reach the URL is the load-bearing half. A parameter
    that looks applied and silently is not is precisely how this went unnoticed for
    every release the adapter has existed — the same failure the Muse test guards.
    """
    calls = []
    monkeypatch.setattr(
        sources, "get_json", lambda url: calls.append(url) or {"jobs": []}
    )
    monkeypatch.setattr(sources.time, "sleep", lambda s: None)
    sources.search_remotive([f"unique-query-{i}" for i in range(10)])
    assert len(calls) == 1, f"expected one unfiltered request, made {len(calls)}"
    assert "search=" not in calls[0], "the dead `search` parameter is back"
    assert not any("unique-query" in c for c in calls), "a query leaked into the URL"


def test_jobicy_parser_maps_fields(monkeypatch):
    fake = {
        "jobs": [
            {
                "jobTitle": "Machine Learning Engineer",
                "companyName": "Globex",
                "jobGeo": "Anywhere",
                "url": "https://jobicy.com/jobs/1",
                "pubDate": "2026-07-09 12:00:00",
                "jobIndustry": ["Engineering"],
                "jobType": ["full-time", "contract"],  # a LIST, unlike every sibling
                "jobExcerpt": "Ship models.",
            }
        ]
    }
    monkeypatch.setattr(sources, "get_json", lambda url: fake)
    out = sources.search_jobicy(["ml"])
    j = out[0]
    assert j["title"] == "Machine Learning Engineer" and j["company"] == "Globex"
    # jobIndustry is a LIST in the live API; it must not reach the CSV as a repr.
    assert j["department"] == "Engineering"
    assert j["location"] == "Anywhere (Remote)"
    # The list-vs-string case is the whole reason this adapter has a branch.
    assert j["employment_type"] == "full-time, contract"
    assert j["text"] == "Ship models."  # falls back to jobExcerpt when no description
    _assert_contract(out, "jobicy")


def test_arbeitnow_keeps_only_remote(monkeypatch):
    fake = {
        "data": [
            {
                "title": "Remote AI Engineer",
                "company_name": "Initech",
                "location": "Berlin",
                "url": "https://arbeitnow.com/1",
                "created_at": 1783699200,  # epoch, not ISO
                "job_types": ["full_time"],
                "description": "LLM work.",
                "remote": True,
            },
            {
                "title": "Onsite Engineer",
                "company_name": "Initech",
                "location": "Berlin",
                "url": "https://arbeitnow.com/2",
                "created_at": 1783699200,
                "job_types": [],
                "description": "Onsite only.",
                "remote": False,
            },
        ]
    }
    monkeypatch.setattr(sources, "get_json", lambda url: fake)
    out = sources.search_arbeitnow(["ai"])
    assert len(out) == 1, "the non-remote role must be dropped at the source"
    assert out[0]["title"] == "Remote AI Engineer"
    assert out[0]["posted"] == "2026-07-10"  # epoch resolved
    _assert_contract(out, "arbeitnow")


def test_himalayas_parser_maps_fields(monkeypatch):
    fake = {
        "jobs": [
            {
                "title": "LLM Engineer",
                "companyName": "Hooli",
                "locationRestrictions": ["United States", "Canada"],
                "applicationLink": "https://hooli.com/apply/1",
                "guid": "https://himalayas.app/jobs/1",
                "pubDate": "2026-07-08T00:00:00Z",
                "employmentType": "Full Time",
                "minSalary": 150000,
                "maxSalary": 200000,
                "excerpt": "Agentic systems.",
            }
        ]
    }
    monkeypatch.setattr(sources, "get_json", lambda url: fake)
    monkeypatch.setattr(sources.time, "sleep", lambda s: None)
    out = sources.search_himalayas(["llm"])
    j = out[0]
    assert j["title"] == "LLM Engineer" and j["company"] == "Hooli"
    assert j["location"] == "United States, Canada (Remote)"
    assert j["url"] == "https://hooli.com/apply/1"  # direct link beats the guid
    assert "150,000" in j["salary"] and "200,000" in j["salary"]
    _assert_contract(out, "himalayas")


def test_usajobs_skipped_without_credentials(monkeypatch, capsys):
    c = _cfg()
    monkeypatch.setattr(c, "env", lambda name: "")
    assert sources.search_usajobs(["engineer"]) == []
    assert "usajobs" in capsys.readouterr().out.lower()


def test_usajobs_parser_maps_nested_federal_shape(monkeypatch):
    """USAJOBS nests everything under MatchedObjectDescriptor and puts pay in a
    LIST — the deepest shape here, and the easiest to break silently."""
    c = _cfg()
    monkeypatch.setattr(c, "env", lambda name: "set")
    fake = {
        "SearchResult": {
            "SearchResultItems": [
                {
                    "MatchedObjectDescriptor": {
                        "PositionTitle": "IT Specialist (Data Management)",
                        "OrganizationName": "National Institutes of Health",
                        "DepartmentName": "Department of Health",
                        "PositionLocationDisplay": "Bethesda, Maryland",
                        "PositionURI": "https://www.usajobs.gov/job/1",
                        "PublicationStartDate": "2026-07-06",
                        # `.Code`, because `.Name` is EMPTY on 47 of 50 live rows
                        # and a shift pattern on the rest — see the adapter comment.
                        "PositionSchedule": [{"Code": "1", "Name": "Full-Time"}],
                        "JobGrade": [{"Code": "GS"}],
                        "PositionRemuneration": [
                            {"MinimumRange": "120000", "MaximumRange": "150000"}
                        ],
                        "UserArea": {
                            "Details": {
                                "JobSummary": "Federal AI work.",
                                "LowGrade": "13",
                                "HighGrade": "14",
                            }
                        },
                    }
                }
            ]
        }
    }

    class _Resp:
        def read(self):
            import json

            return json.dumps(fake).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    import urllib.request

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=0: _Resp())
    out = sources.search_usajobs(["data"])
    j = out[0]
    assert j["title"] == "IT Specialist (Data Management)"
    assert j["company"] == "National Institutes of Health"
    assert j["department"] == "Department of Health"
    # NORMALIZED from PositionSchedule[].Code — `.Name` is EMPTY on 47 of 50 live
    # rows and a shift pattern on the rest, so not one row in 50 produced a usable
    # employment type from it. The vendor's own string is preserved.
    assert j["employment_type"] == "FULL_TIME"
    assert j["employment_type_raw"] == "Full-Time"
    # The GRADE BAND, not the pay plan. `JobGrade[].Code` is "GS"/"ND"/"FV" — pay
    # PLANS — and putting one in `seniority` asserted something it is not.
    assert j["seniority"] == "GS-13/14"
    assert "120,000" in j["salary"]
    assert j["text"] == "Federal AI work."
    _assert_contract(out, "usajobs")


# ── google for jobs (the 0.5.0 flagship) ────────────────────────────────────
# This shipped with zero executed coverage of its parsing path, in the same release
# that added parser tests to six other adapters for exactly that omission. The
# registry test could not reach it either: with no SERPAPI_KEY it returns [] before
# parsing anything, so it was covered only in the sense of "did not crash".
def test_google_jobs_parser_maps_fields(monkeypatch):
    c = _cfg()
    monkeypatch.setattr(c, "env", lambda key: "test-key")
    monkeypatch.setattr(sources.time, "sleep", lambda s: None)
    monkeypatch.setattr(
        sources, "get_json", lambda url, *a, **k: SAMPLES["google_jobs"]
    )
    out = sources.search_google_jobs(["AI Engineer"])
    j = out[0]
    assert j["title"] == "AI Engineer" and j["company"] == "Acme"
    assert j["employment_type"] == "Full-time"
    # The product promise: route to the EMPLOYER, not to an aggregator, even though
    # Google listed LinkedIn first.
    assert j["url"] == "https://acme.wd5.myworkdayjobs.com/j/1"
    _assert_contract(out, "google_jobs")


def test_best_apply_link_prefers_the_employer_over_an_aggregator():
    """Google orders apply_options by its own preference, which routinely puts an
    aggregator first. A direct-to-company link is the whole reason this source
    exists, so the first NON-aggregator option wins regardless of position."""
    opts = [
        {"link": "https://www.linkedin.com/jobs/view/1"},
        {"link": "https://www.indeed.com/viewjob?jk=2"},
        {"link": "https://boards.greenhouse.io/acme/jobs/3"},
    ]
    assert sources._best_apply_link(opts) == "https://boards.greenhouse.io/acme/jobs/3"
    # All aggregators: keep the first rather than dropping the role entirely.
    aggs = [{"link": "https://www.linkedin.com/jobs/view/1"}]
    assert sources._best_apply_link(aggs) == "https://www.linkedin.com/jobs/view/1"
    # Nothing usable: fall back to whatever the caller supplied.
    assert sources._best_apply_link([], "https://share.example/1") == (
        "https://share.example/1"
    )


@pytest.mark.parametrize(
    "text,offset_days",
    [
        ("today", 0),
        ("just posted", 0),
        ("16 hours ago", 0),
        ("yesterday", 1),
        ("3 days ago", 3),
        ("2 weeks ago", 14),
        ("30+ days ago", 30),
    ],
)
def test_google_posted_resolves_relative_strings(text, offset_days):
    """Google reports recency as a relative string. Stored verbatim it would rot —
    '3 days ago' still says '3 days ago' a month later — so it is resolved to an
    absolute Eastern date at FETCH time."""
    from datetime import datetime, timedelta

    expected = (datetime.now(sources._ET) - timedelta(days=offset_days)).strftime(
        "%Y-%m-%d"
    )
    assert sources._google_posted(text) == expected


def test_google_posted_blanks_an_unparseable_string():
    assert sources._google_posted("sometime soon") == ""
    assert sources._google_posted("") == ""


def test_google_jobs_pagination_terminates(monkeypatch):
    """Each page is a billed SerpApi search, so a loop that fails to terminate is a
    real cost. Stop when the token stops coming, and never exceed the page budget."""
    c = _cfg()
    monkeypatch.setattr(c, "env", lambda key: "test-key")
    monkeypatch.setattr(c, "google_jobs_pages", 5)
    monkeypatch.setattr(sources.time, "sleep", lambda s: None)
    calls = []

    def paged(url, *a, **k):
        calls.append(url)
        if "account.json" in url:  # the free quota probe, not a search
            return {"plan_searches_left": 250}
        body = dict(SAMPLES["google_jobs"])
        # hand out a token twice, then stop
        searches = len(_searches(calls))
        if searches < 3:
            body["serpapi_pagination"] = {"next_page_token": f"tok{searches}"}
        return body

    monkeypatch.setattr(sources, "get_json", paged)
    out = sources.search_google_jobs(["AI Engineer"])
    assert len(_searches(calls)) == 3, (
        f"expected to stop when the token ran out, made {len(_searches(calls))}"
    )
    assert len(out) == 3


# ── Strand A: the remote-vs-place URL bugs ──────────────────────────────────
# All three were bugs in the URL that gets BUILT, not in what came back, so URL
# assertions are the only shape of test that could have caught them. Each one
# returned rows (or zero rows) without erroring, which is why they survived.


def _capture(monkeypatch, payload):
    """Record every URL built, return a fixed payload."""
    calls = []
    monkeypatch.setattr(
        sources, "get_json", lambda url, *a, **k: calls.append(url) or payload
    )
    monkeypatch.setattr(sources.time, "sleep", lambda s: None)
    return calls


@pytest.mark.parametrize("place", ["", "remote", "Remote", "anywhere", "any", "  "])
def test_adzuna_never_sends_a_work_arrangement_as_a_place(monkeypatch, place):
    """`where` resolves against Adzuna's PLACE hierarchy. Measured: `where=remote`
    returns 0 rows -- indistinguishable from "no such jobs" behind the adapter's
    `except NET_ERRORS: break`. The fix is NOT a blank `where` (55,052 rows at 2%
    actually remote); it is `what_and=remote` (15,500 at 84%)."""
    c = _cfg()
    monkeypatch.setattr(c, "env", lambda key: "k")
    monkeypatch.setattr(c, "location", place)
    calls = _capture(monkeypatch, {"results": []})
    sources.search_adzuna(["AI Engineer"])
    assert calls, "adzuna made no request at all"
    assert "where=" not in calls[0], f"sent {place!r} as a place: {calls[0]}"
    assert "what_and=remote" in calls[0], f"no remote filter applied: {calls[0]}"


def test_adzuna_still_sends_a_real_place(monkeypatch):
    """The remote branch must not cost the location search. A real place still goes
    in `where`, and must NOT pick up the remote keyword filter."""
    c = _cfg()
    monkeypatch.setattr(c, "env", lambda key: "k")
    monkeypatch.setattr(c, "location", "Louisville, KY")
    monkeypatch.setattr(c, "radius_miles", 50)
    calls = _capture(monkeypatch, {"results": []})
    sources.search_adzuna(["AI Engineer"])
    assert "where=Louisville%2C%20KY" in calls[0], calls[0]
    assert "what_and=remote" not in calls[0], calls[0]
    assert "distance=80" in calls[0], f"radius not converted to km: {calls[0]}"


def test_adzuna_distance_guard_shares_the_remote_predicate(monkeypatch):
    """`distance` is meaningless without a place. The guard used to test
    `location != "remote"` on its own, so "anywhere" slipped through and a radius was
    sent with no `where` to anchor it."""
    c = _cfg()
    monkeypatch.setattr(c, "env", lambda key: "k")
    monkeypatch.setattr(c, "location", "anywhere")
    monkeypatch.setattr(c, "radius_miles", 50)
    calls = _capture(monkeypatch, {"results": []})
    sources.search_adzuna(["AI Engineer"])
    assert "distance=" not in calls[0], f"radius sent with no place: {calls[0]}"


def _searches(calls):
    """Only the METERED search URLs. `search_google_jobs` also hits SerpApi's free
    /account.json to check remaining quota before spending any, and that call is not
    a search — asserting on calls[0] blindly would test the quota probe."""
    return [u for u in calls if "engine=google_jobs" in u]


def test_google_jobs_applies_the_work_from_home_filter(monkeypatch):
    """The adapter's own comment said Google treats "remote" as a filter, then it
    dropped the word and set NO filter -- so a remote search silently became an
    unfiltered nationwide one. `ltype=1` is the documented WFH filter."""
    c = _cfg()
    monkeypatch.setattr(c, "env", lambda key: "test-key")
    monkeypatch.setattr(c, "location", "remote")
    calls = _capture(monkeypatch, {"jobs_results": []})
    sources.search_google_jobs(["AI Engineer"])
    hit = _searches(calls)[0]
    assert "ltype=1" in hit, f"no work-from-home filter: {hit}"
    assert "location=" not in hit, hit


def test_google_jobs_sends_a_place_instead_of_the_filter(monkeypatch):
    c = _cfg()
    monkeypatch.setattr(c, "env", lambda key: "test-key")
    monkeypatch.setattr(c, "location", "Louisville, KY")
    calls = _capture(monkeypatch, {"jobs_results": []})
    sources.search_google_jobs(["AI Engineer"])
    hit = _searches(calls)[0]
    assert "location=Louisville%2C%20KY" in hit, hit
    assert "ltype=1" not in hit, "a place search must not also force WFH"


def _himalayas_lanes(calls):
    """Split captured URLs into the two lanes. They are different endpoints with
    different pagination, which is the whole point of the browse lane existing."""
    search = [c for c in calls if "/jobs/api/search" in c]
    browse = [c for c in calls if "/jobs/api?" in c]
    return search, browse


def test_himalayas_search_lane_pages_with_page_not_offset(monkeypatch):
    """The SEARCH endpoint takes `page`. Sending `offset` to it is silently ignored
    and returns page 1 forever -- so the adapter took 20 rows per query."""
    _cfg()
    monkeypatch.setattr(config.active().harvest_depth, "himalayas_max_pages", 3)
    monkeypatch.setattr(
        config.active().harvest_depth, "himalayas_browse_pages", 0
    )  # isolate the lane
    full = {"jobs": [{"title": f"r{i}"} for i in range(sources.HIMALAYAS_PAGE)]}
    calls = _capture(monkeypatch, full)
    sources.search_himalayas(["AI Engineer"])
    search, browse = _himalayas_lanes(calls)
    assert len(search) == 3, f"expected 3 search pages, made {len(search)}"
    assert "page=1" in search[0] and "page=3" in search[2], search
    assert not any("offset=" in c for c in search), "offset is ignored by search"
    assert not browse


def test_himalayas_search_lane_stops_on_a_short_page(monkeypatch):
    _cfg()
    monkeypatch.setattr(config.active().harvest_depth, "himalayas_max_pages", 10)
    monkeypatch.setattr(config.active().harvest_depth, "himalayas_browse_pages", 0)
    calls = _capture(monkeypatch, {"jobs": [{"title": "only one"}]})
    sources.search_himalayas(["AI Engineer"])
    search, _ = _himalayas_lanes(calls)
    assert len(search) == 1, f"kept paging past a short page: {len(search)}"


def test_himalayas_browse_lane_walks_offset(monkeypatch):
    """The BROWSE endpoint takes `offset` and reaches the whole corpus (~96,934
    measured) where search walls at ~8,020. It is a SECOND lane, not a replacement:
    `q` does nothing on browse."""
    _cfg()
    monkeypatch.setattr(
        config.active().harvest_depth, "himalayas_max_pages", 0
    )  # isolate the lane
    monkeypatch.setattr(config.active().harvest_depth, "himalayas_browse_pages", 3)
    full = {"jobs": [{"title": f"r{i}"} for i in range(sources.HIMALAYAS_PAGE)]}
    calls = _capture(monkeypatch, full)
    sources.search_himalayas([])
    _, browse = _himalayas_lanes(calls)
    assert len(browse) == 3, f"expected 3 browse pages, made {len(browse)}"
    assert "offset=0" in browse[0] and "offset=40" in browse[2], browse
    assert not any("q=" in c for c in browse), "q does nothing on the browse endpoint"


def test_himalayas_browse_stops_when_the_rows_age_out(monkeypatch):
    """Browse is DATE-ORDERED (measured: offset 0 -> 0 days, 20k -> 8 days, 60k -> 28
    days), so the age gate IS the budget. Once the newest row on a page is past
    max_age_days, everything after it is older -- stop rather than page a corpus the
    engine will discard."""
    c = _cfg()
    monkeypatch.setattr(c, "max_age_days", 30)
    monkeypatch.setattr(config.active().harvest_depth, "himalayas_max_pages", 0)
    monkeypatch.setattr(config.active().harvest_depth, "himalayas_browse_pages", 50)
    stale = {"jobs": [{"title": "old", "pubDate": "2020-01-01"} for _ in range(20)]}
    calls = _capture(monkeypatch, stale)
    sources.search_himalayas([])
    _, browse = _himalayas_lanes(calls)
    assert len(browse) == 1, f"kept paging past the age cutoff: {len(browse)} pages"


def test_himalayas_browse_respects_the_backstop_when_dates_are_missing(monkeypatch):
    """Undated rows must not disable the bound. Freshness is the budget; the page cap
    is the backstop that keeps a date-parsing failure from becoming a 4,900-request
    walk."""
    _cfg()
    monkeypatch.setattr(config.active().harvest_depth, "himalayas_max_pages", 0)
    monkeypatch.setattr(config.active().harvest_depth, "himalayas_browse_pages", 4)
    full = {"jobs": [{"title": f"r{i}"} for i in range(sources.HIMALAYAS_PAGE)]}
    calls = _capture(monkeypatch, full)
    sources.search_himalayas([])
    _, browse = _himalayas_lanes(calls)
    assert len(browse) == 4, f"backstop not honoured: {len(browse)}"


def test_google_jobs_stops_on_a_quota_error(monkeypatch):
    """SerpApi reports quota-exhausted as a JSON `error`, not an HTTP error. Retrying
    burns nothing useful, so surface it once and stop."""
    c = _cfg()
    monkeypatch.setattr(c, "env", lambda key: "test-key")
    monkeypatch.setattr(sources.time, "sleep", lambda s: None)
    monkeypatch.setattr(
        sources, "get_json", lambda url, *a, **k: {"error": "Your account has run out"}
    )
    assert sources.search_google_jobs(["AI Engineer"]) == []


# ── the contract, enforced across the WHOLE registry ────────────────────────
# The tests above check adapters one at a time, so a NEW adapter is covered only
# if its author remembers to add one. These two iterate the registries, so a
# non-conforming source cannot be merged even if nobody writes a test for it.
def test_every_breadth_adapter_is_registered_and_callable():
    for name, fn in sources.BREADTH_ALL.items():
        assert callable(fn), f"{name} is not callable"
        assert fn.__name__.startswith("search_"), (
            f"{name} -> {fn.__name__}: breadth adapters are search_*(queries)"
        )


def test_every_depth_adapter_is_registered_and_callable():
    for name, fn in sources.DEPTH_ALL.items():
        assert callable(fn), f"{name} is not callable"
        assert fn.__name__.startswith("fetch_"), (
            f"{name} -> {fn.__name__}: depth adapters are fetch_*(slug)"
        )


@pytest.mark.parametrize("name", sorted(sources.DEPTH_ALL))
def test_depth_adapter_survives_an_empty_feed(name, monkeypatch):
    """An empty board must yield an empty list, never None and never a crash —
    `engine._consume` iterates the result unconditionally.

    Note what this does NOT prove: with no rows there is no contract to check.
    That is `test_every_adapter_honours_the_posting_contract` below."""
    monkeypatch.setattr(sources, "get_json", lambda url: {})
    monkeypatch.setattr(sources, "post_json", lambda url, body: {})
    out = sources.DEPTH_ALL[name]("slug")
    assert out == [] or out is not None


@pytest.mark.parametrize("name", sorted(sources.BREADTH_ALL))
def test_breadth_adapter_survives_an_empty_response(name, monkeypatch):
    """Same, on the breadth side. Keyed sources short-circuit before any request,
    which is itself the behaviour under test."""
    _cfg()
    monkeypatch.setattr(sources, "get_json", lambda url: {})
    monkeypatch.setattr(sources, "post_json", lambda url, body: {})
    monkeypatch.setattr(sources.time, "sleep", lambda s: None)
    out = sources.BREADTH_ALL[name](["AI Engineer"])
    assert isinstance(out, list)


# ── one sample row per adapter, so the contract check has something to check ──
# The two tests above stub the transport with `{}`. Every one of the 17 adapters
# returns [] from that, so a contract assertion placed there iterates an empty
# list and passes for ANY adapter — including one that returns a list in
# `department`, which is the exact bug 0.5.0 shipped a fix for. That is what this
# table is for: each entry is the minimal real response shape for its adapter, so
# the parametrized test below actually produces a row and can actually judge it.
#
# Adding a source means adding its sample here. That is deliberate: the failure is
# a loud KeyError naming the missing adapter, not a silent pass.
_JOB = {
    "title": "AI Engineer",
    "position": "AI Engineer",
    "name": "AI Engineer",
    "jobTitle": "AI Engineer",
    "text": "AI Engineer",
    "company_name": "Acme",
    "companyName": "Acme",
    "company": "Acme",
    "location": "Remote",
    "jobGeo": "Anywhere",
    "candidate_required_location": "USA",
    "url": "https://acme.example/jobs/1",
    "absolute_url": "https://acme.example/jobs/1",
    "hostedUrl": "https://acme.example/jobs/1",
    "applicationLink": "https://acme.example/jobs/1",
    "application_url": "https://acme.example/jobs/1",
    "jobUrl": "https://acme.example/jobs/1",
    "link": "https://acme.example/jobs/1",
    "id": "1",
    "shortcode": "abc",
    "description": "Build RAG and agentic LLM systems.",
    "descriptionPlain": "Build RAG and agentic LLM systems.",
    "content": "Build RAG and agentic LLM systems.",
    "jobDescription": "Build RAG and agentic LLM systems.",
    "jobExcerpt": "Build RAG and agentic LLM systems.",
    "excerpt": "Build RAG and agentic LLM systems.",
    "short_description": "Build RAG and agentic LLM systems.",
    "publication_date": "2026-07-10T00:00:00",
    "pubDate": "2026-07-10 00:00:00",
    "updated_at": "2026-07-10T00:00:00Z",
    "createdAt": 1783699200000,
    "created_at": 1783699200,
    "publishedAt": "2026-07-10T00:00:00Z",
    "releasedDate": "2026-07-10T00:00:00Z",
    "posted_date": "2026-07-10T00:00:00Z",
    "date": "2026-07-10T00:00:00Z",
    "remote": True,
    "workplace_type": "Remote",
    "locations": [{"display_label": "Austin, TX", "country": "US"}],
    "departments": [{"name": "Engineering"}],
    "categories": {"location": "Remote", "team": "Engineering"},
}
_LOC_PARTS = {"city": "Austin", "region": "TX", "country": "US"}
SAMPLES = {
    # Each vendor nests differently — that nesting IS the thing under test, so the
    # samples cannot share one blob.
    "greenhouse": {"jobs": [{**_JOB, "location": {"name": "Remote - US"}}]},
    "lever": [_JOB],
    "ashby": {"jobs": [{**_JOB, "isRemote": True}]},
    "smartrecruiters": {"content": [{**_JOB, "location": _LOC_PARTS}]},
    "workable": {"jobs": [{**_JOB, "location": _LOC_PARTS}]},
    # Rippling's LIST carries five fields and no body; the detail call fills the rest.
    # get_json is monkeypatched to one payload, so the same dict is served for both —
    # which is why it holds the union of list and detail keys.
    "rippling": [
        {
            "uuid": "u1",
            "name": "AI Engineer",
            "department": {"id": "Eng", "label": "Engineering"},
            "url": "https://ats.rippling.com/acme/jobs/u1",
            "workLocation": {"label": "Remote (US)"},
        }
    ],
    "teamtailor": {
        "title": "Acme",  # the feed title IS the company
        "items": [
            {
                "id": "t1",
                "title": "AI Engineer",
                "url": "https://acme.teamtailor.com/jobs/1-ai-engineer",
                "date_published": "2026-07-10T09:33:15+02:00",
                "content_html": "<p>Build RAG and agentic LLM systems.</p>",
                "_jobposting": {
                    "employmentType": "FULL_TIME",
                    "jobLocationType": "TELECOMMUTE",
                    "jobLocation": {
                        "address": {
                            "addressLocality": "Austin",
                            "addressRegion": "TX",
                            "addressCountry": "US",
                        }
                    },
                },
            }
        ],
    },
    "themuse": {
        "page": 0,
        "results": [
            {
                "id": 1,
                "name": "AI Engineer",
                "contents": "<p>Build RAG and agentic LLM systems.</p>",
                "publication_date": "2026-07-10T00:00:00Z",
                "type": "Full Time",
                "company": {"name": "Acme"},
                "locations": [{"name": "Austin, TX"}],
                "categories": [{"name": "Data Science"}],
                "levels": [{"name": "Senior Level"}],
                "refs": {
                    "landing_page": "https://www.themuse.com/jobs/acme/ai-engineer"
                },
            }
        ],
    },
    "workday": {
        "total": 1,
        "jobPostings": [
            {
                "title": "AI Engineer",
                "externalPath": "/job/Remote/AI-Engineer_R1",
                "locationsText": "Remote, US",
                "postedOn": "Posted 3 Days Ago",
            }
        ],
    },
    "remotive": {"jobs": [_JOB]},
    "jobicy": {"jobs": [_JOB]},
    "arbeitnow": {"data": [_JOB]},
    "remoteok": [{"legal": "notice"}, _JOB],
    "himalayas": {"jobs": [_JOB]},
    "braintrust": {
        "results": [
            {
                "id": 1,
                "title": "AI Engineer",
                "employer": {"name": "Acme"},
                "locations": [{"name": "Anywhere"}],
                "created": "2026-07-10T00:00:00Z",
                "contract_type": "hourly",
            }
        ]
    },
    "adzuna": {
        "results": [
            {
                "title": "AI Engineer",
                "company": {"display_name": "Acme"},
                "location": {"display_name": "Remote"},
                "redirect_url": "https://adzuna.example/1",
                "created": "2026-07-10T00:00:00Z",
                "category": {"label": "IT Jobs"},
                "contract_time": "full_time",
                "description": "Build RAG and agentic LLM systems.",
            }
        ]
    },
    "google_jobs": {
        "jobs_results": [
            {
                "title": "AI Engineer",
                "company_name": "Acme",
                "location": "Anywhere",
                "description": "Build RAG and agentic LLM systems.",
                "detected_extensions": {
                    "posted_at": "3 days ago",
                    "schedule_type": "Full-time",
                },
                "apply_options": [
                    {"title": "LinkedIn", "link": "https://linkedin.com/jobs/1"},
                    {"title": "Acme", "link": "https://acme.wd5.myworkdayjobs.com/j/1"},
                ],
            }
        ]
    },
    "usajobs": {
        "SearchResult": {
            "SearchResultItems": [
                {
                    "MatchedObjectDescriptor": {
                        "PositionTitle": "IT Specialist",
                        "OrganizationName": "NIH",
                        "DepartmentName": "HHS",
                        "PositionLocationDisplay": "Bethesda, Maryland",
                        "PositionURI": "https://www.usajobs.gov/job/1",
                        "PublicationStartDate": "2026-07-10",
                        # `.Code`, because `.Name` is EMPTY on 47 of 50 live rows
                        # and a shift pattern on the rest — see the adapter comment.
                        "PositionSchedule": [{"Code": "1", "Name": "Full-Time"}],
                        "JobGrade": [{"Code": "GS"}],
                        "PositionRemuneration": [
                            {"MinimumRange": "120000", "MaximumRange": "150000"}
                        ],
                        "UserArea": {
                            "Details": {
                                "JobSummary": "Federal AI work.",
                                "LowGrade": "13",
                                "HighGrade": "14",
                            }
                        },
                    }
                }
            ]
        }
    },
    # HN parses free-text "Who is hiring" COMMENTS, not a job object, so a job-shaped
    # sample would misrepresent it. Its own parser test covers the text path.
    # BOTH calls this adapter makes, in one payload. `search_by_date` returns the
    # thread list and `items/{id}` returns its comment tree, and a stub that answers
    # every URL with this dict satisfies both — which is what lets hn be held to the
    # same contract as every other adapter instead of being special-cased out of it.
    "hn": {
        "hits": [
            {"title": "Ask HN: Who is hiring? (August 2026)", "objectID": "aug"},
            {"title": "Ask HN: Who is hiring? (July 2026)", "objectID": "jul"},
        ],
        "children": [
            {
                "id": 1,
                "created_at": "2026-08-01T12:00:00.000Z",
                "text": "Acme | AI Engineer | Remote (US) | Full-time | "
                "https://acme.example/jobs/1",
            }
        ],
    },
}


@pytest.mark.parametrize(
    "name", sorted(set(sources.DEPTH_ALL) | set(sources.BREADTH_ALL))
)
def test_every_adapter_honours_the_posting_contract(name, monkeypatch):
    """THE contract test. Feed each adapter its own real response shape and judge
    the row it produces — the types the CSV writer and the scorer depend on.

    An adapter that yields no row from its sample fails loudly rather than passing
    vacuously, because a silent zero-row pass is precisely how the previous version
    of this test managed to approve a deliberately broken adapter."""
    assert name in SAMPLES, f"no SAMPLE payload for adapter {name!r} — add one"
    c = _cfg()
    # Keyed sources return [] before making a request, which is how three of them
    # used to slip through this test entirely. Hand them a key so they really parse.
    monkeypatch.setattr(c, "env", lambda key: "test-key")
    monkeypatch.setattr(sources, "get_json", lambda url, *a, **k: SAMPLES[name])
    monkeypatch.setattr(sources, "post_json", lambda url, body, *a, **k: SAMPLES[name])
    monkeypatch.setattr(sources.time, "sleep", lambda s: None)
    # USAJOBS builds its own Request rather than going through get_json.
    _usajobs_response(monkeypatch, SAMPLES["usajobs"])

    if name in sources.DEPTH_ALL:
        out = sources.DEPTH_ALL[name]("slug")
        required = REQUIRED_KEYS  # company is supplied by engine._consume
    else:
        out = sources.BREADTH_ALL[name](["AI Engineer"])
        required = BREADTH_REQUIRED_KEYS

    assert out, f"{name}: produced no row from its own sample payload"
    _assert_contract(out, required=required)


# ── the three adapters added in 0.7.0 ───────────────────────────────────────
def test_rippling_detail_fills_what_the_list_omits(monkeypatch):
    """The list endpoint has no body, no date and one location. Everything that
    makes a row rankable comes from the per-job detail call, so the parser is
    tested across BOTH responses rather than the list alone."""
    listing = [
        {
            "uuid": "u1",
            "name": "AI Engineer",
            "department": {"id": "Eng", "label": "Engineering"},
            "url": "https://ats.rippling.com/acme/jobs/u1",
            "workLocation": {"label": "Remote (US)"},
        }
    ]
    detail = {
        "description": {"company": "About Acme.", "role": "Build RAG systems."},
        "createdOn": "2023-10-31T10:40:35.194000-07:00",
        # INVERTED on purpose: `id` is the human string, `label` the code.
        "employmentType": {"label": "SALARIED_FT", "id": "Salaried, full-time"},
        "workLocations": ["New York, NY", "Remote (Massachusetts, US)"],
    }
    monkeypatch.setattr(
        sources, "get_json", lambda url: detail if "/jobs/u1" in url else listing
    )
    out = sources.fetch_rippling("acme")
    assert len(out) == 1
    j = out[0]
    assert j["title"] == "AI Engineer"
    # role BEFORE company: a truncated body must keep the part describing the job
    assert j["text"].startswith("Build RAG systems.")
    assert "About Acme." in j["text"]
    assert j["posted"] == "2023-10-31"
    assert j["employment_type"] == "Salaried, full-time"  # not SALARIED_FT
    # the detail's multi-location array replaces the list's single label
    assert j["location"] == "New York, NY; Remote (Massachusetts, US)"
    _assert_contract(out, required=REQUIRED_KEYS)


def test_rippling_survives_a_detail_call_that_fails(monkeypatch):
    """A dead detail endpoint costs a body, never the board."""
    listing = [{"uuid": "u1", "name": "AI Engineer", "url": "u", "workLocation": {}}]

    def boom(url):
        if "/jobs/u1" in url:
            raise ValueError("upstream junk")
        return listing

    monkeypatch.setattr(sources, "get_json", boom)
    out = sources.fetch_rippling("acme")
    assert len(out) == 1 and out[0]["title"] == "AI Engineer"
    _assert_contract(out, required=REQUIRED_KEYS)


def test_rippling_liveness_never_touches_the_detail_endpoint(monkeypatch):
    """The whole point of the cheap variant: 1 request, not 1-per-role."""
    urls = []

    def spy(url):
        urls.append(url)
        return [{"uuid": "u1"}, {"uuid": "u2"}]

    monkeypatch.setattr(sources, "get_json", spy)
    assert sources.live_rippling("acme") == 2
    assert len(urls) == 1, f"liveness made {len(urls)} requests: {urls}"


def test_teamtailor_reads_the_schema_org_block(monkeypatch):
    """The feed carries body and date; `_jobposting` supplies the location and
    employment type the JSON Feed itself omits."""
    monkeypatch.setattr(sources, "get_json", lambda url: SAMPLES["teamtailor"])
    out = sources.fetch_teamtailor("acme")
    assert len(out) == 1
    j = out[0]
    assert j["title"] == "AI Engineer"
    assert j["posted"] == "2026-07-10"
    assert j["employment_type"] == "FULL_TIME"
    assert j["location"] == "Austin, TX, US (Remote)"  # TELECOMMUTE -> the suffix
    assert "RAG" in j["text"]
    _assert_contract(out, required=REQUIRED_KEYS)


def test_themuse_ignores_a_query_because_it_cannot_search(monkeypatch):
    """`title_search: false`, measured across nine parameter names. The adapter takes
    `queries` for signature parity and must NOT put them in the URL — a query that
    looks applied but isn't is the failure mode this asserts against."""
    urls = []

    def spy(url):
        urls.append(url)
        return SAMPLES["themuse"] if "page=0" in url else {"results": []}

    monkeypatch.setattr(sources, "get_json", spy)
    monkeypatch.setattr(sources.time, "sleep", lambda s: None)
    out = sources.search_themuse(["registered nurse"])
    assert urls, "no request made"
    for u in urls:
        assert "nurse" not in u.lower(), f"query leaked into the URL: {u}"
    j = out[0]
    assert j["company"] == "Acme"
    assert j["department"] == "Data Science"  # a job FAMILY, not an org unit
    assert j["url"] == "https://www.themuse.com/jobs/acme/ai-engineer"
    _assert_contract(out, "themuse")


def _themuse_page(url):
    import re as _re

    return int(_re.search(r"[?&]page=(\d+)", url).group(1))


def test_themuse_stops_at_the_page_cap(monkeypatch):
    """Page 100 is a hard 400. Never walk past THEMUSE_PAGE_CAP however high
    THEMUSE_MAX_PAGES is set — and the cap applies PER CATEGORY SLICE."""
    monkeypatch.setattr(config.active().harvest_depth, "themuse_max_pages", 500)
    monkeypatch.setattr(sources.time, "sleep", lambda s: None)
    pages = []

    def spy(url):
        pages.append(_themuse_page(url))
        return SAMPLES["themuse"]

    monkeypatch.setattr(sources, "get_json", spy)
    sources.search_themuse([])
    # Assert the LITERAL vendor bound, not sources.THEMUSE_PAGE_CAP. Comparing the
    # code against its own constant catches "the loop vanished" but never "the bound
    # is wrong" — and 99 is a fact about The Muse (page 100 is a 400 "Value page is
    # too high"), not a preference of ours.
    assert max(pages) <= 99, f"walked to page {max(pages)} — page 100 is a hard 400"
    assert max(pages) == 99, "never reached the cap, so the bound was not exercised"


def test_themuse_fans_out_over_every_category(monkeypatch):
    """The unfiltered feed hard-caps at 2,000 rows, and the cap applies PER SLICE, so
    the category fan-out is the ONLY way past it. Without this the adapter returned
    100 rows out of ~36,060 reachable.

    This is the test that would have caught the original miss: the adapter looked
    healthy, returned rows, and passed every shape assertion while querying one
    twentieth of the source.
    """
    from urllib.parse import unquote

    monkeypatch.setattr(config.active().harvest_depth, "themuse_max_pages", 1)
    monkeypatch.setattr(sources.time, "sleep", lambda s: None)
    urls = []
    monkeypatch.setattr(
        sources, "get_json", lambda url: urls.append(url) or SAMPLES["themuse"]
    )
    sources.search_themuse([])
    asked = {unquote(u.split("category=", 1)[1]) for u in urls if "category=" in u}
    # Pinned against the LITERAL taxonomy measured on 2026-08-05, not against
    # sources.THEMUSE_CATEGORIES — asserting the code matches its own constant would
    # pass just as happily if someone deleted eighteen of them. Every value here was
    # probed individually: each returns a page_count distinct from the unfiltered feed
    # and 20/20 rows carrying that exact category.
    expected = {
        "Account Management",
        "Accounting and Finance",
        "Advertising and Marketing",
        "Animal Care",
        "Business Operations",
        "Data and Analytics",
        "Education",
        "Food and Hospitality Services",
        "Healthcare",
        "Human Resources and Recruitment",
        "Installation, Maintenance, and Repairs",
        "Legal Services",
        "Management",
        "Product Management",
        "Project Management",
        "Retail",
        "Sales",
        "Science and Engineering",
        "Software Engineering",
        "Unknown",
    }
    assert asked == expected, f"taxonomy drifted: {expected ^ asked}"
    assert len(urls) == 20


def test_themuse_dedups_a_job_that_spans_two_categories(monkeypatch):
    """A posting can carry several categories, so the same job legitimately comes
    back in more than one slice. Cross-slice dedup is required, not defensive."""
    monkeypatch.setattr(config.active().harvest_depth, "themuse_max_pages", 1)
    monkeypatch.setattr(sources.time, "sleep", lambda s: None)
    monkeypatch.setattr(sources, "get_json", lambda url: SAMPLES["themuse"])
    out = sources.search_themuse([])
    assert len(out) == 1, f"same job emitted {len(out)} times across slices"


def test_themuse_emits_the_seniority_it_was_holding_back(monkeypatch):
    """`levels` is a real seniority string. It was withheld while `seniority` was a
    contract key no adapter filled; the contract exists now."""
    monkeypatch.setattr(config.active().harvest_depth, "themuse_max_pages", 1)
    monkeypatch.setattr(sources.time, "sleep", lambda s: None)
    monkeypatch.setattr(sources, "get_json", lambda url: SAMPLES["themuse"])
    out = sources.search_themuse([])
    assert out[0]["seniority"], "levels -> seniority is still unmapped"


# ── Strand B: the record contract ───────────────────────────────────────────
# These are the gates BUILD-0.7.0 names. The department byte-identity test is the
# COMPATIBILITY gate: jobfitr pins a released job-radar and reads that column, so if
# it goes red the release breaks a consumer at a minor version.


def _harvest_one(fn, payload, monkeypatch, **kw):
    """Run one adapter against a fixture and return its first record, normalized
    through the engine boundary exactly as a real harvest would be."""
    from job_radar import engine

    monkeypatch.setattr(sources, "get_json", lambda url, *a, **k: payload)
    monkeypatch.setattr(sources, "post_json", lambda url, body, *a, **k: payload)
    monkeypatch.setattr(sources.time, "sleep", lambda s: None)
    rows = fn(**kw) if kw else fn("slug")
    return engine._coerce(rows[0]) if rows else None


def test_every_contract_key_is_present_even_when_unknown(monkeypatch):
    """The contract is "these keys always exist", not "these keys exist when the
    source is rich". A consumer must never have to distinguish a missing key from an
    unknown value."""
    from job_radar import engine

    r = engine._coerce({"title": "x", "source": "test"})
    # computed from other fields, so not None even on a bare record
    derived = {"title_root", "harvested_at", "remote", "direct_apply"}
    for k in engine._CONTRACT_FIELDS:
        assert k in r, f"{k} missing from the record contract"
        if k not in derived:
            assert r[k] is None, f"{k} defaulted to {r[k]!r}, not None"


def test_unknown_remote_is_none_not_false(monkeypatch):
    """`None` != `False`. A source that does not say is not saying "not remote", and
    a store that cannot tell them apart asserts things nobody measured."""
    from job_radar import engine

    r = engine._coerce({"title": "Engineer", "text": "no arrangement stated"})
    assert r["remote_type"] is None
    assert r["remote"] is None
    assert r["remote"] is not False


def test_coerce_does_not_stringify_the_typed_fields():
    """The legacy text fields are forced to str; the contract fields must NOT be, or
    None becomes the string "None" and a list becomes its repr."""
    from job_radar import engine

    r = engine._coerce({"title": None, "remote_type": "remote", "tags": ["a", "b"]})
    assert r["title"] == ""  # required field: coerced to str
    assert r["remote_type"] == "remote"  # typed field: untouched
    assert r["remote"] is True  # derived from remote_type
    assert r["tags"] == ["a", "b"]
    assert engine._coerce({"tags": "solo"})["tags"] == ["solo"]  # scalar -> list


def test_adzuna_area_depth_maps_state_and_remote():
    """`area` is a hierarchy of varying depth. `area == ['US']` EXACTLY means
    nationwide/remote; depth 5 shifts city one slot but never the state."""
    assert sources._adzuna_place(["US"]) == (None, None, "US", True)
    city, state, country, remote = sources._adzuna_place(
        ["US", "Texas", "Howard County", "Big Spring"]
    )
    assert (city, state, country, remote) == ("Big Spring", "Texas", "US", None)
    city5, state5, _, _ = sources._adzuna_place(
        ["US", "New York", "New York City", "Manhattan", "Prince"]
    )
    assert state5 == "New York", "state must stay at area[1] at depth 5"
    assert city5 == "Prince"
    assert sources._adzuna_place(None) == (None, None, None, None)


def test_usajobs_employer_is_not_a_category(monkeypatch):
    """`DepartmentName` is "Department of Veterans Affairs" -- the EMPLOYER. It stays
    in `department` byte-identically for compatibility, and is now also stated as what
    it actually is."""
    place = sources._usajobs_place(
        [{"CityName": "Austin", "CountrySubDivisionCode": "TX", "CountryCode": "US"}]
    )
    assert place == {"city": "Austin", "state": "TX", "country": "US"}
    assert sources._usajobs_place(None)["state"] is None


def test_lever_workplace_type_is_read_not_inferred():
    assert sources._lever_remote("remote")["remote_type"] == "remote"
    # hybrid is now EXPRESSIBLE. It used to collapse to `remote: False`, which reads
    # as on-site to anything checking only the flag — a third of the middle ground
    # reported as its opposite.
    assert sources._lever_remote("hybrid")["remote_type"] == "hybrid"
    assert sources._lever_remote("onsite")["remote_type"] == "onsite"
    assert sources._lever_remote("")["remote_type"] is None  # unknown, not onsite
    assert sources._lever_remote("something new")["remote_type"] is None


def test_ashby_place_reads_the_schema_org_block():
    got = sources._ashby_place(
        {
            "postalAddress": {
                "addressLocality": "Denver",
                "addressRegion": "CO",
                "addressCountry": "US",
            }
        }
    )
    assert got == {"city": "Denver", "state": "CO", "country": "US"}
    assert sources._ashby_place(None)["city"] is None


# ── Strand K: retrieval — detail-deferral, paging, fan-out ──────────────────
# Every one of these is a bug in HOW MANY requests get made and which rows come
# back. They are asserted on the URLs built and the call counts, because that is
# where the defect lives — each adapter returned rows and looked healthy.


def test_workday_buys_bodies_only_for_titles_that_survive_the_gate(monkeypatch):
    """The detail pass is the most expensive thing in a harvest — one request per
    role. The list endpoint already carries the title and the relevance gate reads
    nothing else, so the gate can run BEFORE the bodies are bought.

    Measured across the ten shipped Workday employers: 903 requests for 6,922 roles
    with the gate, versus 1,663 for 1,583 roles without it.
    """
    listing = {
        "total": 3,
        "jobPostings": [
            {"title": "AI Engineer", "externalPath": "/job/1", "bulletFields": []},
            {
                "title": "Senior Accountant",
                "externalPath": "/job/2",
                "bulletFields": [],
            },
            {
                "title": "Warehouse Associate",
                "externalPath": "/job/3",
                "bulletFields": [],
            },
        ],
    }
    details = []
    monkeypatch.setattr(sources, "post_json", lambda url, body, *a, **k: listing)
    monkeypatch.setattr(
        sources,
        "get_json",
        lambda url, *a, **k: details.append(url) or {"jobPostingInfo": {}},
    )
    rows = sources.fetch_workday(
        "t", host="wd1", site="s", keep=lambda t: "engineer" in t.lower()
    )
    assert [r["title"] for r in rows] == ["AI Engineer"]
    assert len(details) == 1, f"bought {len(details)} bodies for 1 kept role"


def test_workday_without_a_gate_is_unchanged(monkeypatch):
    """keep=None must preserve the old behaviour exactly — a direct caller that does
    not filter is not silently given a smaller board."""
    listing = {
        "total": 2,
        "jobPostings": [
            {"title": "AI Engineer", "externalPath": "/job/1", "bulletFields": []},
            {
                "title": "Senior Accountant",
                "externalPath": "/job/2",
                "bulletFields": [],
            },
        ],
    }
    details = []
    monkeypatch.setattr(sources, "post_json", lambda url, body, *a, **k: listing)
    monkeypatch.setattr(
        sources,
        "get_json",
        lambda url, *a, **k: details.append(url) or {"jobPostingInfo": {}},
    )
    rows = sources.fetch_workday("t", host="wd1", site="s")
    assert len(rows) == 2 and len(details) == 2


def test_rippling_gates_before_its_detail_pass(monkeypatch):
    board = [
        {"uuid": "a", "name": "AI Engineer", "url": "u1"},
        {"uuid": "b", "name": "Facilities Coordinator", "url": "u2"},
    ]
    calls = []

    def _get(url, *a, **k):
        calls.append(url)
        return board if url.endswith("/jobs") else {"description": {"role": "x"}}

    monkeypatch.setattr(sources, "get_json", _get)
    rows = sources.fetch_rippling("s", keep=lambda t: "engineer" in t.lower())
    assert [r["title"] for r in rows] == ["AI Engineer"]
    assert len(calls) == 2, f"expected 1 list + 1 detail, made {len(calls)}"


def test_engine_hands_the_gate_only_to_adapters_that_declare_it():
    """DEPTH_ACCEPTS_KEEP is the opt-in. An adapter not in it must never be called
    with a `keep` kwarg it does not accept — that would be a TypeError mid-harvest."""
    import inspect

    for ats in sources.DEPTH_ACCEPTS_KEEP:
        sig = inspect.signature(sources.DEPTH_ALL[ats])
        assert "keep" in sig.parameters, (
            f"{ats} is in DEPTH_ACCEPTS_KEEP but has no keep"
        )
    for ats, fn in sources.DEPTH_ALL.items():
        if ats in sources.DEPTH_ACCEPTS_KEEP:
            continue
        assert "keep" not in inspect.signature(fn).parameters, (
            f"{ats} accepts keep but is not declared in DEPTH_ACCEPTS_KEEP — the "
            "engine will never pass it, so the deferral silently does nothing"
        )


def test_smartrecruiters_pages_past_the_hundred_row_clamp(monkeypatch):
    """The server CLAMPS at 100 and says nothing: `?limit=200` returns 100 rows and
    echoes `limit: 100`. So the single call this adapter used to make took 100 of
    4,716 rows on a real board (boschgroup) and reported success — 97.9% gone.

    The module already knew: live_smartrecruiters returns `totalFound` and feeds
    discovery's role-count sort while the fetch returned 100. Two functions in one
    file disagreeing by 46x.
    """
    page = {
        "totalFound": 250,
        "content": [{"name": f"Role {i}", "id": str(i)} for i in range(100)],
    }
    tail = {"totalFound": 250, "content": [{"name": "Last", "id": "x"}]}
    calls = []

    def _get(url, *a, **k):
        calls.append(url)
        return page if len(calls) < 3 else tail

    monkeypatch.setattr(sources, "get_json", _get)
    monkeypatch.setattr(sources.time, "sleep", lambda s: None)
    out = sources.fetch_smartrecruiters("boschgroup")
    assert len(out) == 201, f"expected 100+100+1 rows, got {len(out)}"
    assert "offset=0" in calls[0] and "offset=100" in calls[1], calls
    assert all("limit=100" in c for c in calls), "limit must stay at the real max"


def test_smartrecruiters_believes_total_found(monkeypatch):
    """`totalFound` announces the end. Stop there rather than paying for a page that
    can only come back short."""
    full = {
        "totalFound": 100,
        "content": [{"name": f"Role {i}", "id": str(i)} for i in range(100)],
    }
    calls = _capture(monkeypatch, full)
    sources.fetch_smartrecruiters("acme")
    assert len(calls) == 1, f"paged past totalFound: {len(calls)} calls"


def test_smartrecruiters_honours_its_page_cap(monkeypatch):
    """Bosch alone is 48 requests and this runs per-company across a watchlist, so
    the loop is bounded."""
    monkeypatch.setattr(config.active().harvest_depth, "smartrecruiters_max_pages", 3)
    full = {
        "totalFound": 99999,
        "content": [{"name": f"Role {i}", "id": str(i)} for i in range(100)],
    }
    calls = _capture(monkeypatch, full)
    sources.fetch_smartrecruiters("acme")
    assert len(calls) == 3, f"cap not honoured: {len(calls)}"


def test_usajobs_pages_with_the_page_parameter(monkeypatch):
    """The adapter built ONE url per query and never sent `&Page=`, so any keyword
    with more than one page was silently truncated — measured in catalog/usajobs.md
    at 736 and 620 matches against a 500-row page."""
    c = _cfg()
    monkeypatch.setattr(c, "env", lambda k: "x")
    monkeypatch.setattr(c, "usajobs_results_per_page", 2)
    monkeypatch.setattr(c, "usajobs_max_pages", 5)
    monkeypatch.setattr(sources.time, "sleep", lambda s: None)
    calls = []
    import urllib.request

    class _Resp:
        def __init__(self, body):
            self._b = body

        def read(self):
            import json as _json

            return _json.dumps(self._b).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _open(req, timeout=0):
        calls.append(req.full_url)
        item = {"MatchedObjectDescriptor": {"PositionTitle": "Nurse"}}
        # total 5 at 2/page -> pages 1,2 full, page 3 short
        n = 2 if len(calls) < 3 else 1
        return _Resp(
            {
                "SearchResult": {
                    "SearchResultItems": [item] * n,
                    "SearchResultCountAll": 5,
                }
            }
        )

    monkeypatch.setattr(urllib.request, "urlopen", _open)
    out = sources.search_usajobs(["nurse"])
    assert len(calls) == 3, f"expected 3 pages, made {len(calls)}"
    assert "Page=1" in calls[0] and "Page=3" in calls[2], calls
    assert len(out) == 5


def test_hn_reads_two_threads_not_one(monkeypatch):
    """One thread is the start-of-month cliff: on the 1st the newest thread is nearly
    empty and the prior month's rows vanish. Measured 2026-08-04: 138 vs 245."""
    hits = {
        "hits": [
            {"title": "Ask HN: Who is hiring? (August 2026)", "objectID": "aug"},
            {"title": "Ask HN: Who is hiring? (July 2026)", "objectID": "jul"},
            {"title": "Ask HN: Who is hiring? (June 2026)", "objectID": "jun"},
        ]
    }
    seen = []

    def _get(url, *a, **k):
        if "search_by_date" in url:
            return hits
        tid = url.rsplit("/", 1)[1]
        seen.append(tid)
        return {"children": [{"text": f"Co{tid} | Engineer | Remote", "id": 1}]}

    monkeypatch.setattr(sources, "get_json", _get)
    out = sources.search_hn_whoishiring([])
    assert seen == ["aug", "jul"], f"read {seen}, expected the two newest"
    assert len(out) == 2


# ── the compatibility gate ──────────────────────────────────────────────────
def test_department_is_byte_identical_to_0_6_0(monkeypatch):
    """THE COMPATIBILITY GATE. If this goes red, the release breaks a consumer.

    `department` is deprecated but still emitted, because jobfitr pins a released
    job-radar and reads that column today (as `category`). Removing or changing it
    before 1.0 would break a shipped consumer at a MINOR version.

    This is an equivalence test against history, not a self-consistent pin: it loads
    0.6.0's sources.py out of git, runs both versions against the SAME fixtures, and
    compares the department values. A pin written from the current code would only
    prove the code agrees with itself.

    Compared as a SET, deliberately. Row COUNTS changed on purpose in 0.7.0 —
    himalayas now runs two lanes, smartrecruiters pages — so counting rows would fail
    for the right reasons and hide the wrong ones. What must not change is the VALUE
    the mapping derives from a given posting.
    """
    import importlib.util
    import subprocess
    import sys

    src = subprocess.run(
        ["git", "show", "b839c81:job_radar/sources.py"],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).parent.parent),
    )
    if src.returncode != 0:
        # Skip ONLY where there is genuinely no history to read (an sdist, a wheel
        # test). Where .git exists and the blob still cannot be read, that is a
        # SHALLOW CLONE, and skipping is how this gate silently never ran in CI:
        # actions/checkout defaults to depth 1, and `pytest -q` prints a bare `s`
        # with no reason. A compatibility gate that quietly does not run reads as
        # assurance, which is worse than not having it. ci.yml now sets
        # fetch-depth: 0; this makes a regression there loud instead of invisible.
        if (Path(__file__).parent.parent / ".git").exists():
            pytest.fail(
                "0.6.0 blob unreachable but .git is present — shallow clone? "
                "The department compatibility gate cannot run. "
                "CI needs `fetch-depth: 0` on actions/checkout."
            )
        pytest.skip("no git history here (sdist/wheel test) — gate cannot run")
    blob = Path(tempfile.mkdtemp()) / "sources_060.py"
    blob.write_text(src.stdout, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("job_radar._v060_sources", blob)
    old = importlib.util.module_from_spec(spec)
    sys.modules["job_radar._v060_sources"] = old
    spec.loader.exec_module(old)

    restore = config.active()  # this test mutates the process global; put it back
    c = _cfg()
    # Keyed sources return [] before making a request. Counting those as "compared"
    # is how the anti-vacuity guard below stayed partly vacuous: adzuna, google_jobs,
    # usajobs and hn were all comparing [] == []. USAJOBS is the case the CHANGELOG
    # names as the sharp one (DepartmentName is the EMPLOYER), so a gate blind to it
    # was blind to its own headline example. Same three lines the contract test
    # above already uses.
    monkeypatch.setattr(c, "env", lambda key: "test-key")
    # USAJOBS builds its own urllib Request rather than going through get_json, so it
    # has to be stubbed a level lower — and it is the adapter whose department mapping
    # matters most here (DepartmentName is the EMPLOYER, the CHANGELOG's headline
    # example). A gate that skipped it was blind to its own motivating case.
    _usajobs_response(monkeypatch, SAMPLES["usajobs"])

    def departments(mod, name, depth):
        fn = (mod.DEPTH_ALL if depth else mod.BREADTH_ALL).get(name)
        if fn is None or name not in SAMPLES:
            return None
        og, op, osl = mod.get_json, mod.post_json, mod.time.sleep
        mod.get_json = lambda u, *a, **k: SAMPLES[name]
        mod.post_json = lambda u, b, *a, **k: SAMPLES[name]
        mod.time.sleep = lambda s: None
        try:
            rows = fn("slug") if depth else fn(["AI Engineer"])
        except Exception:  # noqa: BLE001 — a keyed source without a key returns []
            return None
        finally:
            mod.get_json, mod.post_json, mod.time.sleep = og, op, osl
        return sorted({r.get("department") for r in rows})

    compared, empty = [], []
    for name, depth in [
        (n, True) for n in sorted(set(old.DEPTH_ALL) & set(sources.DEPTH_ALL))
    ] + [(n, False) for n in sorted(set(old.BREADTH_ALL) & set(sources.BREADTH_ALL))]:
        before, after = departments(old, name, depth), departments(sources, name, depth)
        if before is None:
            continue
        assert before == after, f"{name}: department changed {before} -> {after}"
        # An adapter that produced NO rows compared [] == [] and proved nothing. Track
        # it separately rather than counting it toward the guard.
        (compared if before else empty).append(name)
    assert not empty, (
        f"{empty} yielded no rows, so the gate compared [] == [] for them and proved "
        "nothing. Hand them a key / stub their transport, or the gate is blind to "
        "exactly the adapters whose department mapping is most interesting."
    )
    assert len(compared) >= 8, (
        f"only compared {compared} — the gate proved almost nothing"
    )
    config.set_active(restore)


def test_a_null_title_does_not_cost_the_whole_employer(monkeypatch):
    """REGRESSION. The `keep` gate runs one layer UPSTREAM of engine._coerce, which
    is what made every title safe before anything called `.lower()` on it. A vendor
    `null` title therefore reached scoring.relevant raw and raised AttributeError —
    and because a depth adapter's exception is caught per-COMPANY, one malformed
    posting cost the entire board, good roles included.

    Asserted through engine.harvest, NOT the adapter, because the seam is the bug:
    the previous test in this file inspected adapter signatures and never called
    _fetch_company, so the engine->keep path had no coverage at all.
    """
    from job_radar import config as _config
    from job_radar import engine

    restore = _config.active()  # mutates the process global; put it back
    cfg = _config.Config()
    cfg.remote_only = False  # isolate the title path from the remote gate
    cfg.breadth_sources = []
    _config.set_active(cfg)

    listing = {
        "total": 2,
        "jobPostings": [
            {"title": None, "externalPath": "/j/1", "bulletFields": []},
            {"title": "AI Engineer", "externalPath": "/j/2", "bulletFields": []},
        ],
    }
    monkeypatch.setattr(sources, "post_json", lambda u, b, *a, **k: listing)
    monkeypatch.setattr(sources, "get_json", lambda u, *a, **k: {"jobPostingInfo": {}})
    rows, _, errors = engine.harvest(
        cfg,
        companies=[
            {
                "name": "Acme",
                "ats": "workday",
                "slug": "acme",
                "host": "wd1",
                "site": "s",
            }
        ],
    )
    assert errors == [], f"a null title sank the employer: {errors}"
    assert [r["title"] for r in rows] == ["AI Engineer"]

    board = [
        {"uuid": "a", "name": None, "url": "u1"},
        {"uuid": "b", "name": "AI Engineer", "url": "u2"},
    ]
    monkeypatch.setattr(
        sources,
        "get_json",
        lambda u, *a, **k: (
            board if u.endswith("/jobs") else {"description": {"role": "x"}}
        ),
    )
    rows, _, errors = engine.harvest(
        cfg, companies=[{"name": "Beta", "ats": "rippling", "slug": "beta"}]
    )
    assert errors == [], f"a null name sank the board: {errors}"
    assert [r["title"] for r in rows] == ["AI Engineer"]
    _config.set_active(restore)


def test_title_of_survives_every_shape_a_vendor_can_send():
    assert sources._title_of({"title": None}) == ""
    assert sources._title_of({}) == ""
    assert sources._title_of({"title": 123}) == "123"
    assert sources._title_of({"title": "AI Engineer"}) == "AI Engineer"


# ── structured salary (probed live 2026-08-05) ──────────────────────────────
def test_zero_is_not_a_salary():
    """RemoteOK sends salary_min and salary_max on ALL 100 rows of its feed and both
    are 0 — the keys exist, the data does not. Mapping that through would assert a
    salary of zero on every row, the same class of lie as remote: False for unknown."""
    from job_radar import vocab

    assert vocab.salary(0, 0)["salary_min"] is None
    assert vocab.salary(None, None)["salary_basis"] is None
    assert vocab.salary("0.00", "0.00")["salary_max"] is None


def test_a_period_is_never_guessed():
    """`65` and `135000` are both valid numbers, so a WRONG period makes every
    aggregate silently wrong — worse than no period at all. Adzuna sends no period,
    and its figures stay period-less however annual they look."""
    from job_radar import vocab

    assert vocab.salary(100, 200, "USD", None)["salary_period"] is None
    assert vocab.salary(100, 200, "USD", "nonsense")["salary_period"] is None


def test_the_probed_vendor_period_strings_all_map():
    """These are the exact strings the vendors send, and none of them would have been
    guessed: lever's interval, braintrust's payment_type, usajobs' RateIntervalCode."""
    from job_radar import vocab

    for raw, want in [
        ("per-year-salary", "year"),  # lever
        ("per-hour-wage", "hour"),  # lever
        ("annual", "year"),  # himalayas / braintrust
        ("hourly", "hour"),  # braintrust
        ("per_task", "fixed"),  # braintrust
        ("PA", "year"),  # usajobs RateIntervalCode
        ("Per Year", "year"),  # usajobs Description
    ]:
        assert vocab.salary_period(raw) == want, (
            f"{raw!r} -> {vocab.salary_period(raw)}"
        )


def test_every_period_in_the_map_is_legal():
    """A typo in the map is a test failure, not a value that silently escapes."""
    from job_radar import vocab

    assert set(vocab._PERIOD_MAP.values()) <= vocab.SALARY_PERIODS
    assert set(vocab._EMPLOYMENT_MAP.values()) <= vocab.EMPLOYMENT_TYPES
    assert set(vocab._REMOTE_MAP.values()) <= vocab.REMOTE_TYPES


def test_adzuna_predictions_never_reach_the_commitment_columns(monkeypatch):
    """MEASURED: 140 of 150 Adzuna rows carrying a salary were salary_is_predicted=1
    — 93%, and 100% for nursing and warehouse. They are point estimates (min == max,
    two decimals) from Adzuna's model, not figures an employer posted. In one column
    with real salaries, a forgotten WHERE clause silently poisons every average."""
    c = _cfg()
    monkeypatch.setattr(c, "env", lambda k: "k")
    monkeypatch.setattr(sources.time, "sleep", lambda s: None)
    payload = {
        "results": [
            {
                "title": "RN",
                "salary_min": 61482.41,
                "salary_max": 61482.41,
                "salary_is_predicted": "1",
                "redirect_url": "https://a/1",
            },
            {
                "title": "SWE",
                "salary_min": 115000,
                "salary_max": 145000,
                "salary_is_predicted": "0",
                "redirect_url": "https://a/2",
            },
        ]
    }
    monkeypatch.setattr(sources, "get_json", lambda url, *a, **k: payload)
    from job_radar import engine

    # Through _coerce, because that is what a consumer receives: the adapter emits
    # ONLY the estimate columns for a predicted row, and the boundary supplies the
    # rest as None.
    out = [engine._coerce(r) for r in sources.search_adzuna(["x"])]
    predicted, real = out[0], out[1]
    assert predicted["salary_min"] is None, "a prediction reached the commitment column"
    assert predicted["salary_estimated_min"] == 61482.41
    assert real["salary_min"] == 115000.0 and real["salary_basis"] == "stated"
    assert real.get("salary_estimated_min") is None


def test_usajobs_reads_its_rate_interval(monkeypatch):
    """No currency field exists — these are US federal postings, so USD is structural.
    RateIntervalCode carries the period."""
    c = _cfg()
    monkeypatch.setattr(c, "env", lambda k: "k")
    monkeypatch.setattr(sources.time, "sleep", lambda s: None)
    _usajobs_response(
        monkeypatch,
        {
            "SearchResult": {
                "SearchResultCountAll": 1,
                "SearchResultItems": [
                    {
                        "MatchedObjectDescriptor": {
                            "PositionTitle": "Nurse",
                            "PositionURI": "https://u/1",
                            "PositionRemuneration": [
                                {
                                    "MinimumRange": "105053",
                                    "MaximumRange": "136571",
                                    "RateIntervalCode": "PA",
                                    "Description": "Per Year",
                                }
                            ],
                        }
                    }
                ],
            }
        },
    )
    r = sources.search_usajobs(["nurse"])[0]
    assert r["salary_min"] == 105053.0  # arrives as a STRING from this API
    assert r["salary_period"] == "year" and r["salary_currency"] == "USD"
    assert r["salary_basis"] == "stated"


def test_google_salary_carries_its_period():
    """Google states pay as free text with the PERIOD EMBEDDED — "35-43 an hour",
    "2,140 a week" — and it is frequently NOT annual (probed 2026-08-05). Those
    numbers are meaningless without their unit: 35 and 2140 and 150000 in one column
    with no period makes every aggregate wrong, silently."""
    from job_radar import vocab

    got = vocab.google_salary("35–43 an hour")
    assert (got["salary_min"], got["salary_max"]) == (35.0, 43.0)
    assert got["salary_period"] == "hour" and got["salary_basis"] == "parsed"
    assert vocab.google_salary("2,140 a week")["salary_period"] == "week"
    # unparseable -> all None, never a number with a guessed period
    assert vocab.google_salary("competitive")["salary_min"] is None


def test_google_jobs_reads_its_structured_extensions(monkeypatch):
    """`detected_extensions` carries four things the adapter used to ignore:
    work_from_home (the only structured remote signal Google gives), schedule_type,
    a salary with its period, and posted_at — which is ALWAYS relative."""
    c = _cfg()
    monkeypatch.setattr(c, "env", lambda k: "test-key")
    monkeypatch.setattr(sources.time, "sleep", lambda s: None)
    payload = {
        "jobs_results": [
            {
                "title": "Intake Registered Nurse",
                "company_name": "Acme Health",
                "location": "Anywhere",
                "share_link": "https://g/1",
                "description": "care",
                "detected_extensions": {
                    "posted_at": "2 days ago",
                    "schedule_type": "Full-time",
                    "work_from_home": True,
                    "salary": "35–43 an hour",
                },
            }
        ]
    }
    monkeypatch.setattr(sources, "get_json", lambda url, *a, **k: payload)
    from job_radar import engine

    r = engine._coerce(sources.search_google_jobs(["nurse"])[0])
    assert r["remote_type"] == "remote" and r["remote_basis"] == "stated"
    assert r["employment_type"] == "FULL_TIME"
    assert r["employment_type_raw"] == "Full-time"
    assert r["posted_basis"] == "relative", "a computed date must not look stated"
    assert r["salary_min"] == 35.0 and r["salary_period"] == "hour"


def test_employment_type_is_normalized_once_for_every_adapter():
    """Nineteen vendors send nineteen spellings of the same eight ideas. Normalizing
    at the engine boundary rather than per-adapter means one place to be right,
    instead of nineteen chances to drift — and the vendor string is never lost."""
    from job_radar import engine, vocab

    for raw, want in [
        ("Full-time", "FULL_TIME"),
        ("Regular Full Time (Salary)", "FULL_TIME"),
        ("permanent", "FULL_TIME"),
        ("Contract", "CONTRACTOR"),
        ("Weird New Thing", "OTHER"),  # received but unrecognised != "did not say"
    ]:
        r = engine._coerce({"title": "x", "employment_type": raw})
        assert r["employment_type"] == want, f"{raw!r} -> {r['employment_type']!r}"
        assert r["employment_type_raw"] == raw, "the vendor's own string was destroyed"
        assert r["employment_type"] in vocab.EMPLOYMENT_TYPES
    silent = engine._coerce({"title": "x"})
    # None, NOT "". A source that said nothing is not a source that said "no type",
    # and "" is not a member of the closed vocabulary either. This asserted `== ""`
    # until 2026-08-05, when a live probe found the empty string on 680 of 2,747 rows
    # (24%) -- 100% of greenhouse, remoteok and teamtailor.
    assert silent["employment_type"] is None
    assert silent["employment_type_raw"] is None


def test_direct_apply_asks_whether_you_can_actually_apply_there(monkeypatch):
    """schema.org's question — "can you complete an application from this URL" — not
    "is it a depth adapter". The first version of this rule was `source in DEPTH_ALL`
    and it marked USAJOBS false, which is wrong: federal applications are SUBMITTED
    on usajobs.gov. It is the government's own system, not a board pointing at one."""
    from job_radar import engine

    for src, want in [
        ("greenhouse", True),  # the employer's own ATS
        ("workday", True),
        ("usajobs", True),  # applications are submitted here
        ("braintrust", True),  # client hidden by design; nowhere else to go
        ("remoteok", False),  # serves a redirect
        ("adzuna", False),  # its field is literally named redirect_url
    ]:
        r = engine._coerce({"title": "x", "source": src})
        assert r["direct_apply"] is want, f"{src} -> {r['direct_apply']}"


def test_google_jobs_decides_direct_apply_per_row(monkeypatch):
    """_best_apply_link already prefers the first non-aggregator option to CHOOSE the
    url — it just discarded the reasoning. A row whose only option is LinkedIn is not
    a direct apply, whatever its source."""
    c = _cfg()
    monkeypatch.setattr(c, "env", lambda k: "test-key")
    monkeypatch.setattr(sources.time, "sleep", lambda s: None)
    payload = {
        "jobs_results": [
            {
                "title": "A",
                "company_name": "Co",
                "share_link": "https://g/1",
                "apply_options": [{"link": "https://boards.greenhouse.io/co/jobs/1"}],
            },
            {
                "title": "B",
                "company_name": "Co",
                "share_link": "https://g/2",
                "apply_options": [{"link": "https://www.linkedin.com/jobs/view/2"}],
            },
        ]
    }
    monkeypatch.setattr(sources, "get_json", lambda url, *a, **k: payload)
    from job_radar import engine

    out = [engine._coerce(r) for r in sources.search_google_jobs(["x"])]
    assert out[0]["direct_apply"] is True, "an ATS link is a direct apply"
    assert out[1]["direct_apply"] is False, "a LinkedIn redirect is not"


def test_a_missing_deadline_stays_absent(monkeypatch):
    """Greenhouse sends `application_deadline` on every posting and it was NULL on
    all 397 of the board measured 2026-08-05 — the key exists, the data does not.
    An absent deadline must not become a fake date."""
    fake = {
        "jobs": [
            {
                "title": "Engineer",
                "absolute_url": "https://x/1",
                "application_deadline": None,
                "updated_at": "2026-08-01",
            }
        ]
    }
    monkeypatch.setattr(sources, "get_json", lambda url, *a, **k: fake)
    from job_radar import engine

    r = engine._coerce(sources.fetch_greenhouse("acme")[0])
    assert r["expires"] is None
    fake["jobs"][0]["application_deadline"] = "2026-09-30T00:00:00Z"
    r = engine._coerce(sources.fetch_greenhouse("acme")[0])
    assert r["expires"] == "2026-09-30"


def test_posted_and_its_basis_are_produced_together():
    """The basis is a property of HOW the date was derived, so it comes from the
    function that derives it — not from a boundary default and not from fifteen
    hand-set adapters that can drift.

    A boundary default would have been wrong in kind: _coerce sees a date string and
    cannot tell an ISO timestamp from arithmetic on "30+ days ago", so defaulting to
    "stated" would be right for sixteen adapters and an invisible lie for the two
    that compute. Same shape as defaulting seniority to "mid".
    """
    from job_radar.util import posted_from

    assert posted_from("2026-07-30T00:00:00Z") == {
        "posted": "2026-07-30",
        "posted_basis": "stated",
    }
    # An unparseable or absent date leaves the basis None — honestly unknown, never
    # a confident label on a value that does not exist.
    assert posted_from(None) == {"posted": "", "posted_basis": None}
    assert posted_from("not a date") == {"posted": "", "posted_basis": None}

    got = sources.posted_from_relative("Posted 26 Days Ago")
    assert got["posted"] and got["posted_basis"] == "relative"
    assert sources.posted_from_relative("")["posted_basis"] is None


@pytest.mark.parametrize(
    "name", sorted(set(sources.DEPTH_ALL) | set(sources.BREADTH_ALL))
)
def test_every_adapter_labels_where_its_date_came_from(name, monkeypatch):
    """A date with no basis is a date a consumer cannot weigh. Asserted across every
    adapter rather than trusting sixteen call sites to stay in step."""
    from job_radar import engine

    if name not in SAMPLES:
        pytest.skip(f"no SAMPLE payload for {name}")
    c = _cfg()
    monkeypatch.setattr(c, "env", lambda k: "test-key")

    def _get(url, *a, **k):
        # Rippling alone splits list and detail across two shapes, and the DATE only
        # exists on the detail object -- so a fixture that returns the list for both
        # would let this test pass while proving nothing (which is how it first failed).
        if name == "rippling" and "/jobs/" in str(url):
            return {"createdOn": "2026-07-28T00:00:00Z"}
        return SAMPLES[name]

    monkeypatch.setattr(sources, "get_json", _get)
    monkeypatch.setattr(sources, "post_json", lambda *a, **k: SAMPLES[name])
    monkeypatch.setattr(sources.time, "sleep", lambda s: None)
    _usajobs_response(monkeypatch, SAMPLES["usajobs"])

    rows = (
        sources.DEPTH_ALL[name]("slug")
        if name in sources.DEPTH_ALL
        else sources.BREADTH_ALL[name](["x"])
    )
    dated = 0
    for r in (engine._coerce(x) for x in rows):
        if r["posted"]:
            dated += 1
            assert r["posted_basis"] in ("stated", "relative"), (
                f"{name}: emitted a date with no basis — a consumer cannot tell a "
                "real timestamp from arithmetic on a phrase"
            )
    assert dated or not rows, f"{name}: no row carried a date, so nothing was proven"


# ── shared normalizers (probed live 2026-08-05) ─────────────────────────────
def test_country_is_one_vocabulary_across_sources():
    """Lever sends alpha-2 ('SG'), Ashby a display name ('Singapore'), USAJOBS
    'United States'. Passed through verbatim the column held three vocabularies and
    grouping by country split every country into pieces."""
    from job_radar.vocab import country_code

    assert country_code("Singapore") == "SG"
    assert country_code("SG") == "sg".upper()
    assert country_code("United States") == country_code("USA") == "US"
    # An unrecognised NAME is None, never a guess. A wrong country enters a database
    # once and never leaves.
    assert country_code("Freedonia") is None
    assert country_code("") is None and country_code(None) is None


def test_us_state_code_normalizes_the_name_usajobs_calls_a_code():
    from job_radar.vocab import us_state_code

    assert us_state_code("Louisiana") == "LA"
    assert us_state_code("CA") == "CA"
    assert us_state_code("Ontario") is None  # a real subdivision, not a US state


def test_split_place_refuses_to_guess_rather_than_inventing_a_city():
    """The tempting version splits on the comma and calls the second half a state.
    Several sources emit COUNTRY-first order, so that turns 'Taiwan, Taipei' into the
    city of Taiwan. A None costs a filter; a wrong city is a permanently wrong row."""
    from job_radar.vocab import split_place

    assert split_place("Waco, TX") == {"city": "Waco", "state": "TX", "country": "US"}
    assert split_place("Paris, France") == {
        "city": "Paris",
        "state": None,
        "country": "FR",
    }
    for ambiguous in ("Taiwan, Taipei", "Remote", "Kuala Lumpur", ""):
        assert split_place(ambiguous)["city"] is None, ambiguous
    # "ON" is a province; two letters cannot be told from a country code, so the
    # whole two-letter tail is refused rather than inventing the country "ON".
    assert split_place("Toronto, ON")["country"] is None


def test_split_place_reads_position_three_as_a_country_not_a_state():
    """Three parts settle what two cannot. In "Toronto, ON, CA" the region slot is
    already taken by ON, so CA is Canada -- and reading it as California made 25
    foreign rows American on a 21,495-row harvest, plus left 52 with no geography at
    all because their country code is not also a US state ("Curitiba, PR, br")."""
    from job_radar.vocab import split_place

    assert split_place("Toronto, ON, CA") == {
        "city": "Toronto",
        "state": None,  # non-US subdivisions have no canonical form in `state`
        "country": "CA",
    }
    assert split_place("Curitiba, PR, br")["country"] == "BR"
    assert split_place("Eschborn, HESSEN, de")["country"] == "DE"
    # a US three-part still resolves its state, because there the region IS a state
    assert split_place("Charlotte, NC, us") == {
        "city": "Charlotte",
        "state": "NC",
        "country": "US",
    }


def test_split_place_leaves_multi_location_strings_alone():
    """The guard is _KNOWN_COUNTRIES, not country_code(), which passes ANY two
    letters through. "San Francisco, CA, Seattle, WA" is several places in one
    string, not City/Region/Country -- WA is not a country, so it must fall through
    to the two-part logic rather than inventing the country Washington."""
    from job_radar.vocab import split_place

    assert split_place("San Francisco, CA, Seattle, WA")["country"] == "US"
    assert split_place("Mountain View, CA, Detroit, MI")["country"] == "US"
    # SEMICOLON and PIPE separated too -- the first cut of this rule guarded only the
    # comma case and would have turned 519 of these Canadian to fix 25.
    assert split_place("New York, NY; San Francisco, CA")["country"] == "US"
    assert split_place("New York City, NY | Seattle, WA")["country"] == "US"
    assert split_place("New York, NY; San Francisco, CA; Seattle, WA")["state"] == "WA"
    # and the plain shapes are untouched
    assert split_place("San Francisco, CA")["country"] == "US"
    assert split_place("Mountain View, CA, USA")["country"] == "US"


def test_split_place_resolves_a_bare_country_name():
    """The comma guard used to return before asking whether the whole string is a
    country. Measured downstream on a 31,790-row harvest: 1,591 rows are this shape
    and 539 are the literal "United States", so US identification was lost too.
    Only `country` is filled -- a single token names no city."""
    from job_radar.vocab import split_place

    assert split_place("Singapore") == {
        "city": None,
        "state": None,
        "country": "SG",
    }
    assert split_place("United States")["country"] == "US"
    assert split_place("Canada")["country"] == "CA"
    assert split_place("Hong Kong")["country"] == "HK"


def test_split_place_still_refuses_a_bare_two_letter_token():
    """The guard this must not weaken. "CA" alone is California as readily as Canada,
    so the lookup is gated on the country NAME map, never on country_code(), whose
    two-letter passthrough would resolve both it and the province "ON"."""
    from job_radar.vocab import split_place

    assert split_place("CA")["country"] is None
    assert split_place("ON")["country"] is None
    assert split_place("WA")["country"] is None


def test_split_place_still_refuses_a_bare_city_name():
    """A city is not a country and guessing one is the permanently-wrong row this
    function refuses to create -- "London" is also London, Ontario."""
    from job_radar.vocab import split_place

    for city in ("London", "Kuala Lumpur", "Paris", "Remote"):
        assert split_place(city) == {
            "city": None,
            "state": None,
            "country": None,
        }, city


def test_split_place_does_not_read_an_arrangement_as_a_city():
    """Every branch assigned `city` from the head without testing that the head was a
    place, so "Remote, France" came back as the city of Remote. The country and state
    the string really carries survive -- only the invented city is dropped."""
    from job_radar.vocab import split_place

    assert split_place("Remote, France") == {
        "city": None,
        "state": None,
        "country": "FR",
    }
    # the US-state branch keeps its state; only the city goes
    assert split_place("Remote, TX") == {"city": None, "state": "TX", "country": "US"}
    for s in ("Anywhere, Canada", "Hybrid, Germany", "WFH, India", "Onsite, Brazil"):
        assert split_place(s)["city"] is None, s
        assert split_place(s)["country"] is not None, s
    # case and a leading intensifier both normalize
    assert split_place("remote, italy") == {
        "city": None,
        "state": None,
        "country": "IT",
    }
    assert split_place("Fully Remote, France")["city"] is None


def test_split_place_drops_a_placeless_city_without_falling_through():
    """THE regression this must never hit. "CA" is in both _US_STATES and
    _KNOWN_COUNTRIES, so a nulled city that fell out of the three-part branch would
    reach the US-state test and read "Remote, ON, CA" as state CA / country US --
    the 25 foreign-rows-made-American bug that branch exists to prevent."""
    from job_radar.vocab import split_place

    assert split_place("Remote, ON, CA") == {
        "city": None,
        "state": None,
        "country": "CA",
    }
    assert split_place("Remote, NY, US") == {
        "city": None,
        "state": "NY",
        "country": "US",
    }


def test_split_place_still_resolves_real_cities():
    """The recovery cases must be byte-identical -- a placeless-head rule that also
    nulls real cities would cost far more than the bug it fixes."""
    from job_radar.vocab import split_place

    assert split_place("Austin, TX") == {
        "city": "Austin",
        "state": "TX",
        "country": "US",
    }
    assert split_place("Paris, France") == {
        "city": "Paris",
        "state": None,
        "country": "FR",
    }
    assert split_place("Toronto, ON, CA") == {
        "city": "Toronto",
        "state": None,
        "country": "CA",
    }
    # The documented residual: a DECORATED arrangement keeps its city. Narrow on
    # purpose -- a pattern loose enough to catch this also nulls "Hybrid - Austin".
    assert split_place("Remote - US, France")["city"] == "Remote - US"


def test_remote_scope_reads_the_boundary_not_the_arrangement():
    """WHERE a remote worker may sit is a different fact from whether the role is remote,
    and one boolean could not hold both -- which is how "Remote - Brazil" and "Remote"
    became the same value in a shortlist. Measured on 7,712 remote-by-location rows:
    3,127 name a country, 654 say anywhere, 338 a region/timezone, 609 nothing."""
    from job_radar.vocab import remote_scope

    assert remote_scope("Remote - Brazil") == "BR"
    assert remote_scope("Philippines (Remote)") == "PH"
    assert remote_scope("United States (Remote)") == "US"
    assert remote_scope("Canada - Remote (ON, AB, BC, or NS Only)") == "CA"
    assert remote_scope("Remote (North America)") == "NORTH AMERICA"
    assert remote_scope("Remote - EMEA") == "EMEA"
    # ISO 3166-2, never a bare state code: AR CA CO DE ID IL IN are each BOTH a US state
    # and an ISO country, so "Los Angeles, CA" and "Canada" would otherwise be the same
    # value in one column -- undecidable on 586 rows.
    assert remote_scope("Remote - TX") == "US-TX"
    assert remote_scope("Atlanta, GA - Remote") == "US-GA"
    assert remote_scope("Los Angeles, CA - Remote") == "US-CA"
    assert remote_scope("Remote - Canada") == "CA"


def test_remote_scope_does_not_read_south_america_as_the_us():
    """`america` was a member of the US marker pattern, so "South America" answered US --
    and because the non-US exclusion reads the same pattern as its veto, a posting naming
    Argentina, Chile and South America survived a US-only search. 44 rows carry one of
    these. They are now scoped as the regions they are."""
    from job_radar.vocab import remote_scope

    assert remote_scope("Remote - South America") == "SOUTH AMERICA"
    assert remote_scope("Remote - Latin America") == "LATIN AMERICA"
    # ...while the real thing still resolves, including the spelled-out long form.
    assert remote_scope("Remote - United States of America") == "US"
    assert remote_scope("Remote (North America)") == "NORTH AMERICA"
    # "us" is NOT a key in the country name map -- it is the English pronoun and far too
    # collision-prone for prose -- so these hundreds of rows needed their own pattern.
    for s in ("Remote - US", "Remote US", "US Remote", "US - Remote", "Remote, US"):
        assert remote_scope(s) == "US", s


def test_remote_scope_never_reads_unstated_as_anywhere():
    """Only 654 of 7,712 rows actually say anywhere. A bare "Remote" means "remote,
    boundary unstated" -- usually within whatever country the employer can pay from.
    Collapsing unstated into ANY is the blank-country-means-placeless bug again."""
    from job_radar.vocab import remote_scope

    assert remote_scope("Remote") is None
    assert remote_scope("") is None
    # A lone city stays unstated too: recognising it needs a gazetteer this package does
    # not carry, and "US" inferred from a city name is a plausible-looking default.
    assert remote_scope("New York (Remote)") is None
    # ...while the real thing is ANY, and distinguishable.
    assert remote_scope("Remote - Anywhere") == "ANY"
    assert remote_scope("Worldwide") == "ANY"


def test_country_code_prefers_a_known_alias_over_the_two_letter_passthrough():
    """ "UK" is not an ISO alpha-2 code -- GB is -- and the name map has said `uk -> GB`
    all along, but the unvalidated two-letter passthrough answered first. So a column the
    record contract declares alpha-2 held both spellings for one country, ~197 rows in a
    31,790-row harvest. Same defect as the state='California' vs 'CA' split."""
    from job_radar.vocab import country_code

    assert country_code("UK") == "GB"
    assert country_code("uk") == "GB"
    assert country_code("United Kingdom") == "GB"
    # Bulgaria was missing from the map entirely, so this returned None while the old
    # hand-written non-US filter did list it.
    assert country_code("Bulgaria") == "BG"
    # The passthrough still applies to codes the map does not carry -- that is deliberate
    # (this module is not the authority on the ISO list), and only ALIASES now win.
    assert country_code("ZZ") == "ZZ"
    assert country_code("NZ") == "NZ"


def test_no_country_name_is_also_a_us_state_name():
    """The invariant split_place's bare-name lookup depends on, pinned here because
    nothing else enforces it. _COUNTRY_CODES is hand-curated and currently missing
    Georgia, Jordan and Chad; adding "georgia": "GE" for an unrelated source would
    otherwise make the bare US state a foreign country. split_place excludes
    _STATE_NAMES for that reason, and this fails the moment the two maps collide so
    the collision is a test failure rather than a silent wrong row."""
    from job_radar.vocab import _COUNTRY_CODES, _STATE_NAMES

    assert not set(_COUNTRY_CODES) & set(_STATE_NAMES)


def test_ashby_salary_never_reads_the_equity_component():
    """Measured on openai (n=734): components are Salary 594, EquityCashValue 576,
    Commission 15. Taking the first would write an equity grant into salary_min."""
    comp = {
        "summaryComponents": [
            {
                "compensationType": "EquityCashValue",
                "interval": "1 YEAR",
                "currencyCode": "USD",
                "minValue": None,
                "maxValue": None,
            },
            {
                "compensationType": "Salary",
                "interval": "1 YEAR",
                "currencyCode": "USD",
                "minValue": 257000,
                "maxValue": 335000,
            },
        ]
    }
    got = sources._ashby_salary(comp)
    assert got["salary_min"] == 257000 and got["salary_max"] == 335000
    # '1 YEAR' -- the leading count is part of the vendor string, so a period map
    # keyed on 'year' alone would drop every Ashby salary.
    assert got["salary_period"] == "year" and got["salary_currency"] == "USD"
    assert sources._ashby_salary({})["salary_min"] is None


def test_bodies_are_assembled_from_every_field_the_vendor_splits_them_across():
    """Lever and USAJOBS both put a tenth of the posting in the obvious field and the
    rest in siblings, so reading the obvious one fed the scorer a fragment."""
    lever = sources._lever_text(
        {
            "descriptionPlain": "Intro.",
            "lists": [{"text": "Requirements", "content": "<li>Python</li>"}],
            "additionalPlain": "Why us.",
        }
    )
    assert "Intro." in lever and "Requirements" in lever and "Python" in lever
    assert "Why us." in lever

    federal = sources._usajobs_text(
        {
            "UserArea": {
                "Details": {
                    "JobSummary": "Summary.",
                    "MajorDuties": ["Duty one.", "Duty two."],  # arrives as a LIST
                    "Evaluations": "Rated on X.",
                }
            }
        }
    )
    assert "Summary." in federal and "Duty two." in federal and "Rated on X." in federal


def test_greenhouse_metadata_goes_to_source_extra_not_a_core_column():
    """The names are chosen per board -- databricks sends 'Company Assignment',
    anthropic 'Location Type', stripe none -- so mapping one to parent_company works
    on exactly one board. That is the mistake `department` already made."""
    got = sources._gh_metadata(
        [
            {"name": "Company Assignment", "value": "Databricks Japan K.K."},
            {"name": "Career Page Posting Category", "value": ["Field Engineering"]},
            {"name": "Empty", "value": None},
            "not a dict",
        ]
    )
    assert got == {
        "Company Assignment": "Databricks Japan K.K.",
        # a multi-select stays a list; taking the first would silently drop the rest
        "Career Page Posting Category": ["Field Engineering"],
    }


# ── workday location + requisition id (probed live 2026-08-05) ───────────────
def test_workday_recovers_the_location_its_list_endpoint_never_sends():
    """`locationsText` IS NOT IN the Workday list response — `location` was empty on
    120 of 120 accenture rows. That emptied two of `dedup_key`'s four components at
    once (the location, and the job id nothing parsed), so every same-titled role a
    company posts worldwide collapsed into ONE row: 89 of 400 discarded, each with
    its own apply URL and city."""
    # bulletFields carries it on some tenants, alongside a req id and sometimes a
    # posting date — read BY SHAPE, never by position.
    assert (
        sources._workday_place(
            {"bulletFields": ["R00333425", "Buenos Aires"]}, "/job/Buenos-Aires/X_R1"
        )
        == "Buenos Aires"
    )
    # Other tenants send only the id, so the path is the fallback — and it is the one
    # source present on every tenant probed.
    assert (
        sources._workday_place(
            {"bulletFields": ["R327553"]},
            "/job/US-Minnesota-Maplewood/Automated-Inspection-Engineer_R01169027",
        )
        == "US Minnesota Maplewood"
    )
    # A posting date in bulletFields must never be mistaken for a location.
    assert (
        sources._workday_place(
            {"bulletFields": ["R123", "Posting Date: 06/26/2026"]}, "/job/Reno/X_R123"
        )
        == "Reno"
    )
    # The documented field still wins when a tenant actually sends it.
    assert (
        sources._workday_place({"locationsText": "Springfield, IL"}, "/job/Other/X_R1")
        == "Springfield, IL"
    )
    assert sources._workday_place({}, "") == ""


def test_workday_path_decoding_survives_a_literal_hyphen():
    """Workday encodes each space as `-`, so a real hyphen between spaces arrives as
    `---`. Decoding shortest-first turns "Sao Paulo - Barueri" into three spaces."""
    assert sources._wd_unslug("Buenos-Aires") == "Buenos Aires"
    assert sources._wd_unslug("Sao-Paulo---Barueri") == "Sao Paulo - Barueri"
    assert sources._wd_unslug("") == ""


def test_workday_requisition_id_reaches_dedup_key_without_breaking_discovery():
    """The id is parsed by its OWN pattern rather than by teaching `ats_from_url`
    about Workday, and that is load-bearing: `ats_from_url` returns `(ats, slug)`,
    but a Workday board needs slug + host + site. `funnel._probe` would call
    `live_workday(slug)`, whose defaults do not raise — it would build a wrong URL,
    404, and silently discard every real Workday employer discovery found."""
    from job_radar import dedup

    url = (
        "https://accenture.wd103.myworkdayjobs.com/en-US/AccentureCareers"
        "/job/Buenos-Aires/Analytics-and-Modeling-Specialist_R00333425"
    )
    assert dedup.job_ref(url) == ("workday", "", "R00333425")
    # Workday appends a copy suffix when a requisition is reposted. Anchoring the id
    # to end-of-path missed every one, and they cluster: all 14 residual wrong merges
    # on accenture after the first fix were `-1` rows.
    assert dedup.job_ref(url + "-1")[2] == "R00333425-1"
    # Still NOT a known ATS for routing purposes — this is the invariant that keeps
    # discovery working.
    assert dedup.ats_from_url(url) is None


def test_two_workday_openings_with_one_title_stay_two_rows():
    """The end of the actual bug: same title, same company, different city and
    requisition id."""
    from job_radar import dedup

    def row(city, req):
        return {
            "company": "Accenture",
            "title": "Contract Manager",
            "location": city,
            "url": (
                "https://accenture.wd103.myworkdayjobs.com/en-US/AccentureCareers"
                f"/job/{city.replace(' ', '-')}/Contract-Manager_{req}"
            ),
        }

    a, b = row("Buenos Aires", "R00333425"), row("Yokohama", "R00250899")
    assert dedup.dedup_key(a) != dedup.dedup_key(b)
    assert dedup.different_openings(a, b), "two requisition ids are two openings"


# ── the SerpApi quota guard (Strand G) ──────────────────────────────────────
def test_google_jobs_never_spends_past_the_plan_reserve(monkeypatch):
    """The live risk this exists for: pages x title_queries searches per run, six
    queries shipped, one page = 6/run = 180 of a 250/month free tier at daily
    cadence. One more query or page overruns mid-month — and SerpApi reports
    exhaustion as a JSON `error`, not an HTTP failure, so the adapter degraded into a
    printed notice while the shortlist just got quieter."""
    c = _cfg()
    monkeypatch.setattr(c, "env", lambda key: "test-key")
    monkeypatch.setattr(c, "serpapi_reserve", 25)
    monkeypatch.setattr(c, "serpapi_max_searches_per_run", 100)
    calls = []

    def fake(url, *a, **k):
        calls.append(url)
        if "account.json" in url:
            return {"plan_searches_left": 27}  # 27 - 25 reserved = 2 spendable
        return {"jobs_results": []}

    monkeypatch.setattr(sources, "get_json", fake)
    monkeypatch.setattr(sources.time, "sleep", lambda s: None)
    sources.search_google_jobs(["a", "b", "c", "d", "e", "f"])
    assert len(_searches(calls)) == 2, (
        f"spent {len(_searches(calls))} searches with only 2 above the reserve"
    )


def test_google_jobs_skips_entirely_when_the_plan_is_exhausted(monkeypatch):
    c = _cfg()
    monkeypatch.setattr(c, "env", lambda key: "test-key")
    monkeypatch.setattr(c, "serpapi_reserve", 25)
    calls = []

    def fake(url, *a, **k):
        calls.append(url)
        if "account.json" in url:
            return {"plan_searches_left": 10}  # below the reserve entirely
        return {"jobs_results": []}

    monkeypatch.setattr(sources, "get_json", fake)
    assert sources.search_google_jobs(["a", "b"]) == []
    assert _searches(calls) == [], "spent a metered search with no quota to spend"


def test_a_failed_quota_check_falls_back_to_the_run_cap_not_to_zero(monkeypatch):
    """None, not 0, when /account is unreachable: a network blip says nothing about
    the quota, and treating it as empty would disable the adapter. The per-run cap
    still bounds the damage."""
    c = _cfg()
    monkeypatch.setattr(c, "env", lambda key: "test-key")
    monkeypatch.setattr(c, "serpapi_max_searches_per_run", 3)
    calls = []

    def fake(url, *a, **k):
        if "account.json" in url:
            raise OSError("account endpoint down")
        calls.append(url)
        return {"jobs_results": []}

    monkeypatch.setattr(sources, "get_json", fake)
    monkeypatch.setattr(sources.time, "sleep", lambda s: None)
    sources.search_google_jobs(["a", "b", "c", "d", "e"])
    assert len(_searches(calls)) == 3, "the per-run cap did not bound an unknown quota"


def test_the_quota_probe_itself_is_free(monkeypatch):
    """/account.json does not consume a search — verified live 2026-08-05, usage
    stayed put across calls. That is what makes checking before every run affordable;
    if it were metered the guard would cost the thing it protects."""
    c = _cfg()
    monkeypatch.setattr(c, "env", lambda key: "test-key")
    seen = []
    monkeypatch.setattr(
        sources,
        "get_json",
        lambda url, *a, **k: (
            seen.append(url),
            {"plan_searches_left": 250} if "account" in url else {"jobs_results": []},
        )[1],
    )
    monkeypatch.setattr(sources.time, "sleep", lambda s: None)
    sources.search_google_jobs(["a"])
    account_calls = [u for u in seen if "account.json" in u]
    assert len(account_calls) == 1, "the quota is probed once per run, not per query"


# ── the basis vocabularies are closed (panel review C1, 2026-08-05) ──────────
def test_no_adapter_emits_a_basis_outside_its_closed_vocabulary():
    """A basis outside its set is a contract break: the whole point of the field is
    that a consumer can branch on it, which requires knowing every value it can take.

    Enforced by reading the SOURCE, so an adapter that hard-codes a typo'd basis fails
    here rather than in whatever consumer eventually branches on it."""
    import inspect
    import re as _re

    from job_radar import vocab as _vocab

    src = inspect.getsource(sources)
    for field, legal in (
        ("remote_basis", _vocab.REMOTE_BASES),
        ("seniority_basis", _vocab.SENIORITY_BASES),
        ("posted_basis", _vocab.POSTED_BASES),
        ("salary_basis", _vocab.SALARY_BASES),
    ):
        used = set(_re.findall(rf'"{field}":\s*"([a-z_]+)"', src))
        used |= set(_re.findall(rf'"{field}":\s*"([a-z_]+)"\s+if', src))
        illegal = used - legal
        assert not illegal, f"{field} emits {sorted(illegal)}, not in {sorted(legal)}"


def test_a_remote_only_board_reports_board_scope_not_a_vendor_field():
    """C1, the panel's blocker. Six adapters labelled `stated` for a fact no ROW ever
    asserted — every posting on remotive/jobicy/remoteok/himalayas/braintrust is
    remote because that is what the board IS. The values were right; the provenance
    was not, and collapsing "the vendor's field said so" into "the board is remote-
    only" destroys exactly the distinction the basis field exists to preserve.

    A consumer tightening a remote filter must be able to discount a board-scope
    inference without discarding a vendor's explicit flag."""
    for name, sample_key in (
        ("remotive", "remotive"),
        ("jobicy", "jobicy"),
        ("remoteok", "remoteok"),
        ("braintrust", "braintrust"),
    ):
        if sample_key not in SAMPLES:
            continue
        import unittest.mock as m

        c = _cfg()
        with (
            m.patch.object(sources, "get_json", lambda *a, **k: SAMPLES[sample_key]),
            m.patch.object(sources.time, "sleep", lambda s: None),
            m.patch.object(c, "env", lambda k: "test-key"),
        ):
            rows = sources.BREADTH_ALL[name](["engineer"])
        for r in rows:
            assert r.get("remote_basis") == "board", (
                f"{name}: a remote-only board is not a per-row vendor statement"
            )


def test_usajobs_reads_the_rows_remote_field_not_our_query_parameter():
    """It emitted `stated` from the string WE appended to the request URL, so every
    row claimed a vendor statement purely because we had asked for remote. The real
    per-row fields were there the whole time (probed n=25: RemoteIndicator on 25/25,
    TeleworkEligible True on 13)."""
    remote = sources._usajobs_remote(
        {"UserArea": {"Details": {"RemoteIndicator": True, "TeleworkEligible": True}}}
    )
    assert remote == {
        "remote_type": "remote",
        "remote_basis": "stated",
        "remote_region": "US",
    }
    # Telework-eligible but not remote is partly-in-office, which is what hybrid means.
    hybrid = sources._usajobs_remote(
        {"UserArea": {"Details": {"RemoteIndicator": False, "TeleworkEligible": True}}}
    )
    assert hybrid["remote_type"] == "hybrid" and hybrid["remote_region"] is None
    onsite = sources._usajobs_remote(
        {"UserArea": {"Details": {"RemoteIndicator": False, "TeleworkEligible": False}}}
    )
    assert onsite["remote_type"] == "onsite"
    # Neither field present -> unknown, never a plausible default.
    assert sources._usajobs_remote({})["remote_type"] is None
    assert sources._usajobs_remote({})["remote_basis"] is None


def test_usajobs_treats_every_non_place_the_way_adzuna_does(monkeypatch):
    """The remote-vs-place predicate drifted apart again (panel P2). This adapter
    compared against the literal string "remote", so "anywhere", "any" and "" built
    `&LocationName=%20remote%20` with no RemoteIndicator — the remote filter never
    reached the API and an empty LocationName went out. Every keyed search API
    distinguishes a PLACE from a WORK ARRANGEMENT, and every one fails silently when
    you confuse them."""
    for arrangement in ("remote", "Remote", " remote ", "anywhere", "any", ""):
        c = _cfg()
        monkeypatch.setattr(c, "location", arrangement)
        monkeypatch.setattr(c, "env", lambda key: "test-key")
        calls = _usajobs_response(
            monkeypatch, {"SearchResult": {"SearchResultItems": []}}
        )
        sources.search_usajobs(["engineer"])
        url = calls[0] if isinstance(calls, list) and calls else ""
        assert "RemoteIndicator=True" in url, f"{arrangement!r} lost the remote filter"
        assert "LocationName=" not in url, f"{arrangement!r} sent a place too: {url}"

    c = _cfg()
    monkeypatch.setattr(c, "location", "Louisville, KY")
    monkeypatch.setattr(c, "env", lambda key: "test-key")
    calls = _usajobs_response(monkeypatch, {"SearchResult": {"SearchResultItems": []}})
    sources.search_usajobs(["engineer"])
    url = calls[0]
    assert "LocationName=Louisville" in url and "RemoteIndicator" not in url
