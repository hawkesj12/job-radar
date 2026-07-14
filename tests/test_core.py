"""Core tests: config, deterministic scoring, dedup, the upsert store, and the
LLM no-op guarantee. Run: pytest"""

from job_radar import config, dedup, llm, scoring, store


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
def test_dedup_key_ignores_seniority():
    a = {"company": "Acme Inc", "title": "Senior AI Engineer"}
    b = {"company": "Acme Inc", "title": "AI Engineer"}
    assert dedup.dedup_key(a) == dedup.dedup_key(b)


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
