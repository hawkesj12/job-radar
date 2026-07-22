"""Workday adapter + Common Crawl discovery. No network — every fetch is stubbed.

Two of these are regression tests for bugs found by running the real thing against
production data on 2026-07-22; both were silent-wrong, not loud-broken, which is
exactly the class this file exists to pin.
"""

from __future__ import annotations

import json

import pytest

from job_radar import discover, sources


@pytest.fixture(autouse=True)
def _no_workday_details(monkeypatch):
    """Keep the per-job detail pass OFF unless a test opts in. Without this the
    pagination tests fan out real HTTP for every stubbed row — the suite went from
    1.1s to 21s and quietly depended on the network."""
    monkeypatch.setattr(sources, "WORKDAY_FETCH_DETAILS", False)


# ── fetch_workday: pagination ────────────────────────────────────────────────
def _wd_pages(total, n_pages, page=20):
    """Fake Workday: reports `total` ONLY on page 1, mirroring the real API."""
    calls = []

    def fake_post(url, payload):
        calls.append(payload["offset"])
        first = payload["offset"] == 0
        remaining = max(0, total - payload["offset"])
        count = min(page, remaining)
        return {
            "total": total if first else 0,  # <- the trap
            "jobPostings": [
                {
                    "title": f"Role {payload['offset'] + i}",
                    "externalPath": f"/job/City/Role_{payload['offset'] + i}",
                    "locationsText": "Springfield, IL",
                    "postedOn": "Posted 3 Days Ago",
                    "bulletFields": ["R123", "Posting Date: 06/26/2026"],
                }
                for i in range(count)
            ],
        }

    return fake_post, calls


def test_workday_pages_past_the_first_page(monkeypatch):
    """REGRESSION: Workday returns total=0 on every page after the first. Re-reading
    it per page made `offset >= total` true immediately, silently capping EVERY
    employer at 40 roles (2 pages) — a wrong answer that looked like a real board."""
    fake, calls = _wd_pages(total=95, n_pages=5)
    monkeypatch.setattr(sources, "post_json", fake)
    rows = sources.fetch_workday("acme", host="wd1", site="Careers")
    assert len(rows) == 95, f"expected the full board, got {len(rows)}"
    assert calls == [0, 20, 40, 60, 80]


def test_workday_respects_the_page_cap(monkeypatch):
    """A 10k-role tenant must not run away with the nightly harvest budget."""
    fake, calls = _wd_pages(total=10_000, n_pages=999)
    monkeypatch.setattr(sources, "post_json", fake)
    rows = sources.fetch_workday("huge", host="wd5", site="X")
    assert len(rows) == sources.WORKDAY_MAX_PAGES * sources.WORKDAY_PAGE
    assert len(calls) == sources.WORKDAY_MAX_PAGES


def test_workday_maps_fields_and_builds_a_public_url(monkeypatch):
    fake, _ = _wd_pages(total=1, n_pages=1)
    monkeypatch.setattr(sources, "post_json", fake)
    r = sources.fetch_workday("acme", host="wd1", site="Careers")[0]
    assert r["title"] == "Role 0"
    assert r["location"] == "Springfield, IL"
    assert (
        r["url"] == "https://acme.wd1.myworkdayjobs.com/en-US/Careers/job/City/Role_0"
    )
    assert r["posted"] == "2026-06-26"  # absolute date wins over the relative one


def test_workday_falls_back_to_the_relative_date(monkeypatch):
    """Only some tenants put an absolute date in bulletFields; a blank `posted`
    would sink the whole employer in any freshness filter."""

    def fake_post(url, payload):
        return {
            "total": 1,
            "jobPostings": [
                {
                    "title": "T",
                    "externalPath": "/job/x",
                    "locationsText": "",
                    "postedOn": "Posted 3 Days Ago",
                    "bulletFields": ["R1"],  # no Posting Date
                }
            ],
        }

    monkeypatch.setattr(sources, "post_json", fake_post)
    assert sources.fetch_workday("a", host="wd1", site="S")[0]["posted"]


def test_relative_posted_parses_the_known_shapes():
    assert sources._relative_posted("Posted Today")
    assert sources._relative_posted("Posted 30+ Days Ago")
    assert sources._relative_posted("Posted 2 Weeks Ago")
    assert sources._relative_posted("nonsense") == ""


# ── name -> slug precision ───────────────────────────────────────────────────
def test_name_variants_never_emits_a_bare_first_word():
    """REGRESSION: 'Capital One' -> `capital` resolved to a REAL board owned by an
    unrelated company. The probe can't catch that (it returns live jobs), so the bad
    slug passed the gate and would file a stranger's postings under Capital One."""
    for name in ("Capital One", "Veterans Health Administration", "Allied Universal"):
        got = discover.name_variants(name)
        assert "capital" not in got and "veterans" not in got and "allied" not in got
        assert all("-" in v or " " not in v for v in got)


def test_name_variants_strips_legal_suffixes():
    assert "roberthalf" in discover.name_variants("Robert Half, Inc.")
    assert "acme" in discover.name_variants("ACME LLC")


def test_name_variants_ignores_junk():
    assert discover.name_variants("") == []
    assert discover.name_variants("   ") == []


# ── CDX mining ───────────────────────────────────────────────────────────────
def _cdx(monkeypatch, urls):
    class R:
        def read(self):
            return "\n".join(json.dumps({"url": u}) for u in urls).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(discover.urllib.request, "urlopen", lambda *a, **k: R())


def test_mine_greenhouse_dedupes_and_drops_non_slugs(monkeypatch):
    _cdx(
        monkeypatch,
        [
            "https://boards.greenhouse.io/stripe",
            "https://boards.greenhouse.io/stripe/jobs/123",  # same company
            "https://boards.greenhouse.io/embed/job_board",  # not a company
            "https://boards.greenhouse.io/figma",
        ],
    )
    got = discover.mine("greenhouse", collection="CC-MAIN-TEST")
    assert sorted(e["slug"] for e in got) == ["figma", "stripe"]


def test_mine_workday_recovers_the_triple_and_skips_locales(monkeypatch):
    _cdx(
        monkeypatch,
        [
            "https://3m.wd1.myworkdayjobs.com/en-US/Search/job/x",
            "https://3m.wd1.myworkdayjobs.com/robots.txt",
            "https://barrywehmiller.wd1.myworkdayjobs.com/BWCareers",
        ],
    )
    got = discover.mine("workday", collection="CC-MAIN-TEST")
    trip = {(e["slug"], e["host"], e["site"]) for e in got}
    assert ("3m", "wd1", "Search") in trip
    assert ("barrywehmiller", "wd1", "BWCareers") in trip
    assert not any(e["site"].lower() in discover._NOT_SLUGS for e in got)


def test_probe_drops_boards_that_return_nothing(monkeypatch):
    monkeypatch.setattr(
        discover,
        "DEPTH_ALL",
        {"greenhouse": lambda slug, **kw: [{"t": 1}] if slug == "real" else []},
    )
    got = discover.probe(
        [{"ats": "greenhouse", "slug": "real"}, {"ats": "greenhouse", "slug": "dead"}]
    )
    assert [e["slug"] for e in got] == ["real"]
    assert got[0]["roles"] == 1


def test_probe_survives_a_fetcher_that_raises(monkeypatch):
    def boom(slug, **kw):
        raise RuntimeError("upstream schema change")

    monkeypatch.setattr(discover, "DEPTH_ALL", {"greenhouse": boom})
    assert discover.probe([{"ats": "greenhouse", "slug": "x"}]) == []


# ── identity verification: does the board BELONG to the company? ─────────────
def _owner(monkeypatch, mapping):
    """Stub the ATS board-owner lookup. None = the ATS gave us no answer."""
    monkeypatch.setattr(discover, "board_owner", lambda ats, slug: mapping.get(slug))


def test_identity_accepts_a_true_binding(monkeypatch):
    _owner(monkeypatch, {"cloudflare": "Cloudflare"})
    assert discover.verify_identity("greenhouse", "cloudflare", "Cloudflare")


def test_identity_ignores_legal_suffixes_on_either_side(monkeypatch):
    _owner(monkeypatch, {"leveltenenergy": "LevelTen Energy"})
    assert discover.verify_identity(
        "greenhouse", "leveltenenergy", "LevelTen Energy, Inc."
    )


def test_identity_rejects_a_real_board_owned_by_someone_else(monkeypatch):
    """REGRESSION: the whole reason this exists. jobs.lever.co/capital is a LIVE board
    with real jobs — owned by an unrelated company. A liveness probe cannot tell."""
    _owner(monkeypatch, {"capital": "Capital Group Companies"})
    assert not discover.verify_identity("greenhouse", "capital", "Capital One")


def test_identity_rejects_what_the_conservative_heuristic_let_through(monkeypatch):
    """Slug `remote` for the company 'Remote' is really 'General Assembly Remote Jobs'
    — a false positive the full-name-only rule accepted before identity checking."""
    _owner(monkeypatch, {"remote": "General Assembly Remote Jobs "})
    assert not discover.verify_identity("greenhouse", "remote", "Remote")


def test_identity_rejects_when_the_ats_could_answer_but_did_not(monkeypatch):
    """A Greenhouse board that won't confirm itself is not trusted — silence is not
    consent when the endpoint exists."""
    _owner(monkeypatch, {})
    assert not discover.verify_identity("greenhouse", "whatever", "Some Co")


def test_identity_passes_through_for_an_ats_with_no_endpoint(monkeypatch):
    """Ashby/Lever expose no owner; they must fall back to conservative variants
    rather than rejecting everything."""
    assert discover.verify_identity("ashby", "anything", "Some Co")
    assert discover.verify_identity("lever", "anything", "Some Co")


# ── variant gating ───────────────────────────────────────────────────────────
def test_aggressive_variant_is_opt_in_only():
    assert discover.name_variants("Capital One") == ["capitalone", "capital-one"]
    assert "capital" in discover.name_variants("Capital One", aggressive=True)


def test_from_names_only_guesses_first_word_where_identity_is_verifiable(monkeypatch):
    """The gating that makes the aggressive variant safe: it is generated ONLY for an
    ATS whose ownership we can check, so lever/ashby never see a bare first word."""
    seen = []

    def _capture(candidates, workers=8, require_identity=False, outcomes=None):
        seen.extend(candidates)
        assert require_identity, "from_names must demand identity verification"
        return []

    monkeypatch.setattr(discover, "probe", _capture)
    discover.from_names(["Capital One"], ats_list=["greenhouse", "lever", "ashby"])
    by_ats = {}
    for c in seen:
        by_ats.setdefault(c["ats"], set()).add(c["slug"])
    assert "capital" in by_ats["greenhouse"]
    assert "capital" not in by_ats.get("lever", set())
    assert "capital" not in by_ats.get("ashby", set())


def test_probe_enforces_identity_when_asked(monkeypatch):
    monkeypatch.setattr(
        discover, "DEPTH_ALL", {"greenhouse": lambda s, **k: [{"t": 1}]}
    )
    monkeypatch.setattr(
        discover, "verify_identity", lambda ats, slug, company: slug == "good"
    )
    cands = [
        {"ats": "greenhouse", "slug": "good", "name": "Good Co"},
        {"ats": "greenhouse", "slug": "bad", "name": "Good Co"},
    ]
    assert [e["slug"] for e in discover.probe(cands, require_identity=True)] == ["good"]
    # ...and without the flag, liveness alone still passes both (CDX mining path)
    assert len(discover.probe(cands, require_identity=False)) == 2


def test_probe_skips_identity_for_candidates_with_no_company_name(monkeypatch):
    """Pure CDX mining never had a company name to compare against."""
    monkeypatch.setattr(
        discover, "DEPTH_ALL", {"greenhouse": lambda s, **k: [{"t": 1}]}
    )
    monkeypatch.setattr(
        discover, "verify_identity", lambda *a: (_ for _ in ()).throw(AssertionError)
    )
    assert (
        len(discover.probe([{"ats": "greenhouse", "slug": "x"}], require_identity=True))
        == 1
    )


def test_probe_distinguishes_a_refusal_from_a_miss(monkeypatch):
    """A 403 board EXISTS and said no; a 404 board simply is not there. Collapsing
    both to 'nothing found' means retrying a deliberate refusal every night."""
    import urllib.error

    def fetch(slug, **kw):
        code = {"refuser": 403, "gone": 404, "throttled": 429}.get(slug)
        if code:
            raise urllib.error.HTTPError("u", code, "no", None, None)
        return [{"t": 1}]

    monkeypatch.setattr(discover, "DEPTH_ALL", {"greenhouse": fetch})
    cands = [
        {"ats": "greenhouse", "slug": s}
        for s in ("refuser", "gone", "throttled", "good")
    ]
    outcomes = []
    ok = discover.probe(cands, outcomes=outcomes)
    by = {o["slug"]: o["outcome"] for o in outcomes}
    assert [e["slug"] for e in ok] == ["good"]
    assert by["refuser"] == "refused"
    assert by["throttled"] == "throttled"  # transient — see the 429 test below
    assert by["gone"] == "missing"


def test_probe_reports_a_wrong_owner_distinctly(monkeypatch):
    monkeypatch.setattr(discover, "DEPTH_ALL", {"greenhouse": lambda s, **k: [{"t": 1}]})
    monkeypatch.setattr(discover, "verify_identity", lambda *a: False)
    outcomes = []
    discover.probe(
        [{"ats": "greenhouse", "slug": "capital", "name": "Capital One"}],
        require_identity=True,
        outcomes=outcomes,
    )
    assert outcomes[0]["outcome"] == "wrong-owner"


# ── Workday descriptions ─────────────────────────────────────────────────────
def test_workday_fetches_descriptions_when_enabled(monkeypatch):
    """A body-less job is unrankable (boosts match title+body) AND unreadable (the UI
    renders its snippet from the body), so descriptions are a precondition for Workday
    being worth harvesting at all."""
    fake, _ = _wd_pages(total=2, n_pages=1)
    monkeypatch.setattr(sources, "post_json", fake)
    monkeypatch.setattr(sources, "WORKDAY_FETCH_DETAILS", True)
    monkeypatch.setattr(
        sources,
        "get_json",
        lambda url: {
            "jobPostingInfo": {
                "jobDescription": "<p>Build things. Salary $120,000 - $150,000</p>",
                "startDate": "2026-07-01",
                "timeType": "Full time",
            }
        },
    )
    rows = sources.fetch_workday("acme", host="wd1", site="Careers")
    assert all(r["text"] for r in rows), "every row should carry a body"
    assert rows[0]["posted"] == "2026-07-01"  # detail's real date wins
    assert rows[0]["employment_type"] == "Full time"
    assert rows[0]["salary"], "salary should be parsed out of the fetched body"
    assert "_wd_path" not in rows[0], "the internal key must not leak into a row"


def test_workday_skips_descriptions_when_disabled(monkeypatch):
    fake, _ = _wd_pages(total=2, n_pages=1)
    monkeypatch.setattr(sources, "post_json", fake)
    monkeypatch.setattr(sources, "WORKDAY_FETCH_DETAILS", False)
    monkeypatch.setattr(
        sources, "get_json", lambda url: pytest.fail("must not fetch details")
    )
    rows = sources.fetch_workday("acme", host="wd1", site="Careers")
    assert rows and all(r["text"] == "" for r in rows)


def test_one_bad_detail_does_not_sink_the_employer(monkeypatch):
    fake, _ = _wd_pages(total=3, n_pages=1)
    monkeypatch.setattr(sources, "post_json", fake)
    monkeypatch.setattr(sources, "WORKDAY_FETCH_DETAILS", True)

    def flaky(url):
        if url.endswith("Role_1"):
            raise TimeoutError("slow")
        return {"jobPostingInfo": {"jobDescription": "ok"}}

    monkeypatch.setattr(sources, "get_json", flaky)
    rows = sources.fetch_workday("acme", host="wd1", site="Careers")
    assert len(rows) == 3, "a failed detail must not drop the role"
    assert sum(1 for r in rows if r["text"]) == 2


def test_throttling_is_never_terminal(monkeypatch):
    """REGRESSION + near-miss: 429 means slow down, not go away. Sweeping a few
    hundred Workday tenants reliably trips their rate limiter — measured 40/40 real
    triples returning 429, including one that had served 200 roles minutes earlier.
    Treating that as a permanent refusal would blacklist good employers wholesale."""
    import urllib.error

    def fetch(slug, **kw):
        raise urllib.error.HTTPError(
            "u", {"a": 429, "b": 403, "c": 404}[slug], "no", None, None
        )

    monkeypatch.setattr(discover, "DEPTH_ALL", {"greenhouse": fetch})
    outcomes = []
    discover.probe(
        [{"ats": "greenhouse", "slug": s} for s in ("a", "b", "c")], outcomes=outcomes
    )
    by = {o["slug"]: o["outcome"] for o in outcomes}
    assert by["a"] == "throttled", "429 must be its own, retryable outcome"
    assert by["b"] == "refused"
    assert by["c"] == "missing"


def test_ats_from_url_stops_at_the_query_string():
    """REGRESSION: greenhouse's embed form consumes the '?' itself
    (embed/job_app?for=SLUG&token=...), so a capture that excluded only /?# ran on
    through the query and yielded 'gemini&token=7743177&gh_jid=7743177'. Those probe
    as 404s, so a company just looked quietly unresolvable."""
    from job_radar.dedup import ats_from_url

    assert ats_from_url(
        "https://boards.greenhouse.io/embed/job_app?for=gemini&token=774&gh_jid=774"
    ) == ("greenhouse", "gemini")
    assert ats_from_url("https://jobs.lever.co/vaco?lever-source=x") == ("lever", "vaco")
    assert ats_from_url("https://jobs.ashbyhq.com/runway-ml/28e1") == (
        "ashby",
        "runway-ml",
    )
