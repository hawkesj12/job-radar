"""Core tests: config, deterministic scoring, dedup, the upsert store, and the
LLM no-op guarantee. Run: pytest"""

import json
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
    assert dedup.fuzzy_title_match(
        n("Senior AI Engineer"), n("AI Engineer (Senior)"), c
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
        body = min(raw / norm if norm > 0 else raw, c.blob_score_cap)
        tl = p.get("title", "").lower()
        body += min(
            sum(w for kw, w in fw.items() if util.has(kw, tl)), c.title_score_cap
        )
        body -= sum(w for kw, w in c.title_penalty.items() if util.has(kw, tl))
        ab = f"{p.get('company', '')} {p.get('text', '')}".lower()
        body -= sum(w for kw, w in c.agency_penalty.items() if util.has(kw, ab))
        sig = ", ".join(kw for _, kw in sorted(bh, reverse=True)[:7])
        return round(body), sig

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
