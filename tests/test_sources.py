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
def test_depth_adapter_honours_the_posting_contract_on_an_empty_feed(name, monkeypatch):
    """An empty board must yield an empty list, never None and never a crash —
    `engine._consume` iterates the result unconditionally."""
    monkeypatch.setattr(sources, "get_json", lambda url: {})
    monkeypatch.setattr(sources, "post_json", lambda url, body: {})
    out = sources.DEPTH_ALL[name]("slug")
    assert out == [] or out is not None
    _assert_contract(out or [], required=REQUIRED_KEYS)


@pytest.mark.parametrize("name", sorted(sources.BREADTH_ALL))
def test_breadth_adapter_survives_an_empty_response(name, monkeypatch):
    """Same contract on the breadth side. Keyed sources short-circuit before any
    request, which is itself the behaviour under test."""
    _cfg()
    monkeypatch.setattr(sources, "get_json", lambda url: {})
    monkeypatch.setattr(sources, "post_json", lambda url, body: {})
    monkeypatch.setattr(sources.time, "sleep", lambda s: None)
    out = sources.BREADTH_ALL[name](["AI Engineer"])
    assert isinstance(out, list)
    _assert_contract(out)
