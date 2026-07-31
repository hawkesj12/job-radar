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

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=0: _Resp())


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


def test_remotive_is_capped_at_four_calls(monkeypatch):
    """The rate-limit promise the README makes to Remotive, in code."""
    calls = []
    monkeypatch.setattr(
        sources, "get_json", lambda url: calls.append(url) or {"jobs": []}
    )
    monkeypatch.setattr(sources.time, "sleep", lambda s: None)
    sources.search_remotive([f"q{i}" for i in range(10)])
    assert len(calls) == 4


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


def test_techtree_parser_maps_fields(monkeypatch):
    fake = {
        "jobs": [
            {
                "title": "Founding AI Engineer",
                "company_name": "",  # TechTree fronts hidden clients
                "locations": [{"display_label": "Austin, TX", "country": "US"}],
                "workplace_type": "Remote",
                "application_url": "https://jobs.techtree.dev/apply/1",
                "posted_date": "2026-07-07T00:00:00Z",
                "level": "Senior",
                "job_type": "Full-time",
                "salary_min": 180000,
                "salary_max": 220000,
                "short_description": "Own the AI stack.",
                "requirements": ["RAG", "agents"],
                "skills": ["Python"],
            }
        ]
    }
    monkeypatch.setattr(sources, "get_json", lambda url: fake)
    out = sources.search_techtree(["ai"])
    j = out[0]
    assert j["company"] == "TechTree's client"  # the documented placeholder
    assert "Austin, TX" in j["location"] and j["location"].endswith("(Remote)")
    # requirements + skills are folded into the body so they can be SCORED.
    assert "RAG" in j["text"] and "Python" in j["text"]
    _assert_contract(out, "techtree")


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
                        "PositionSchedule": [{"Name": "Full-Time"}],
                        "PositionRemuneration": [
                            {"MinimumRange": "120000", "MaximumRange": "150000"}
                        ],
                        "UserArea": {"Details": {"JobSummary": "Federal AI work."}},
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
    assert j["employment_type"] == "Full-Time"
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
        body = dict(SAMPLES["google_jobs"])
        # hand out a token twice, then stop
        if len(calls) < 3:
            body["serpapi_pagination"] = {"next_page_token": f"tok{len(calls)}"}
        return body

    monkeypatch.setattr(sources, "get_json", paged)
    out = sources.search_google_jobs(["AI Engineer"])
    assert len(calls) == 3, (
        f"expected to stop when the token ran out, made {len(calls)}"
    )
    assert len(out) == 3


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
    "techtree": {"jobs": [_JOB]},
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
                        "PositionSchedule": [{"Name": "Full-Time"}],
                        "PositionRemuneration": [
                            {"MinimumRange": "120000", "MaximumRange": "150000"}
                        ],
                        "UserArea": {"Details": {"JobSummary": "Federal AI work."}},
                    }
                }
            ]
        }
    },
    # HN parses free-text "Who is hiring" COMMENTS, not a job object, so a job-shaped
    # sample would misrepresent it. Its own parser test covers the text path.
    "hn": {"hits": []},
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

    if name == "hn":  # parses free-text comments; see the SAMPLES note
        assert isinstance(out, list)
        return
    assert out, f"{name}: produced no row from its own sample payload"
    _assert_contract(out, required=required)
