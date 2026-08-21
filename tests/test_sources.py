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
                # `type` IS NOT AN EMPLOYMENT TYPE, and this fixture used to say
                # "Full Time", which is a value the API has never sent. That is why
                # the adapter read it for so long: the fixture made a provenance flag
                # look like a legitimate type, so the parser test agreed with the bug.
                # Probed live 2026-08-20 -- `type` is the literal string "external" on
                # 20 of 20 rows, with `model_type` beside it. A fixture is a CAPTURE,
                # not an assumption; catalog/themuse.md records employment_type: null.
                "type": "external",
                "model_type": "external",
                "company": {"name": "Acme"},
                "locations": [{"name": "Austin, TX"}],
                "categories": [{"name": "Data Science"}],
                # Both forms ship in the same object -- the canonical token and the
                # display string. The adapter reads short_name; name becomes the raw.
                "levels": [{"name": "Senior Level", "short_name": "senior"}],
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
                # Braintrust mixes opaque numeric ids into both skill arrays -- 164 of
                # its 227 tag tokens corpus-wide. Captured here because the fixture
                # carried none, so nothing could catch them reaching `tags` or `text`.
                "main_skills": [{"name": "Python"}, {"name": "122059"}],
                "job_skills": [{"name": "AI/ML"}, {"name": "122060"}],
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


@pytest.mark.parametrize(
    "name", sorted(set(sources.DEPTH_ALL) | set(sources.BREADTH_ALL))
)
def test_every_adapter_emits_sections_or_honestly_omits_them(name, monkeypatch):
    """0.9.0 wired `sections` at eighteen body sites across FOUR different call shapes
    -- a plain assignment, two helpers that return the value, a mutator writing into a
    caller's dict, and one where the local is a parsing intermediate. A site missed in
    any of them fails SILENTLY: that source simply carries no structure, forever, and
    nothing else in the suite would notice.

    `list | None`, not `list`. Three adapters legitimately produce None because they
    send no body at all -- smartrecruiters' list endpoint, and the workday/rippling list
    endpoints before their detail calls run. Braintrust is a fourth and a different
    reason: it SYNTHESIZES its text from the title and skill tags, so there is no vendor
    markup to read and never will be.
    """
    assert name in SAMPLES, f"no SAMPLE payload for adapter {name!r} — add one"
    c = _cfg()
    monkeypatch.setattr(c, "env", lambda key: "test-key")
    monkeypatch.setattr(sources, "get_json", lambda url, *a, **k: SAMPLES[name])
    monkeypatch.setattr(sources, "post_json", lambda url, body, *a, **k: SAMPLES[name])
    monkeypatch.setattr(sources.time, "sleep", lambda s: None)
    _usajobs_response(monkeypatch, SAMPLES["usajobs"])

    if name in sources.DEPTH_ALL:
        out = sources.DEPTH_ALL[name]("slug")
    else:
        out = sources.BREADTH_ALL[name](["AI Engineer"])
    assert out, f"{name}: produced no row from its own sample payload"

    bodyless = {"smartrecruiters", "workday", "rippling", "braintrust"}
    for row in out:
        secs = row.get("sections")
        assert secs is None or isinstance(secs, list), (
            f"{name}: sections is {type(secs).__name__}, not list or None"
        )
        if name not in bodyless and row.get("text"):
            assert secs is not None, (
                f"{name}: has a body but sections is None — a wiring site was missed"
            )
        for sec in secs or []:
            assert set(sec) <= {"type", "header", "start", "end"}, sec
            assert ("start" in sec) == ("end" in sec), "half a span is not a span"


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
    # Sections arrive through the DETAIL call, which is why they are asserted here and
    # not in the all-adapter contract test: that test drives the LIST endpoint, whose
    # rows carry no body at all, so it exempts rippling and workday by name and could
    # never see a missed wiring site in either. Un-wiring this branch used to leave the
    # whole suite green.
    # `[]`, not None -- and the difference IS the assertion. This fixture's description
    # is plain text, so "we looked and found no headers" is the right answer; a row that
    # never reached `clean_with_sections` at all would arrive as None from `_coerce`.
    # The two-state contract is what makes a missed wiring site visible here.
    assert j["sections"] == []


def test_rippling_string_shaped_description_also_carries_sections(monkeypatch):
    """`description` arrives as EITHER a dict of two HTML blocks or a bare string, and
    the two are separate assignments in `_rippling_detail`. An earlier draft of 0.9.0
    wired only the dict-shaped one, which would have left every string-shaped response
    with a body and no structure -- silently, for that response shape alone."""
    listing = [{"uuid": "u1", "name": "AI Engineer", "url": "u", "workLocation": {}}]
    detail = {
        # `<p><strong>…</strong></p>` is the real Greenhouse/Rippling heading shape --
        # the line break comes from `</p>`, since `</strong>` is not a block closer.
        "description": (
            "<p><strong>What you'll do</strong></p><p>Build RAG systems.</p>"
        ),
        "createdOn": "2023-10-31T10:40:35.194000-07:00",
    }
    monkeypatch.setattr(
        sources, "get_json", lambda url: detail if "/jobs/u1" in url else listing
    )
    j = sources.fetch_rippling("acme")[0]
    assert j["text"] == "What you'll do\nBuild RAG systems."
    kinds = [s["type"] for s in j["sections"]]
    assert "responsibilities" in kinds


def test_workday_detail_carries_sections_not_just_a_body(monkeypatch):
    """Same hole as rippling: a Workday body exists only after the per-job detail call,
    so the list-driven contract test exempts it and cannot catch an un-wired site."""
    _cfg()
    # `_wd_path` is what drives the detail call; a row without it is skipped entirely.
    rows = [
        {
            "title": "AI Engineer",
            "url": "https://x/job/AI_Engineer_R-1",
            "text": "",
            "salary": "",
            "_wd_path": "/job/AI_Engineer_R-1",
        }
    ]
    monkeypatch.setattr(
        sources,
        "get_json",
        lambda url, *a, **k: {
            "jobPostingInfo": {
                "jobDescription": (
                    "<p><strong>Responsibilities</strong></p><p>Build systems.</p>"
                ),
                "startDate": "2026-08-01",
            }
        },
    )
    sources._workday_add_details("https://x", rows)
    assert rows[0]["text"] == "Responsibilities\nBuild systems."
    assert [s["type"] for s in rows[0]["sections"]] == ["responsibilities"]


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


def test_themuse_reads_the_canonical_level_token_not_the_display_string(monkeypatch):
    """The Muse ships {'name': 'Mid Level', 'short_name': 'mid'} in one object and the
    adapter kept only `name`, throwing the vendor's own canonical token away -- the
    same class of defect as reading a plain-text field when the structured one is
    beside it. `short_name` needs no interpretation from us and is already the form a
    consumer can filter on, so it is the value; `name` is what the vendor displays, so
    it is the raw. Worth 87 rows on a `seniority='senior'` filter."""
    monkeypatch.setattr(config.active().harvest_depth, "themuse_max_pages", 1)
    monkeypatch.setattr(sources.time, "sleep", lambda s: None)
    monkeypatch.setattr(sources, "get_json", lambda url: SAMPLES["themuse"])
    out = sources.search_themuse([])
    assert out[0]["seniority"] == "senior"
    assert out[0]["seniority_raw"] == "Senior Level"


def test_themuse_states_no_employment_type_because_the_api_has_none(monkeypatch):
    """It read `type`, which is The Muse's posting-PROVENANCE flag -- the literal
    string "external" on 20/20 rows probed live -- so 216 of 216 rows normalized to
    OTHER, the single largest contributor to that bucket. catalog/themuse.md has
    recorded `employment_type: null` for this API since it was written; the code was
    reading a field the profile says does not exist. The key stays present (every
    adapter owes its caller that) and empty, which the boundary turns into None."""
    from job_radar import engine

    monkeypatch.setattr(config.active().harvest_depth, "themuse_max_pages", 1)
    monkeypatch.setattr(sources.time, "sleep", lambda s: None)
    monkeypatch.setattr(sources, "get_json", lambda url: SAMPLES["themuse"])
    out = sources.search_themuse([])
    assert "employment_type" in out[0], "the contract requires the key"
    r = engine._coerce(out[0])
    assert r["employment_type"] is None
    assert r["employment_type_raw"] is None


def test_braintrust_keeps_its_opaque_ids_out_of_both_tags_and_the_body(monkeypatch):
    """A five-digit integer is not a skill, and the contract calls `tags` "skills the
    source itself extracted". The ids reached TWO fields: `tags`, and `text`, where
    they were interpolated under the label "Skills:" and then read by relevant() and
    score_and_signals() -- 42 live rows carried them in the scored, searchable body.
    Filtering `tags` alone would have fixed half the defect while reporting it done."""
    from job_radar import engine

    monkeypatch.setattr(sources.time, "sleep", lambda s: None)
    monkeypatch.setattr(sources, "get_json", lambda url, *a, **k: SAMPLES["braintrust"])
    r = engine._coerce(sources.search_braintrust(["ai"])[0])
    assert r["tags"] == ["Python", "AI/ML"]
    for opaque in ("122059", "122060"):
        assert opaque not in r["text"], f"{opaque} is still in the scored body"


def test_braintrust_contract_type_survives_its_own_qualifier(monkeypatch):
    """The adapter builds `f"contract ({contract_type})"`, so the normalizer saw
    "contract long" and returned OTHER on 29 of 29 rows while bare "contract" maps to
    CONTRACTOR. The raw keeps the qualifier, because that is what was sent."""
    from job_radar import engine

    monkeypatch.setattr(sources.time, "sleep", lambda s: None)
    monkeypatch.setattr(sources, "get_json", lambda url, *a, **k: SAMPLES["braintrust"])
    r = engine._coerce(sources.search_braintrust(["ai"])[0])
    assert r["employment_type"] == "CONTRACTOR"
    assert r["employment_type_raw"] == "contract (hourly)"


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
    assert r["remote_type"] != "onsite"  # "did not say" is not "said on-site"


def test_coerce_does_not_stringify_the_typed_fields():
    """The legacy text fields are forced to str; the contract fields must NOT be, or
    None becomes the string "None" and a list becomes its repr."""
    from job_radar import engine

    r = engine._coerce({"title": None, "remote_type": "remote", "tags": ["a", "b"]})
    assert r["title"] == ""  # required field: coerced to str
    assert r["remote_type"] == "remote"  # typed field: untouched
    assert r["tags"] == ["a", "b"]
    assert engine._coerce({"tags": "solo"})["tags"] == ["solo"]  # scalar -> list


def test_adzuna_area_depth_maps_state_and_remote():
    """`area` is a hierarchy of varying depth. `area == ['US']` EXACTLY means
    nationwide/remote; depth 5 shifts city one slot but never the state.

    THIS DOCSTRING AND ITS OWN ASSERTION USED TO DISAGREE. It said "depth 5 shifts city
    one slot" and then asserted `city5 == "Prince"` -- the UNSHIFTED last element, a
    neighbourhood. `_adzuna_place` matched the assertion rather than the sentence, so
    the test was green and the described behaviour had never been implemented. The
    docstring was right; the assertion was the defect, and it is corrected here.
    """
    assert sources._adzuna_place(["US"]) == (None, None, "US", True)
    city, state, country, remote = sources._adzuna_place(
        ["US", "Texas", "Howard County", "Big Spring"]
    )
    assert (city, state, country, remote) == ("Big Spring", "Texas", "US", None)
    city5, state5, _, _ = sources._adzuna_place(
        ["US", "New York", "New York City", "Manhattan", "Prince"]
    )
    assert state5 == "New York", "state must stay at area[1] at depth 5"
    assert city5 == "Manhattan", "depth 5: the city tier is area[3], not the last slot"
    # DEPTH 3 -- the city is the last slot, and the old `>= 4` test discarded it
    # outright: 'San Francisco, California' (15 rows) and 'New York City, New York' (8)
    # came back with no city at all while the vendor had supplied a clean one.
    assert sources._adzuna_place(["US", "California", "San Francisco"])[0] == (
        "San Francisco"
    )
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


def test_a_truncated_body_says_so_and_a_built_one_says_so(monkeypatch):
    """Two bodies that are not ordinary bodies, each labelled by the adapter that knows.

    Adzuna caps every description at 500 characters and ends it with an ellipsis -- 275
    of 275 rows locally, 7,146 of 7,150 [live prod, 2026-08-20], and a live probe
    confirms no fuller field exists. Braintrust sends no prose at all and its adapter
    BUILDS a sentence from the title and skills. Without a label both are
    indistinguishable in the record from a real 6,870-character description.

    There is no `full`: seventeen adapters would have to claim a completeness nobody
    measured, so `None` -- not characterized -- is the honest value for them.
    """
    payload = {
        "results": [
            {
                "title": "AI Engineer",
                "company": {"display_name": "Acme"},
                "location": {"display_name": "Louisville, KY", "area": ["US", "KY"]},
                "redirect_url": "https://www.adzuna.com/details/1",
                "created": "2026-08-01T00:00:00Z",
                "description": "Responsibilities: Develop and v" + "x" * 460 + "\u2026",
            }
        ]
    }
    c = _cfg()
    monkeypatch.setattr(c, "env", lambda key: "k")
    monkeypatch.setattr(sources, "get_json", lambda url: payload)
    monkeypatch.setattr(sources.time, "sleep", lambda *_a, **_k: None)
    rows = sources.search_adzuna(["ai engineer"])
    assert rows, "adzuna produced no rows"
    assert rows[0]["text_basis"] == "excerpt"


def test_a_synthesized_braintrust_body_reports_no_headers_not_no_body(monkeypatch):
    """`sections: null` means "there was no body to read" and `[]` means "a body with no
    headers". This adapter emitted `null` while shipping a body on 29 of 29 rows, which
    is a false statement about the posting -- and it collapses the exact two-state
    distinction the field exists to carry."""
    payload = {
        "results": [
            {
                "id": 1,
                "title": "AI Platform Engineer",
                "employer": {"name": "Acme"},
                "created": "2026-08-01T00:00:00Z",
                "contract_type": "long",
                "expected_hours_per_week": 40,
            }
        ]
    }
    monkeypatch.setattr(sources, "get_json", lambda url: payload)
    monkeypatch.setattr(sources.time, "sleep", lambda *_a, **_k: None)
    rows = sources.search_braintrust(["ai engineer"])
    assert rows, "no braintrust rows"
    row = rows[0]
    assert row["text"], "the adapter builds a body, so there is one"
    assert row["sections"] == [], (
        f"a body with no headers is [], not {row['sections']!r}"
    )
    assert row["text_basis"] == "synthesized"


def test_lever_reads_the_html_body_and_promotes_its_own_list_headings(monkeypatch):
    """The Ashby defect, one adapter over, and missed when Ashby's was fixed: this read
    `descriptionPlain`/`additionalPlain`, so `sections` was `[]` on 122 of 135 Lever
    rows. Lever also labels each `lists[]` entry with its own heading, which the adapter
    appended as bare prose -- throwing away structure the vendor handed us, then failing
    to find it again.

    Asserted POSITIONALLY, on headings the plain fields do not contain, and against the
    NEIGHBOURING section's prose: a wrong-span bug survives every string-identity check
    while pointing at the wrong block, and one shipped here before.
    """
    payload = [
        {
            "text": "Applied AI Engineer",
            "hostedUrl": "https://jobs.lever.co/acme/1",
            "categories": {"location": "Remote"},
            # HTML and plain are BOTH sent; only the first carries structure.
            "description": "<div><strong>A World-Changing Company</strong></div>",
            "descriptionPlain": "A World-Changing Company",
            "lists": [
                {
                    "text": "What We Require",
                    "content": "<li>Three years of Python.</li>",
                },
                {
                    "text": "What We Value",
                    "content": "<li>Ability to adjust quickly.</li>",
                },
            ],
            "additional": "<div><strong>Benefits</strong> Health cover.</div>",
            "additionalPlain": "Benefits Health cover.",
        }
    ]
    monkeypatch.setattr(sources, "get_json", lambda url: payload)
    (row,) = sources.fetch_lever("acme")

    headers = [s["header"] for s in row["sections"] if s.get("header")]
    assert "What We Require" in headers and "What We Value" in headers, (
        f"the vendor's own list headings did not become sections. got {headers!r}"
    )
    # PINS THE FIELD CHOICE, not just the list wrapping. This heading is bold in
    # `description` and flat prose in `descriptionPlain`, so it is the only assertion
    # here that fails when the adapter reads the plain field again -- the list headings
    # survive either way, which made an earlier version of this test pass against the
    # very mutation it exists to catch.
    assert "A World-Changing Company" in headers, (
        f"read the plain intro -- a markup-only heading is missing. got {headers!r}"
    )
    assert "Benefits" in headers, "read `additionalPlain` -- its heading is missing"
    sec = next(s for s in row["sections"] if s.get("header") == "What We Require")
    span = row["text"][sec["start"] : sec["end"]]
    assert "Three years of Python." in span
    assert "Ability to adjust quickly." not in span, "span points at the next section"
    assert "<li>" not in row["text"] and "<strong>" not in row["text"]


def test_lever_falls_back_to_plain_when_the_vendor_sends_no_html(monkeypatch):
    """Reading the HTML field ALONE would silently empty the body for an employer who
    fills only the plain one -- `""` is a legal value, so nothing raises and the row
    ships with no description at all. Same safety net as Ashby's."""
    payload = [
        {
            "text": "Applied AI Engineer",
            "hostedUrl": "https://jobs.lever.co/acme/1",
            "categories": {"location": "Remote"},
            "descriptionPlain": "We build things.",
            "additionalPlain": "Health cover.",
        }
    ]
    monkeypatch.setattr(sources, "get_json", lambda url: payload)
    (row,) = sources.fetch_lever("acme")
    assert "We build things." in row["text"] and "Health cover." in row["text"]


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


def test_hn_does_not_ship_the_whole_comment_as_the_location():
    """`" ".join(parts[2:4])` put the ENTIRE BODY in the location when a comment ran out of
    pipes. hn `location` averaged 461 characters and reached 2,158, against greenhouse's
    maximum of 331 and ashby's 34 -- 82 of 196 rows over 100. Every prose rule in
    `vocab.remote_scope` is written for a short string, so it produced wrong values, not
    merely noisy ones.

    The strings below are VERBATIM from a 7,545-row harvest, elided only in the body."""
    from job_radar.sources import _hn_location
    from job_radar.vocab import remote_scope

    # The pronoun "us", and a URL slug ending in the same two letters. `US_LOCATION_RE`
    # carries a bare `us` on purpose -- "Remote - US" is 227 rows -- but against 2 KB of
    # prose it matches English. 12 rows, 3 of them inside a link.
    contaminated = (
        "REMOTE (EU, Switzerland, Norway) Full-time or Contract Multiple roles open. "
        "We are a small boutique consultancy and are quite experienced. A lot of us have "
        "extensive consultancy backgrounds. Apply: https://grnh.se/bhfswi9e5us"
    )
    assert remote_scope(contaminated)[0] == ["CH", "NO", "US"], "the defect"
    assert remote_scope(_hn_location(contaminated))[0] == ["CH", "NO"], (
        "US was a pronoun"
    )

    canada = (
        "Toronto, Canada REMOTE (Canada only) Makeship empowers influencers, creators, "
        "and brands of all sizes to bring their ideas to life. If you apply, please note "
        "that you found us through Hacker News."
    )
    assert remote_scope(canada)[0] == ["CA", "US"], "the defect"
    assert remote_scope(_hn_location(canada))[0] == ["CA"], (
        "Canada only means Canada only"
    )

    # TRUNCATED, NOT DROPPED. Filtering over-long segments emptied the location on 9 rows
    # whose header and body share one segment, discarding a real boundary to remove the
    # noise stuck to it.
    shared = (
        "REMOTE (US) Origamics is building AI models that reason about hardware and "
        "electronics systems."
    )
    assert remote_scope(_hn_location(shared))[0] == ["US"], (
        "a stated bound must survive"
    )

    # A mid-token cut INVENTS a place, which is why the cap is not tuned tighter: at 48
    # this truncates inside "(NYC, NC, MA)", `split_place` reads the fragment as a city
    # with state NC, `has_city` flips and the correct ['US'] is suppressed. THIS ASSERTION
    # PINS THE CAP, not the word-boundary rewind -- see the note below.
    listy = (
        "Python / SQL / Node.js / Terraform Remote (USA, most states) or Onsite "
        "(NYC, NC, MA) We're building the data platform."
    )
    assert remote_scope(_hn_location(listy))[0] == ["US"]

    # THE WORD-BOUNDARY REWIND IS NOT EXERCISED BY ANY CORPUS ROW AT CAP 64, and saying so
    # is the point. Every one of the 196 hn segments was truncated both ways and ZERO
    # produced a different `remote_scope`. It is kept because the failure mode is real and
    # demonstrated at a smaller cap, not because anything here proves it -- so a mutation
    # that removes it will NOT fail this test, and a reader must not take these greens as
    # coverage of it. Only the shape below pins the mechanism at all, and it is
    # constructed, not observed.
    # One long token straddling the cap: with the rewind the whole partial token is
    # dropped, without it a 57-character fragment of it survives into the location.
    made_up = "Remote " + "x" * 80
    assert _hn_location(made_up) == "Remote", (
        "a partial token must not reach the location"
    )

    # 64 is the shortest cap that keeps a measured multi-place header intact.
    wide = (
        "London / NYC / SF / Seattle / Remote (US + Europe) Full-time We're hiring a "
        "Trust and Safety engineer."
    )
    assert remote_scope(_hn_location(wide)) == (["US"], ["EUROPE"])

    # A short segment is returned untouched -- this only ever removes.
    assert _hn_location("Remote (Italy)") == "Remote (Italy)"


def test_hn_header_ends_at_a_block_tag_not_a_newline():
    """The cap above is a MITIGATION; this is the cure, and they compose.

    An HN comment ends its header at a BLOCK TAG. `clean` flattens that tag into the
    same text run, so the location segment swallows the body and the cap can then only
    make the result SHORTER, never correct -- measured on the live Portless comment
    (hn_id 48889875), the cap alone leaves 83 characters of which 49 are pitch copy.
    Splitting the DECODED markup first returns the header outright.

    Both are load-bearing. Over 196 live comments, maximum location length: cap alone
    119, split alone 548, split-then-cap 97. The split fixes the common case; the cap
    catches the tail where a comment carries no block tag before a long header. Removing
    either one regresses the other's weak side -- do not "simplify" one away.

    The markup below is VERBATIM from the live Firebase item, elided only in the body.
    `flat.ndjson` CANNOT substitute for it: its `text` is post-strip and contains zero
    markup tags on all 196 rows, so the boundary this pins is structurally invisible
    there. That is why this fixture is inline rather than reconstructed from the corpus.
    """
    from job_radar.sources import _hn_rows

    portless = (
        "Portless | AI Engineer (Founding seat) | Remote (North America) | $180k-$230k"
        "<p>AI usage today is basic &#x2F; decks, spreadsheets, a few people vibe-coding "
        "dashboards. No real agentic workflows yet. This is a founding seat to build "
        "them end to end."
    )
    out: list = []
    _hn_rows({"children": [{"text": portless, "id": 48889875, "author": "x"}]}, out)
    assert out[0]["location"] == "Remote (North America) $180k-$230k", (
        "the header ends at the <p>, and nothing after it is a location"
    )
    # The mutation this pins: drop the split and the body arrives, capped at 64 per
    # segment rather than removed. Asserting the ABSENCE of body prose is what makes
    # this test fail when the split is deleted -- a length bound alone would not,
    # because the cap already bounds the length.
    assert "vibe-coding" not in out[0]["location"]
    assert "AI usage" not in out[0]["location"]

    # THE FALLBACK. A comment whose block tag precedes its pipes has no header to split,
    # and must keep the old behaviour rather than emptying a populated location. 4 of 196
    # comments split short today and 0 of them lose a location; this keeps that true when
    # a thread formats differently next month.
    leading_block = (
        "<p>Acme Corp | Staff Engineer | Remote (US) | Full-time<p>We build things."
    )
    out2: list = []
    _hn_rows({"children": [{"text": leading_block, "id": 1, "author": "y"}]}, out2)
    assert out2[0]["location"].startswith("Remote (US)"), (
        "an unsplittable header must fall back, never empty"
    )


def _hn_one(text, company_id=1):
    from job_radar.sources import _hn_rows

    out: list = []
    _hn_rows({"children": [{"text": text, "id": company_id, "author": "a"}]}, out)
    return out[0]


def test_hn_reads_the_href_not_the_truncated_anchor_text():
    """HN renders a long link as `<a href="FULL">https://…/j...</a>`. `clean` strips the
    tag, keeps the DISPLAY text, and throws the href away -- so the old prose regex
    recovered a URL with a literal ellipsis in it, which 404s. 48 of 196 rows, skewed to
    `boards.greenhouse.io` and `jobs.lever.co`: the bug destroyed the best links.

    Live measurement over 196 comments, before -> after:
        truncated (dead)  48 -> 0     deep link  69 -> 107
        bare homepage     46 -> 34    hn thread  33 ->  55

    Markup is entity-encoded exactly as HN sends it -- `&#x2F;` for a slash. Decoding
    BEFORE matching is load-bearing and is the same ordering bug 0.9.0 fixed in `clean`.
    """
    encoded = (
        "Acme Corp | Staff Engineer | Remote (US) | Full-time "
        '<a href="https:&#x2F;&#x2F;boards.greenhouse.io&#x2F;acmecorp&#x2F;jobs&#x2F;'
        '596644?gh_jid=596644">https:&#x2F;&#x2F;boards.greenhouse.io&#x2F;acmecorp&#x2F;j'
        "...</a><p>We build things."
    )
    assert (
        _hn_one(encoded)["url"]
        == "https://boards.greenhouse.io/acmecorp/jobs/596644?gh_jid=596644"
    ), "the full target was in the markup the whole time"


def test_hn_will_not_hand_you_another_employers_board():
    """A host check proves a board is REAL, never WHOSE it is. Measured: the Phaselaw
    comment carries `jobs.ashbyhq.com/Pear-VC/...` -- an investor's board posting for a
    portfolio company. `_is_direct_apply` passes it on the ATS allowlist alone.

    Handing a user the wrong employer's posting is worse than handing them a broken
    link: the broken one fails visibly, this one looks right. Gated on RECOVERED links
    only -- a URL the poster typed is never second-guessed."""
    from job_radar.sources import _slug_owns_company

    assert not _slug_owns_company(
        "https://jobs.ashbyhq.com/Pear-VC/361caae7", "Phaselaw"
    )
    # A slug ABBREVIATES, so containment has to run both ways: "Eleos Technologies"
    # ships as `eleostech`, and equality would reject a correct board.
    assert _slug_owns_company(
        "https://jobs.lever.co/eleostech/b48", "Eleos Technologies"
    )
    assert _slug_owns_company("https://jobs.ashbyhq.com/Nango", "Nango")
    assert _slug_owns_company(
        "https://job-boards.greenhouse.io/canopyworks/jobs/4317128009", "Canopy"
    )
    # Nothing to check is not evidence of a mismatch -- a bare careers page has no slug,
    # and `_ATS_HOSTS` already carried the positive evidence that got it here.
    assert _slug_owns_company(
        "https://www.adalat.ai/careers/engineering-lead", "Adalat AI"
    )

    # AND THE GATE IS WIRED IN, which the three assertions above do not show. Deleting
    # `and _slug_owns_company(...)` from `_hn_url` leaves every one of them green -- it
    # was measured, not assumed, and it is the exact green-but-blind shape this file
    # keeps finding. Only a row driven through `_hn_rows` pins the call site.
    wrong_owner = (
        "Phaselaw | Founding Engineer | Remote | Full-time https://phase.law "
        '<a href="https://jobs.ashbyhq.com/Pear-VC/361caae7">apply</a>'
        "<p>We build things."
    )
    assert _hn_one(wrong_owner)["url"] == "https://phase.law", (
        "an investor's board is not this employer's posting"
    )


def test_hn_prefers_a_dead_end_it_can_explain_over_one_it_cannot():
    """Two rules that only look like the same rule.

    TIER 1 ONLY. `_best_apply_link`'s middle tier is "not on the known-aggregator list",
    running on a list measured as under-populated. Applied here it promotes 15
    unclassified hosts and, on the Double Holo row, trades an employer homepage for a
    LinkedIn redirect -- on a product whose entire claim is direct-to-employer.

    THREAD OVER A KNOWN 404. When no tier-1 href exists and the prose URL is truncated,
    the comment link wins: it reaches the posting's details, and `_is_direct_apply` reads
    False on it, so the row stays honestly not-direct rather than being promoted."""
    # The real Double Holo row: the poster typed their homepage, and the comment also
    # carries a LinkedIn href DEEPER than it. A "prefer the deeper link" rule takes the
    # aggregator; tier-1 refuses it and the employer's own domain stands.
    aggregator = (
        "Double Holo | Engineer | Remote | Full-time https://doubleholo.com "
        '<a href="https://www.linkedin.com/jobs/view/4438695357">jobs</a>'
        "<p>We build things."
    )
    assert _hn_one(aggregator)["url"] == "https://doubleholo.com", (
        "an aggregator must never displace the employer's own link"
    )

    truncated_only = (
        "Acme Corp | Staff Engineer | Remote (US) | Full-time "
        "https://uctalent.io/referral/Huynh_Nhu/DNwhgw...<p>We build things."
    )
    got = _hn_one(truncated_only, company_id=48889875)["url"]
    assert got == "https://news.ycombinator.com/item?id=48889875", (
        "a truncated URL is a known 404; the thread reaches the posting"
    )
    from job_radar.sources import _is_direct_apply

    assert not _is_direct_apply(got, "Acme Corp"), (
        "the fallback must not promote the row to direct_apply"
    )


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

    `department` is deprecated but still emitted. This docstring used to say jobfitr
    reads it; verified 2026-08-20, **it does not** — the column is absent from that
    consumer's production schema and its store reads it zero times. The gate is kept
    anyway, because it costs 0.07s and an unreleased or third-party consumer may pin a
    version that does read it, and removing the column before 1.0 would break them at a
    MINOR version. Keep the gate; do not repeat the claim that justified it.

    KNOW WHAT THIS GATE CANNOT SEE. It calls the adapters DIRECTLY and never imports
    `engine`, so it compares raw adapter output. A change to `department` made in the
    adapter turns it red, correctly — but the same change made after the adapter, at
    the engine boundary, leaves it green while every consumer's value changes. Anything
    touching this field must therefore touch it in the adapter, or extend this gate to
    compare post-engine records.

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

    # THE DISPLAY STRING IS INSIDE THE SPLIT, NOT BESIDE IT. The commitment columns
    # were already null on a predicted row while `salary` still rendered "$61,482" --
    # a string a human reads as an employer's offer, and the only field most UIs show.
    # 221 of 277 adzuna rows locally, ~6,781 of 7,150 in the live consumer's store.
    # `None`, not `""`: the adapter emits "" and `_coerce` normalizes every nullable
    # text field to None. Asserting "" here passes on the adapter and fails on the
    # record a consumer actually receives.
    assert predicted["salary"] is None, "a prediction rendered as a display string"
    assert real["salary"] == "$115,000–$145,000", "a real range lost its display string"


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


def test_direct_apply_reads_the_url_too_and_can_only_ever_raise():
    """The test above passes NO url, so it exercises only the source branch and cannot
    see that the URL was ignored -- which is how `direct_apply` stayed per-SOURCE while
    its own docstring said per-URL. `jobs.ashbyhq.com` is direct on 1,202 rows and
    not-direct on 10, the only difference being that hn carried the second set.

    MONOTONE IS THE WHOLE DESIGN. Replacing the source rule with the URL rule demotes
    2,638 rows in a 67,481-row production store against 85 gained -- 31:1, all genuine
    employer careers pages, and they rot rather than drop: intake rejects them every
    harvest while the stale rows stay served."""
    from job_radar import engine

    # RAISES: hn is not a depth source, but this link reaches the employer's ATS.
    assert (
        engine._coerce(
            {
                "title": "x",
                "source": "hn",
                "url": "https://jobs.ashbyhq.com/runway-ml/c3e",
            }
        )["direct_apply"]
        is True
    )

    # NEVER LOWERS: a depth source stays direct even on a host the URL rule cannot read.
    # This is the 2,638 in one assertion -- `okta` is 4 characters, so the employer-domain
    # branch is structurally blind to Okta's own careers page.
    assert (
        engine._coerce(
            {
                "title": "x",
                "source": "greenhouse",
                "company": "Okta",
                "url": "https://www.okta.com/company/careers/opportunity/8139696",
            }
        )["direct_apply"]
        is True
    )
    # ...and an aggregator with no positive evidence stays false.
    assert (
        engine._coerce(
            {"title": "x", "source": "remoteok", "url": "https://remoteok.com/l/123"}
        )["direct_apply"]
        is False
    )

    # A RECOVERED link does not promote. Of 5 rows the ATS-platform additions newly
    # reach, 4 are 404/410 -- promoting on host shape would assert "you can apply here"
    # about a dead posting, which is the defect this release fixes, pointed the other way.
    assert (
        engine._coerce(
            {
                "title": "x",
                "source": "hn",
                "url": "https://jobs.ashbyhq.com/runway-ml/c3e",
                "_url_recovered": True,
            }
        )["direct_apply"]
        is False
    )


def test_the_company_name_can_be_the_ats_subdomain():
    """`bitpay.applytojob.com` matched on `bitpay` and read as BitPay's own careers page.
    The company name was the ATS's SUBDOMAIN and a substring test cannot tell those apart.

    All 6 measured rows were real ATS platforms, so they were right BY ACCIDENT -- the
    identical rule promotes `nike.some-aggregator.com`. The platforms are named in
    `_ATS_HOSTS` so those rows keep their verdict through the INTENTIONAL branch, which
    is why that addition and this tightening are one change and not two."""
    from job_radar.sources import _ATS_HOSTS, _is_direct_apply

    # Right for the right reason now: the platform is named, not the subdomain guessed.
    assert "applytojob.com" in _ATS_HOSTS
    assert _is_direct_apply("https://bitpay.applytojob.com/apply/scufGKyASo/", "BitPay")
    # The latent hole, closed: an employer-named subdomain on a host nobody vouched for.
    assert not _is_direct_apply("https://nike.some-aggregator.com/jobs/1", "Nike Inc")
    # A real careers page on the employer's own registrable domain still passes.
    assert _is_direct_apply("https://careers.acmehealth.com/jobs/1", "Acme Health Inc")
    # `personio.de` was listed while the same vendor's .com was not -- the gap that
    # survives an audit because a reader sees "Personio" in the tuple and ticks it off.
    assert _is_direct_apply(
        "https://friendlycaptcha.jobs.personio.com/job/2686399", "Friendly Captcha"
    )
    # ACCEPTED RESIDUAL. Shared hosting where the SUBDOMAIN is the owner is invisible to
    # a registrable-domain match. 1 row in 7,545. Allowlisting github.io would certify
    # every project page on GitHub as a direct apply -- a far worse trade, so this stays
    # failing ON PURPOSE and a future reader must not "fix" it.
    assert not _is_direct_apply("https://joulent.github.io/careers/", "Joulent")


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
      # ET, not the vendor's UTC day: midnight UTC is 20:00 the PREVIOUS day in
    # Eastern. `to_date` converts an offset-bearing instant rather than truncating
    # it, so a `...T00:00:00Z` fixture legitimately lands one day earlier.
    assert r["expires"] == "2026-09-29"


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

      # ET, not the vendor's UTC day: midnight UTC is 20:00 the PREVIOUS day in
    # Eastern. `to_date` converts an offset-bearing instant rather than truncating
    # it, so a `...T00:00:00Z` fixture legitimately lands one day earlier.
    assert posted_from("2026-07-30T00:00:00Z") == {
        "posted": "2026-07-29",
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


def test_an_empty_restriction_list_means_worldwide_not_unknown():
    """himalayas' own API doc: "An empty array [] means the job is open worldwide with no
    geographic restrictions." The adapter wrote `", ".join(...) or None`, which recorded
    that as "we don't know" on 29 measured rows -- throwing away the most permissive rows in
    the feed. catalog/himalayas.md had documented the rule; the code ignored it."""
    from job_radar.sources import stated_scope

    assert stated_scope([])["remote_areas"] == []
    assert stated_scope(None)["remote_areas"] is None
    # and the two are not the same thing -- that distinction is the whole point
    assert stated_scope([])["remote_areas"] is not None


def test_a_country_name_containing_a_comma_survives_intact():
    """The list was joined into a string, and ISO names contain commas: "Congo, The
    Democratic Republic of the" and "Micronesia, Federated States of" are both real and both
    in the corpus. Re-splitting on ", " produced fragments like "Federated States of" as if
    they were countries. The join survives only as the DISPLAY string."""
    from job_radar.sources import stated_scope

    got = stated_scope(["Congo, The Democratic Republic of the", "United States"])
    assert got["remote_areas"] == ["CD", "US"]
    assert "Congo, The Democratic Republic of the" in got["remote_scope_raw"]


def test_an_unmappable_restriction_is_unstated_never_worldwide():
    """A non-empty list whose members do not resolve must NOT collapse to `[]`. Asserting
    "open worldwide" because a lookup failed is the worst available direction to be wrong."""
    from job_radar.sources import stated_scope

    assert stated_scope(["Narnia"])["remote_areas"] is None
    assert stated_scope(["Narnia"])["remote_scope_raw"] == "Narnia"


def test_remote_scope_returns_areas_and_regions_separately():
    """Two fields because they are two kinds of value. `areas` are ISO codes; `regions` are
    multi-country tokens that have no ISO code and whose membership only the employer can
    settle. One column holding both is the failure this replaced."""
    from job_radar.vocab import remote_scope

    assert remote_scope("Remote - Brazil") == (["BR"], None)
    assert remote_scope("Philippines (Remote)") == (["PH"], None)
    assert remote_scope("Remote - EMEA") == (None, ["EMEA"])
    # ALL matches, not the longest: this string names two countries and used to keep one.
    assert remote_scope("Remote - US & Canada") == (["CA", "US"], None)
    # BOTH fields when the string states both. An early return on regions discarded every
    # country on 111 measured rows.
    assert remote_scope("Americas (USA or Canada) (Remote)") == (
        ["CA", "US"],
        ["AMERICAS"],
    )
    # ISO 3166-2 for a stated US state -- never a bare `TX`, because seven codes are both a
    # US state and an ISO country and the column would be undecidable.
    assert remote_scope("Remote - TX") == (["US-TX"], None)


def test_anywhere_is_unbounded_only_when_no_place_is_named():
    """A posting that names a bound and also says "anywhere" is BOUNDED. Gating the
    unbounded fallback on `areas` being empty was wrong, because areas is also empty when
    the country pass was skipped by the city test -- so "Anywhere in France, Belgium, Spain"
    (11 rows) and "Any location, United States" (12) claimed stated-worldwide while naming
    three countries and one. Asserting a posting is open to the world when it named a bound
    is the worst direction this field can be wrong in."""
    from job_radar.vocab import remote_scope

    assert remote_scope("Anywhere in France, Belgium, Spain") == (
        ["BE", "ES", "FR"],
        None,
    )
    assert remote_scope("Any location, United States")[0] != []
    assert remote_scope("Anywhere in Texas")[0] != []
    # the genuine article still resolves
    assert remote_scope("Remote - Anywhere") == ([], None)
    assert remote_scope("Worldwide") == ([], None)


def test_ashby_secondary_locations_become_the_locations_list():
    """Ashby ships a structured per-place array and the adapter used to ignore it.

    `secondaryLocations` is populated on 422 of 1,730 live postings (24.4%) and carries
    a full addressLocality / addressRegion / addressCountry per entry, in a response
    already fetched. Without it Ashby emitted no `locations` key at all and `_coerce`
    fell back to splitting the DISPLAY string -- which on this source names one place,
    so a posting open in three offices reported one.

    The nested `state` is canonicalized HERE because nothing downstream does it:
    `_coerce` applies the US-state-is-a-code rule to the scalar only, and builds
    `locations[]` only when an adapter left it None.
    """
    from job_radar.sources import _ashby_locations

    def addr(city, region, country):
        return {
            "postalAddress": {
                "addressLocality": city,
                "addressRegion": region,
                "addressCountry": country,
            }
        }

    got = _ashby_locations(
        {
            "location": "San Francisco",
            "address": addr("San Francisco", "California", "United States"),
            "secondaryLocations": [
                {
                    "location": "Toronto",
                    "address": addr("Toronto", "Ontario", "Canada"),
                },
                {
                    "location": "Seattle",
                    "address": addr("Seattle", "Washington", "United States"),
                },
            ],
        }
    )
    assert [e["raw"] for e in got] == ["San Francisco", "Toronto", "Seattle"]
    # a US subdivision is a CODE, matching the scalar rule; a non-US one keeps its name
    assert [e["state"] for e in got] == ["CA", "Ontario", "WA"]
    # NO per-place url. Entries carried the posting's own on every one until 0.9.0.
    assert all("url" not in e for e in got)
    # one place is not a list -- `_coerce` owns the single-place shape
    assert _ashby_locations({"location": "Berlin", "address": None}) is None


def test_ashby_region_that_is_the_country_repeated_is_dropped():
    """A vendor putting the country in `addressRegion` is not stating a subdivision.

    RAW STRING EQUALITY, and the alternative is why. "Drop a region that RESOLVES to the
    row's own country" reads as the more principled rule and destroys real data:
    `England` resolves to GB, but England IS an ISO 3166-2:GB subdivision (GB-ENG), and
    27 of the 34 values that rule deleted on 1,730 live postings were exactly that -- 39%
    collateral. `vocab._COUNTRY_CODES` carries `england -> GB` for PROSE matching; using
    it to validate a data column is the error.
    """
    from job_radar.sources import _ashby_place

    def pa(**kw):
        return {"postalAddress": kw}

    assert _ashby_place(pa(addressRegion="UK", addressCountry="UK"))["state"] is None
    assert (
        _ashby_place(pa(addressRegion="Australia", addressCountry="Australia"))["state"]
        is None
    )
    # ...and a REAL subdivision that merely resolves to the same country survives
    assert (
        _ashby_place(pa(addressRegion="England", addressCountry="United Kingdom"))[
            "state"
        ]
        == "England"
    )
    # the vendor's own trailing whitespace never reaches a grouped column
    assert (
        _ashby_place(pa(addressRegion="California ", addressCountry="United States"))[
            "state"
        ]
        == "California"
    )


def test_a_city_is_never_a_country_region_or_state_name():
    """THE ACCEPTANCE METRIC for the geography fixes, and it is deliberately not a
    fill-rate one.

    "Rows that gained a `state`" reads IDENTICALLY for the right fix and the wrong one:
    re-splitting "Americas, Europe, Israel" into city="Americas" scores as a win on that
    metric while turning visible junk into plausible junk. The number that separates them
    is how many rows gained a `city` that is not a city at all.

    THE EXEMPTIONS ARE LOAD-BEARING and were found by running the naive version: "New
    York" (316 corpus parts), "Washington" (124) and "Singapore" are real cities that are
    ALSO a state or a country, so a blanket "reject a city that is a state name" nulls
    thousands of correct values. A metric that lies is worse than no metric.
    """
    from job_radar.vocab import (
        _COUNTRY_CODES,
        _STATE_NAMES,
        split_place,
        strip_arrangement,
    )

    both_a_city_and_not = {"new york", "washington", "singapore"}
    for raw in (
        "Americas, Europe, Israel (Remote)",
        "LATAM,  Brazil (Remote)",
        "Canada, United States (Remote)",
        "Remote - Illinois, USA",
        "Texas, United States",
        "Singapore, Singapore",
    ):
        city = split_place(strip_arrangement(raw))["city"]
        if city is None:
            continue
        low = city.strip().lower()
        assert low in both_a_city_and_not or (
            low not in _STATE_NAMES and low not in _COUNTRY_CODES
        ), f"{raw!r} put a non-city in `city`: {city!r}"


def test_a_parsed_city_came_from_the_string_it_was_parsed_from():
    """The mechanical half of the gate: a `city` must be present in its own input.

    Cheap, total, and it catches manufacture -- a value assembled rather than read.
    BLIND TO the wrong-token case by construction: a neighborhood picked out of
    "Grand Central, Manhattan" passes this perfectly, because it really is in the
    string. That one needs the vendor's hierarchy, not this check.
    """
    from job_radar.vocab import split_place, strip_arrangement

    for raw in (
        "Costa Mesa, California, United States",
        "Toronto, Ontario, Canada",
        "New York, New York, United States",
        "Mountain View, California",
        "Waco, TX",
        "London, United Kingdom",
    ):
        city = split_place(strip_arrangement(raw))["city"]
        if city is not None:
            assert city.lower() in raw.lower(), f"{city!r} is not in {raw!r}"


def test_a_list_of_countries_is_not_a_city():
    """`rpartition` leaves an enumeration in the head slot, and writing it as `city` is the
    permanently-wrong row split_place refuses to create. 99 measured locations.

    The test is 2+ DISTINCT COUNTRY NAMES, not "contains a comma": a comma test would null
    "Austin, Texas" and "New York, New York", ordinary city+state heads that account for
    7,785 locations with a comma inside the head, against 99 genuine country lists. Trading 99
    wrong values for thousands of right ones is the wrong direction."""
    from job_radar.vocab import split_place, strip_arrangement

    assert (
        split_place(
            strip_arrangement("Australia, Canada, Germany, United Kingdom (Remote)")
        )["city"]
        is None
    )
    # ...while an ordinary city+state head SURVIVES -- still the claim this test makes,
    # and still true. What changed is the shape, not the guard: split_place now re-splits
    # a comma-bearing head when the tail is a spelled-out country, so the subdivision
    # reaches `state` instead of riding along inside `city`. The docstring's trade is
    # unchanged and is why this is not a comma test; it is narrowed because the head can
    # now be read, not because it was ever wrong to keep it.
    assert split_place("Austin, Texas, United States")["city"] == "Austin"
    assert split_place("Austin, Texas, United States")["state"] == "TX"
    assert split_place("Waco, TX")["city"] == "Waco"


def test_a_bounded_anywhere_string_is_not_unbounded():
    """The regression the Worldwide fix introduced, caught before release. The anywhere
    pattern is a word-boundary SEARCH, so "Anywhere in the US" and "Worldwide except China"
    matched it and returned [] -- a BOUNDED posting asserting it is open to the world.
    `_region_allowed` treats [] as satisfying every policy, so "Anywhere in the US" would
    have been admitted into a Germany-only filter. The whole trimmed name must BE an
    anywhere-word."""
    from job_radar.sources import stated_scope

    for bounded in (
        "Anywhere in the US", "Anywhere in Europe", "Worldwide except China", "Global South",
        "Remote (Anywhere in LATAM)", "Anywhere in the EU",
    ):  # fmt: skip
        assert stated_scope(bounded)["remote_areas"] != [], bounded
    # the genuine article still reads as unbounded
    assert stated_scope("Worldwide")["remote_areas"] == []


def test_a_list_of_blanks_is_malformed_not_worldwide():
    """Only a genuinely EMPTY list carries himalayas' "open worldwide" meaning. `["", None]`
    is a vendor sending junk, and reading it as unbounded asserts the most permissive
    possible value from the least information."""
    from job_radar.sources import stated_scope

    assert stated_scope(["", None])["remote_areas"] is None
    assert stated_scope([])["remote_areas"] == []


def test_the_continents_remotive_actually_sends_are_all_kept():
    """ "Americas, Europe, Asia, Africa, Oceania" is remotive's canonical five-continent
    value -- the worked example in catalog/remotive.md -- on 11% of a live feed. Asia,
    Africa and Oceania were silently dropped, making a five-continent role invisible to a
    searcher on three of them."""
    from job_radar.sources import stated_scope

    got = stated_scope("Americas, Europe, Asia, Africa, Oceania")["remote_regions"]
    assert got == ["AFRICA", "AMERICAS", "ASIA", "EUROPE", "OCEANIA"]


def test_the_word_worldwide_is_stated_unbounded():
    """Found by probing a LIVE endpoint, not by a fixture -- 6 of 18 real remotive rows came
    back unstated with a raw of "Worldwide". himalayas says unbounded with an empty array,
    which this already honoured; remotive and jobicy say it with the WORD, and the adapter
    path did not know it. Same bug as the empty array: a vendor declaring no geographic
    restriction, recorded as "we don't know"."""
    from job_radar.sources import stated_scope

    assert stated_scope("Worldwide")["remote_areas"] == []
    assert stated_scope("anywhere")["remote_areas"] == []
    # ...but a list that ALSO names a place is bounded, not unbounded
    assert stated_scope(["Worldwide", "France"])["remote_areas"] == ["FR"]


def test_a_blank_vendor_string_is_not_stated_worldwide():
    """himalayas' EMPTY ARRAY means "open worldwide". A blank STRING from remotive or jobicy
    means the vendor said nothing, and collapsing the two asserts a posting is open to the
    world because a field happened to be empty."""
    from job_radar.sources import stated_scope

    assert stated_scope("")["remote_areas"] is None
    assert stated_scope("   ")["remote_areas"] is None
    assert stated_scope([])["remote_areas"] == []  # the array still means worldwide


def test_a_comma_bearing_country_name_survives_the_string_branch():
    """15 ISO names contain a comma and one re-splits into a DIFFERENT real country:
    "Congo, The Democratic Republic of the" (CD) becomes "Congo" (CG). A well-formed code
    naming the wrong country is worse than no code, so the whole string is tried first."""
    from job_radar.sources import stated_scope

    assert stated_scope("Congo, The Democratic Republic of the")["remote_areas"] == [
        "CD"
    ]
    assert stated_scope("USA, EMEA")["remote_areas"] == ["US"]


def test_remote_scope_takes_only_STATED_boundaries():
    """An office address is not an eligibility boundary. The rule applies to countries and
    subdivisions alike -- scoping it to subdivisions made it a carve-out, and on 6,779
    area-carrying rows `remote_areas` then meant nothing more than `country`.

    THE FIRST FIVE STRINGS PASSED WHILE THE INVARIANT WAS BROKEN ON 622 CORPUS ROWS, and
    the reason is the point of the second block: four of them pass because `has_city` is
    TRUE on them -- the one shape where that proxy works -- and the fifth passes through the
    state-name guard, a different branch entirely. The failure path was never exercised. The
    inputs were hand-written; the corpus's are not, and that difference was the bug.

    So the second block is DRAWN FROM THE CORPUS, with its row count at 781e504 beside each
    string, and it must stay that way. Every one of these returned an office country as a
    stated remote-eligibility boundary before the arrangement gate."""
    from job_radar.vocab import remote_scope

    for office in (
        "Munich, Germany",
        "Sao Paulo, Brazil - Remote",
        "Costa Mesa, California, United States",
        "Atlanta, GA - Remote",
        "New York (Remote)",
    ):
        assert remote_scope(office) == (None, None), office

    # Real locations from a 7,545-row harvest, counts as measured. `has_city` is False on
    # every one -- these are the strings `split_place` could not read, not the ones it read
    # as having no city, which is the distinction the proxy cannot make.
    for office, rows in (
        ("US", 109),
        ("United States", 89),
        ("London, UK", 50),  # split_place does not resolve a bare "UK" tail
        ("San Mateo, CA United States", 48),  # no comma before the country
        ("Singapore", 34),
        ("San Francisco, CA • New York, NY • United States", 23),  # bullet-separated
        ("Canada", 22),
        ("Mexico City", 7),  # a CITY whose name ends in a country name
        ("US > Arizona > Phoenix", 0),  # prod-only shape; 0 in this harvest, 43 in prod
        ("Cambridge, MA USA", 0),  # prod-only, 82 rows there
    ):
        assert remote_scope(office) == (None, None), f"{office!r} ({rows} rows)"


def test_remote_scope_does_not_read_new_mexico_as_mexico():
    """The word-boundary lookbehind accepts a space, so `mexico` matched inside `New
    Mexico` once ALL matches were collected -- 63 rows. `_longest_match` had hidden it."""
    from job_radar.vocab import remote_scope

    areas, _ = remote_scope("Remote - New Mexico")
    assert areas is None or "MX" not in areas
    assert remote_scope("Remote - Mexico") == (["MX"], None)


def test_remote_scope_distinguishes_unstated_from_stated_unbounded():
    """Three states, and the middle one is why this is a list. `[]` is a posting that says
    anywhere; `None` is one that says nothing. Collapsing them either drops the most
    permissive rows in the feed or admits the ones nobody classified."""
    from job_radar.vocab import remote_scope

    assert remote_scope("Remote") == (None, None)
    assert remote_scope("") == (None, None)
    assert remote_scope("Remote - Anywhere") == ([], None)
    assert remote_scope("Worldwide") == ([], None)
    # ...but a country named alongside "anywhere" wins: "anywhere IN Brazil" is bounded.
    assert remote_scope("Remote - Anywhere in Brazil") == (["BR"], None)


def test_no_region_token_is_a_valid_country_code():
    """THE ANTI-MIXING INVARIANT, and it must be checked against the FULL ISO set. Checking
    only this module's narrow 62-name map is how the first version passed vacuously while
    shipping `TZ` as the timezone sentinel -- TZ is Tanzania."""
    from job_radar.iso3166 import NAME_TO_ALPHA2
    from job_radar.vocab import REMOTE_AREA_RE, REMOTE_REGION_TOKENS

    assert not (REMOTE_REGION_TOKENS & frozenset(NAME_TO_ALPHA2.values()))
    # and no region token is even SHAPED like an ISO area
    assert not [t for t in REMOTE_REGION_TOKENS if REMOTE_AREA_RE.match(t)]


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


def test_ashby_reads_the_html_body_because_plain_text_carries_no_headers(monkeypatch):
    """The 0.9.0 defect: this adapter read `descriptionPlain`, so `sections` was []
    on 1,198 of 1,198 Ashby rows -- the feature dead on the second-largest source.

    Asserted POSITIONALLY, on a header the plain field does not contain at all. A
    test that only checked `len(sections) > 0` would pass against either field the
    moment the plain text happened to carry a colon, and the repo has already been
    bitten by tests that survive mutation of the thing they exist to pin.
    """
    payload = {
        "jobs": [
            {
                "title": "Applied AI Engineer",
                "location": "Remote",
                "jobUrl": "https://jobs.ashbyhq.com/acme/1",
                # BOTH present, as they are on 457/457 live postings. The headers
                # exist only in the markup; the plain field is the same prose with
                # every structural cue flattened out of it.
                "descriptionHtml": (
                    "<p><strong>ABOUT ACME</strong></p><p>We build things.</p>"
                    "<p><strong>RESPONSIBILITIES</strong></p><p>Ship models.</p>"
                ),
                "descriptionPlain": (
                    "ABOUT ACME\n\nWe build things.\n\nRESPONSIBILITIES\n\nShip models."
                ),
            }
        ]
    }
    monkeypatch.setattr(sources, "get_json", lambda url: payload)
    (row,) = sources.fetch_ashby("acme")

    headers = [s["header"] for s in row["sections"] if s.get("header")]
    assert "RESPONSIBILITIES" in headers, (
        f"read the plain field -- markup-derived headers absent. got {headers!r}"
    )
    assert any(s["type"] == "responsibilities" for s in row["sections"])
    # The span must index THIS row's own text, and must resolve to the section it
    # names -- not merely to a valid slice. Asserted against the OTHER section's
    # prose too, because a wrong-span bug preserves every string-identity check
    # while pointing at the neighbouring block; that is how one shipped before.
    sec = next(s for s in row["sections"] if s.get("header") == "RESPONSIBILITIES")
    span = row["text"][sec["start"] : sec["end"]]
    assert "Ship models." in span
    assert "We build things." not in span
    # Markup never survives into the body.
    assert "<p>" not in row["text"] and "<strong>" not in row["text"]


def test_ashby_falls_back_to_plain_when_the_vendor_sends_no_html(monkeypatch):
    """`descriptionHtml` was present on 457/457 live postings, so the fallback is a
    safety net against a vendor change -- but a missing HTML field must not empty the
    body, which is what reading `descriptionHtml` ALONE would have done. That failure
    would be silent: a body of "" is a legal value, so nothing raises."""
    payload = {
        "jobs": [
            {
                "title": "Applied AI Engineer",
                "location": "Remote",
                "jobUrl": "https://jobs.ashbyhq.com/acme/1",
                "descriptionPlain": "Plain body only.",
            }
        ]
    }
    monkeypatch.setattr(sources, "get_json", lambda url: payload)
    (row,) = sources.fetch_ashby("acme")
    assert row["text"] == "Plain body only."
    # No markup means no headers -- `[]` is "a body with no headers", NOT `None`,
    # which is reserved for "no body at all".
    assert row["sections"] == []


def test_bodies_are_assembled_from_every_field_the_vendor_splits_them_across():
    """Lever and USAJOBS both put a tenth of the posting in the obvious field and the
    rest in siblings, so reading the obvious one fed the scorer a fragment."""
    # Both helpers return (text, sections) as of 0.9.0 -- the body still has to carry
    # every field, which is what this test is about.
    lever, _ = sources._lever_text(
        {
            "descriptionPlain": "Intro.",
            "lists": [{"text": "Requirements", "content": "<li>Python</li>"}],
            "additionalPlain": "Why us.",
        }
    )
    assert "Intro." in lever and "Requirements" in lever and "Python" in lever
    assert "Why us." in lever

    federal, _ = sources._usajobs_text(
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
        # `text_basis` joins the same enforcement rather than getting its own: the
        # whole point of a closed vocabulary is that one mechanism polices all of them.
        ("text_basis", _vocab.TEXT_BASES),
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
        # a LIST now, and a federal remote role is US-bounded by construction
        "remote_areas": ["US"],
    }
    # Telework-eligible but not remote is partly-in-office, which is what hybrid means.
    hybrid = sources._usajobs_remote(
        {"UserArea": {"Details": {"RemoteIndicator": False, "TeleworkEligible": True}}}
    )
    assert hybrid["remote_type"] == "hybrid" and hybrid["remote_areas"] is None
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


def test_a_us_containing_region_is_never_a_non_us_marker():
    """`northern america` is the UN M49 region CONTAINING the United States.

    For one release it sat in `_NON_US_REGIONS`, the DEFAULT location-exclusion filter
    applied to every source's output and matched against title text -- so "Engineer,
    Northern America" was dropped as non-US while the synonym "North America", never in
    that list, passed. Opposite verdicts on the same place, from the shipped config.

    It reached there because the same four continent names were pasted into two tuples
    with one copied comment; `_CONTINENTS` is spliced into both so the next addition
    cannot land in one and not the other."""
    from job_radar import vocab

    assert "northern america" not in vocab.NON_US_LOCATION_TOKENS
    assert "north america" not in vocab.NON_US_LOCATION_TOKENS
    # Still a real STATED boundary though -- remotive sends it, and dropping it from the
    # scope vocabulary was the other half of the same bug.
    assert "NORTHERN AMERICA" in vocab.REMOTE_REGION_TOKENS
    assert not (set(vocab._CONTINENTS) & {"northern america", "americas"})


def test_the_continents_are_non_us_markers_like_europe_already_was():
    """Pinning a deliberate behaviour change, not an accident.

    `europe`, `apac` and `latam` were already non-US markers, so "Europe Program
    Director" was dropped by the default exclusion while "Asia Program Director" was
    kept -- an inconsistency nobody chose. These three make it consistent. Recorded as a
    test because it is a real change to what the default config filters out, and the
    kind of thing that should fail loudly if someone reverses it by accident."""
    from job_radar import vocab

    for c in ("asia", "africa", "oceania"):
        assert c in vocab.NON_US_LOCATION_TOKENS, c
        assert c in vocab._CONTINENTS, c


def test_a_number_in_the_boundary_field_does_not_kill_the_harvest():
    """`stated_scope(123)` raised `TypeError: 'int' object is not iterable`.

    Errors are values in this package: `engine._coerce` exists because one vendor's JSON
    null once killed a whole harvest on the first `.lower()`. But stated_scope runs
    INSIDE the adapter, upstream of that coercion, so this field had no such protection.
    A number is not a place -- it must resolve to "unknown" while keeping the vendor's
    value as evidence, not raise."""
    from job_radar.sources import stated_scope

    for junk in (123, True, 45.5):
        r = stated_scope(junk)
        assert r["remote_areas"] is None and r["remote_regions"] is None, junk
        assert r["remote_scope_raw"] == str(junk), junk


def test_the_two_lookups_agree_about_whitespace():
    """`iso3166.alpha2` collapses internal whitespace; the region lookup did not.

    So `"North  America"` with a double space resolved to nothing while `" Germany "`
    resolved fine -- two vocabularies disagreeing about spaces inside one function."""
    from job_radar.sources import stated_scope

    assert stated_scope("North  America")["remote_regions"] == ["NORTH AMERICA"]
    assert stated_scope(["NORTH\tAMERICA"])["remote_regions"] == ["NORTH AMERICA"]
    assert stated_scope([" Germany ", "France"])["remote_areas"] == ["DE", "FR"]


def test_the_raw_boundary_is_the_vendors_string_not_a_rebuild():
    """`remote_scope_raw` exists so a consumer that disagrees with our parse can re-read
    the original. Normalizing the parts and rejoining them would quietly rewrite the
    evidence -- the double space below is the vendor's, and it survives."""
    from job_radar.sources import stated_scope

    assert stated_scope("North  America")["remote_scope_raw"] == "North  America"
    assert stated_scope("Americas, Europe, Israel")["remote_scope_raw"] == (
        "Americas, Europe, Israel"
    )


def test_a_continent_inside_a_country_name_is_not_a_stated_region():
    """ "South Africa" contains "africa".

    Adding the continents made ZA the one country in 425 that also carried a continent
    tag, while France carried no EUROPE. The tag is not false — South Africa is in
    Africa — but it is INFERRED, and `remote_scope`'s rule is stated-only: a continent
    deduced from a country is the same inference as a country deduced from an office
    city. It shows downstream too, because regions ADMIT: `[AFRICA]` would have kept
    South Africa while `[EUROPE]` dropped France.

    Masked by span rather than a `(?<!south )` lookbehind, so the whole class is closed
    and not just this one name."""
    from job_radar.vocab import remote_scope

    assert remote_scope("Remote - South Africa") == (["ZA"], None)
    assert remote_scope("Remote - France") == (["FR"], None)
    # The continent itself still resolves when the vendor actually states it, including
    # beside the country whose name contains it.
    assert remote_scope("Remote - Africa")[1] == ["AFRICA"]
    # And a region that merely sits NEXT TO a country is untouched — only a region whose
    # span falls INSIDE a matched country name is masked.
    #
    # The REGION half is this test's subject and is unchanged. The areas half used to read
    # `["IN"]` and that was a wrong value pinned green: "Bengaluru, Karnataka, India" is an
    # office address, `split_place` cannot read it (city/state/country all None), and the
    # docstring's own rule — "a country deduced from an office city" is inference, not a
    # statement — says it must not become a boundary. The incoherence it left: the same
    # string WITHOUT the region returns (None, None), so appending "APAC" was what promoted
    # the office country to an eligibility bound. The arrangement gate in `remote_scope`
    # answers the areas half now; nothing about the span-masking changed.
    assert remote_scope("Bengaluru, Karnataka, India, APAC") == (None, ["APAC"])
    assert remote_scope("Bengaluru, Karnataka, India") == (None, None)


def test_the_state_name_guard_survives_being_hoisted_to_one_alternation():
    """The per-call loop over 50 state names became one module-level alternation — a 7.5x
    win on `remote_scope`, and NOT an output-identical refactor at the intermediate step.

    The old loop searched each name independently, so "Charleston, West Virginia" produced
    the guard set {WV, VA}; a longest-first non-overlapping scan produces {WV}. That is the
    only containment pair in `_STATE_NAMES`, and it is inert because the set is read once as
    `if guards:` — truthiness, never membership.

    This test pins the OBSERVABLE behaviour rather than the set, so it keeps holding if the
    internals change again — and fails loudly if someone ever makes the guard's contents
    load-bearing, which is the one edit that would make the hoist unsafe."""
    from job_radar.vocab import remote_scope

    # A state name GUARDS the unbounded fallback: naming a place means "not worldwide",
    # so none of these may come back as stated-unbounded ([]).
    for s in (
        "Charleston, West Virginia",
        "Anywhere in West Virginia",
        "Remote - West Virginia",
        "Richmond, Virginia",
        "Anywhere in Virginia",
    ):
        areas, _ = remote_scope(s)
        assert areas != [], f"{s} claimed stated-worldwide while naming a state"
    # And the guard still fires for the contained name on its own.
    assert remote_scope("Anywhere in Texas")[0] != []


def test_a_state_after_the_word_new_still_guards_the_unbounded_fallback():
    """The hoist's real landmine, and it was NOT the containment pair.

    `_alternation` carries a `(?<!new )` lookbehind, added for the COUNTRY map so that
    "New Mexico" (a US state) never resolves to Mexico. The per-state loop it replaced
    never had that guard, so reusing the helper silently suppressed any state name after
    the literal word "new" — and New Washington (Ohio, Indiana) and New Virginia (Iowa)
    are real towns. The suppressed guard was the only thing standing between those strings
    and `_REMOTE_ANYWHERE`, so they came back `[]`: STATED-WORLDWIDE for a posting that
    named a place. An empty list satisfies every `allowed_scopes` policy, which makes this
    the one direction this contract exists never to be wrong in.

    Hence `guard_new=False` on the state alternation — while the country map keeps it."""
    from job_radar.vocab import remote_scope

    for s in ("Anywhere in New Washington", "Anywhere, New Washington",
              "Remote (Anywhere) - New Virginia"):  # fmt: skip
        assert remote_scope(s)[0] != [], f"{s} claimed stated-worldwide"
    # The country guard this lookbehind exists for is untouched.
    assert remote_scope("Remote - Mexico")[0] == ["MX"]
    assert remote_scope("Remote - New Mexico")[0] != ["MX"]


def test_a_vendors_own_iso_code_resolves_except_where_it_is_ambiguous():
    """`iso3166.alpha2` maps NAMES, so "United States" resolved while the literal "US" —
    the very string this package stores — did not.

    Half-closed on purpose. Seven codes are both a country and a US state abbreviation,
    and the ambiguity is live: on the prose path `remote_scope("Remote - CA")` returns
    `US-CA`, California. Reading "CA" as Canada here would make two characters mean two
    different places on two paths of one contract, and a wrong country admits a posting
    into a filter that excludes it. Those seven stay silent instead."""
    from job_radar.sources import stated_scope

    for code in ("US", "GB", "FR", "JP", "BR"):
        assert stated_scope(code)["remote_areas"] == [code], code
    for ambiguous in ("AR", "CA", "CO", "DE", "ID", "IL", "IN"):
        r = stated_scope(ambiguous)
        assert r["remote_areas"] is None, f"{ambiguous} must stay unresolved"
        # Silent, but never lost — the raw value is what a re-parse would read.
        assert r["remote_scope_raw"] == ambiguous
    # Junk is still junk.
    assert stated_scope("ZZ")["remote_areas"] is None
    assert stated_scope("XX-YY")["remote_areas"] is None


def test_a_subdivision_code_resolves_because_it_cannot_be_ambiguous():
    """`US-TX` names exactly one place. `remote_scope` already EMITS this form,
    `REMOTE_AREA_RE` blesses it and `_region_allowed` resolves it — so accepting it makes
    the adapter path agree with the prose path instead of diverging from it.

    The suffix is deliberately unvalidated: under `_region_allowed`'s `startswith` rule a
    bogus `US-XX` can only narrow a match, never broaden one."""
    from job_radar.sources import stated_scope
    from job_radar.vocab import REMOTE_AREA_RE

    assert stated_scope("US-TX")["remote_areas"] == ["US-TX"]
    assert stated_scope("us-tx")["remote_areas"] == ["US-TX"]
    assert stated_scope(["US-CA", "Canada"])["remote_areas"] == ["CA", "US-CA"]
    for a in stated_scope("US-TX")["remote_areas"]:
        assert REMOTE_AREA_RE.match(a), a


def test_rippling_org_unit_reaches_team(monkeypatch):
    """THE PRECONDITION FOR REMOVING `department` at 0.9.0.

    Rippling was the ONE adapter of nineteen that filled the deprecated `department`
    and set neither `team` nor `category`. Every other one assigns its org unit to
    `team` or its job family to `category` on the line beside the `department` it also
    filled, so dropping that field cost them nothing -- but on this adapter it would
    have silently deleted the org unit from every row.

    No corpus measurement could see it: Rippling is keyless but was not among the 14
    sources in the harvest the cut was measured on. It was found by reading all
    nineteen adapters instead of counting rows, which is the only method that covers
    a source that did not run.
    """
    listing = [
        {
            "uuid": "u1",
            "name": "AI Engineer",
            "department": {"id": "Eng", "label": "Engineering"},
            "url": "https://ats.rippling.com/acme/jobs/u1",
            "workLocation": {"label": "Remote (US)"},
        }
    ]
    monkeypatch.setattr(sources, "get_json", lambda url, *a, **k: listing)
    monkeypatch.setattr(sources.time, "sleep", lambda s: None)
    j = sources.fetch_rippling("acme")[0]
    assert j["team"] == "Engineering", "Rippling's org unit no longer reaches the record"


@pytest.mark.parametrize(
    "name", sorted(set(sources.DEPTH_ALL) | set(sources.BREADTH_ALL))
)
def test_no_adapter_puts_a_url_on_a_location(name, monkeypatch):
    """`locations[]` entries carried the posting's own url until 0.9.0 -- 9,585 of
    9,585 in a 7,568-row harvest, 0 differing from the row's `url`. It was not merely
    redundant: it ADVERTISED a per-place apply link that none of the nineteen sources
    publishes, so a consumer could reasonably have built a per-office apply flow on a
    value that never varied.

    THIS RUNS OVER EVERY ADAPTER because the three that build the list are not the
    ones the corpus covered. `engine._coerce`'s two fallback branches are guarded
    separately by `test_every_locations_element_has_the_same_keys`, which calls the
    engine rather than an adapter -- adapter output is not record output, and one test
    cannot stand in for the other.
    """
    c = _cfg()
    monkeypatch.setattr(c, "env", lambda key: "test-key")
    monkeypatch.setattr(sources, "get_json", lambda url, *a, **k: SAMPLES[name])
    monkeypatch.setattr(sources, "post_json", lambda url, body, *a, **k: SAMPLES[name])
    monkeypatch.setattr(sources.time, "sleep", lambda s: None)
    _usajobs_response(monkeypatch, SAMPLES["usajobs"])
    if name in sources.DEPTH_ALL:
        out = sources.DEPTH_ALL[name]("slug")
    else:
        out = sources.BREADTH_ALL[name](["AI Engineer"])
    want = {"raw", "city", "state", "country"}
    for row in out:
        for el in row.get("locations") or []:
            assert set(el) == want, (
                f"{name}: location keys {sorted(el)} != {sorted(want)} -- a per-place "
                "`url` is the row's own url on every source and was removed at 0.9.0"
            )
