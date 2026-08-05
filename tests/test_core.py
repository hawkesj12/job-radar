"""Core tests: config, deterministic scoring, dedup, the upsert store, and the
LLM no-op guarantee. Run: pytest"""

import json
import os
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
    seed,
    sources,
    shortlist,
    util,
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
            "department": hostile,
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


# ── source parser (the brittle provider-JSON → posting mapping) ───────────────
def test_greenhouse_parser_maps_fields(monkeypatch):
    fake = {
        "jobs": [
            {
                "title": "AI Engineer",
                "location": {"name": "Remote - US"},
                "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
                "updated_at": "2026-07-10T00:00:00Z",
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
    assert j["posted"] == "2026-07-10"
    assert j["department"] == "Engineering"
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
    assert added and "Acme" in wl.read_text()


def test_append_watchlist_refuses_template(tmp_path):
    ex = tmp_path / "watchlist.example.json"
    ex.write_text('{"companies": []}')
    added = funnel.append_watchlist(
        ex, [{"name": "Acme", "ats": "greenhouse", "slug": "acme"}]
    )
    assert added == [] and "Acme" not in ex.read_text()  # template untouched


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
    assert wl.read_text() == original, "engine must not have written the watchlist"


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
    assert [c["slug"] for c in _json.loads(wl.read_text())["companies"]] == ["newco"]


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
    r = engine._coerce({"title": "Engineer", "remote_type": "hybrid"})
    assert r["remote_type"] == "hybrid"
    assert r["remote"] is False  # derived: hybrid is not fully remote
    assert engine._coerce({"title": "Engineer"})["remote"] is None  # unknown != False


def test_absent_optional_text_is_none_not_empty_string():
    """`posted: ""` meant "we could not parse a date", which a consumer cannot tell
    from a job that has no date — the same lie as remote: False for unknown."""
    r = engine._coerce({"title": "Engineer", "posted": "", "salary": "", "text": ""})
    assert r["posted"] is None and r["salary"] is None and r["text"] is None
    assert r["title"] == "Engineer"  # required fields stay strings
