"""Core tests: config, deterministic scoring, dedup, the upsert store, and the
LLM no-op guarantee. Run: pytest"""

import types

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
    store,
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
    assert "greenhouse" in c.depth_sources and "adzuna" in c.breadth_sources
    assert c.llm.enabled is False


def test_config_override(tmp_path):
    p = tmp_path / "cfg.yaml"
    p.write_text("filters:\n  max_age_days: 14\n  min_score: 40\n")
    c = config.load_config(p)
    assert c.max_age_days == 14 and c.min_score == 40
    assert c.fit_weights  # untouched sections keep defaults


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
    merged = store.upsert(csvp, [p], today="2026-07-10")
    assert merged[0]["status"] == "new" and merged[0]["_is_new"] is True
    rid = merged[0]["id"]
    # user applies
    assert store.mark_status(csvp, rid, "applied") is True
    # run 2: same role reappears with a fresh score
    p2 = _post("Acme", "AI Engineer", 55, "https://x/1")
    merged2 = store.upsert(csvp, [p2], today="2026-07-12")
    row = next(r for r in merged2 if r["id"] == rid)
    assert row["status"] == "applied"  # status PRESERVED
    assert row["first_seen"] == "2026-07-10"  # first_seen PRESERVED
    assert int(row["score"]) == 55  # score refreshed
    assert row["_is_new"] is False


def test_applied_is_sticky_when_role_leaves_feed(tmp_path):
    csvp = tmp_path / "shortlist.csv"
    merged = store.upsert(
        csvp, [_post("Acme", "AI Engineer", 40, "https://x/1")], today="2026-07-10"
    )
    store.mark_status(csvp, merged[0]["id"], "applied")
    # next run: the role is gone from the market (empty postings)
    merged2 = store.upsert(csvp, [], today="2026-07-12")
    assert any(r["status"] == "applied" for r in merged2)  # history persists


def test_surface_excludes_applied_and_low_score(tmp_path):
    c = _cfg()
    csvp = tmp_path / "shortlist.csv"
    ps = [
        _post("A", "AI Engineer", 40, "u1"),
        _post("B", "AI Engineer", 5, "u2"),
        _post("C", "AI Engineer", 33, "u3"),
    ]
    merged = store.upsert(csvp, ps, today="2026-07-12")
    store.mark_status(
        csvp, next(r["id"] for r in merged if r["company"] == "A"), "applied"
    )
    merged = store.load_all(csvp)
    shown = store.surface(merged, c)
    names = {r["company"] for r in shown}
    assert names == {"C"}  # A applied (excluded), B below min_score (excluded)


# ── the AI layer's no-op guarantee ───────────────────────────────────────────
def test_llm_is_noop_when_disabled():
    c = _cfg()
    assert c.llm.enabled is False
    items = [{"key": "k", "title": "AI Engineer", "company": "Acme", "text": "..."}]
    assert llm.rerank(items, c) == {}  # never calls out when disabled


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
    merged = store.upsert(
        csvp, [_post("A", "AI Engineer", 90, "u1")], today="2026-07-12"
    )
    store.mark_status(csvp, merged[0]["id"], "rejected")
    assert store.surface(store.load_all(csvp), c) == []  # rejected never resurfaces


def test_csv_formula_injection_neutralized(tmp_path):
    csvp = tmp_path / "s.csv"
    store.upsert(
        csvp, [_post("=cmd|'/c calc'!A1", "AI Engineer", 40, "u1")], today="2026-07-12"
    )
    row = store.load_all(csvp)[0]
    assert row["company"].startswith("'=")  # prefixed, inert in a spreadsheet


def test_surface_tolerates_dirty_hand_edits(tmp_path):
    c = _cfg()
    csvp = tmp_path / "s.csv"
    store.write_all(
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
    store.surface(store.load_all(csvp), c)  # must not raise


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
    merged = store.upsert(
        csvp, [_post("Acme", "AI Engineer", 40, "https://x/1")], today="2026-07-10"
    )
    store.mark_status(csvp, merged[0]["id"], "applied")
    # next run: the recruiter re-titled it (new dedup_key) but the URL is stable
    p2 = _post("Acme", "AI Engineer, Platform Team", 55, "https://x/1")
    merged2 = store.upsert(csvp, [p2], today="2026-07-12")
    applied = [r for r in merged2 if r["status"] == "applied"]
    assert len(applied) == 1  # one row, status preserved (no duplicate 'new' row)
    assert applied[0]["first_seen"] == "2026-07-10"


# ── panel-review punch-list fixes ────────────────────────────────────────────
def test_store_roundtrips_unicode(tmp_path):
    """Non-cp1252 titles (CJK/emoji) survive write+read — the Windows
    UnicodeEncodeError guard (encoding='utf-8' on every file open)."""
    csvp = tmp_path / "s.csv"
    store.upsert(
        csvp, [_post("Acme", "Senior AI Engineer 🚀 — 东京", 40, "https://x/1")],
        today="2026-07-10",
    )
    rows = store.load_all(csvp)
    assert any("🚀" in r["title"] and "东京" in r["title"] for r in rows)


def test_total_outage_preserves_store(tmp_path, monkeypatch):
    """Every source failing (0 rows + errors) must NOT overwrite the store (which
    would wipe 'new' roles / reset first_seen) and must exit nonzero."""
    c = _cfg()
    csvp = tmp_path / "shortlist.csv"
    wl = tmp_path / "watchlist.json"
    wl.write_text('{"companies": []}', encoding="utf-8")
    merged = store.upsert(
        csvp,
        [
            _post("Acme", "AI Engineer", 40, "https://x/1"),
            _post("Beta", "AI Engineer", 45, "https://x/2"),
        ],
        today="2026-07-10",
    )
    store.mark_status(csvp, merged[0]["id"], "applied")
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
        w = _csv.DictWriter(f, fieldnames=store.COLUMNS, extrasaction="ignore")
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
    assert dedup.fuzzy_title_match(n("Senior AI Engineer"), n("AI Engineer (Senior)"), c)


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
