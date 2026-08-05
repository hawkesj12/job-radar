"""Configuration for job-radar.

Every tunable that used to be a module constant lives here, loaded from one
YAML file the user edits. The engine reads the *active* Config (set once at
startup); tests pass an explicit Config. Defaults are generic tech-role
defaults -- run `job-radar init` to get a commented copy of them as YAML
(job_radar/data/job-radar.example.yaml) and make it yours.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path


def _env_int(name: str, default: int) -> int:
    """Read an int from the environment, falling back on a missing/garbage value
    so a bad env var can never crash the CLI at import time."""
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    """Read a boolean from the environment. `0`/`false`/`no`/`off` are false; anything
    else present is true. Same forgiving discipline as _env_int -- a bad value falls
    back rather than crashing the CLI at import."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off", "")


# ── generic defaults (NOT tuned to any one person) ──────────────────────────
DEFAULT_TITLE_QUERIES = [
    "AI Engineer",
    "Applied AI Engineer",
    "Machine Learning Engineer",
    "Forward Deployed Engineer",
    "LLM Engineer",
    "Software Engineer LLM",
]
DEFAULT_TITLE_SIGNAL = [
    "ai",
    "ml",
    "machine learning",
    "llm",
    "genai",
    "generative",
    "agent",
    "applied scientist",
    "forward deployed",
    "fde",
    "engineer",
    "developer",
    "architect",
    "automation",
    "solutions",
    "data scientist",
]
DEFAULT_TITLE_EXCLUDE = [
    "intern",
    "internship",
    "recruiter",
    "sales",
    "account executive",
    "marketing",
    "customer success",
    "support engineer",
    "people partner",
    "talent",
    "controller",
    "accountant",
    "office manager",
]
DEFAULT_TITLE_PENALTY = {
    "research scientist": 8,
    "quantitative researcher": 8,
    "machine learning researcher": 7,
    "ai researcher": 6,
    "member of technical staff": 6,
}
DEFAULT_AGENCY_PENALTY = {
    "staff augmentation": 15,
    "staffing agency": 15,
    "staffing firm": 15,
    "our client": 12,
    "multiple clients": 12,
    "talent solutions": 12,
    # "on behalf of" was here at 10 and has been REMOVED. It is generic English,
    # not an agency signal, and it appears verbatim in employers' own
    # anti-recruitment-fraud boilerplate: "we may partner with vetted recruiting
    # agencies who will identify themselves as working on behalf of <company>."
    # That paragraph is standard on first-party ATS boards, so this keyword fired
    # on the direct-from-employer roles this tool exists to surface -- measured on
    # 20 of 34 live Greenhouse postings.
    "consulting firm": 10,
    "consultancy": 10,
    "contract-to-hire": 10,
    "staffing": 8,
    "recruiter": 8,
    "c2c": 8,
}
DEFAULT_APPLIED_DOOR = [
    "forward deployed",
    "forward-deployed",
    "fde",
    "solutions engineer",
    "solutions architect",
    "applied ai",
    "deployment engineer",
    "customer engineer",
]
# Generic AI/tech fit weights. Add your own domain + location keywords in YAML.
DEFAULT_FIT_WEIGHTS = {
    "forward deployed": 4,
    "fde": 4,
    "applied ai": 4,
    "ai engineer": 3,
    "ml engineer": 3,
    "solutions engineer": 3,
    "solutions architect": 3,
    "automation engineer": 3,
    "platform engineer": 2,
    "applied scientist": 2,
    "developer": 1,
    "agentic": 4,
    "multi-agent": 4,
    "agent": 3,
    "orchestration": 3,
    "rag": 4,
    "retrieval-augmented": 4,
    "retrieval": 3,
    "knowledge base": 3,
    "llm": 3,
    "large language model": 3,
    "genai": 3,
    "generative ai": 3,
    "machine learning": 2,
    "foundation model": 2,
    "ai-first": 3,
    "ai-native": 3,
    "ai-powered": 2,
    "ai": 1,
    "embeddings": 2,
    "vector": 2,
    "evals": 2,
    "evaluation": 1,
    "prompt engineering": 2,
    "fine-tuning": 1,
    "claude": 2,
    "anthropic": 2,
    "model context protocol": 3,
    "mcp": 2,
    "openai": 1,
    "langchain": 1,
    "python": 1,
    "typescript": 1,
    "react": 1,
    "founding": 5,
    "founding engineer": 5,
    "first ai engineer": 5,
    "greenfield": 3,
    "0 to 1": 3,
    "zero to one": 3,
    "own the ai": 4,
    "internal tooling": 4,
    "internal tools": 4,
    "remote": 4,
    "work from anywhere": 4,
    "remotely": 3,
    "senior": 1,
    "lead": 1,
    "staff": 1,
}
DEFAULT_NON_US = [
    "india",
    "australia",
    "united kingdom",
    "england",
    "ireland",
    "germany",
    "denmark",
    "sweden",
    "finland",
    "norway",
    "singapore",
    "canada",
    "poland",
    "spain",
    "portugal",
    "france",
    "netherlands",
    "japan",
    "brazil",
    "mexico",
    "colombia",
    "argentina",
    "emea",
    "apac",
    "latam",
    "europe",
    "(eu)",
    "romania",
    "bulgaria",
    "czech",
    "ukraine",
    "israel",
    "dubai",
    "uae",
]
# NOTE: there is deliberately no ALL_DEPTH / ALL_BREADTH name list here. This module
# used to carry copies of the adapter names purely to seed the defaults below, which
# made sources.py's registries and this file two sources of truth that had to be kept
# in sync by hand — and they silently drifted the first time an adapter was added (a
# new fetcher registered fine, then got filtered straight back out by the stale copy).
# The registries in sources.py are now the ONLY list. See depth_sources below.


@dataclass
class HarvestDepth:
    """How deep each adapter walks its source, in ONE place.

    These lived as nine module-level constants in `sources.py`, each reading its own
    environment variable at IMPORT time -- which meant a YAML file could not set them
    at all (the module is imported before any config is loaded), and the only way to
    tune a harvest was to know nine undocumented variable names. The env vars still
    work and still supply the defaults; they are now the fallback for one named block
    rather than the only interface.

    Every value is a CEILING, not a target. The etiquette rule this encodes: there is
    no unbounded walk against anyone's API anywhere in this package, and every bound
    is visible here instead of buried in a URL.
    """

    # depth adapters
    smartrecruiters_max_pages: int = field(
        default_factory=lambda: _env_int("SMARTRECRUITERS_MAX_PAGES", 10)
    )
    workday_max_pages: int = field(
        default_factory=lambda: _env_int("WORKDAY_MAX_PAGES", 25)
    )
    # The two adapters that buy job descriptions ONE REQUEST PER ROLE. Turning these
    # off is the single biggest lever on a harvest's request count; it costs the body,
    # which is the entire input to the fit score.
    workday_fetch_details: bool = field(
        default_factory=lambda: _env_bool("WORKDAY_FETCH_DETAILS", True)
    )
    workday_detail_workers: int = field(
        default_factory=lambda: _env_int("WORKDAY_DETAIL_WORKERS", 8)
    )
    rippling_fetch_details: bool = field(
        default_factory=lambda: _env_bool("RIPPLING_FETCH_DETAILS", True)
    )
    # breadth adapters
    himalayas_max_pages: int = field(
        default_factory=lambda: _env_int("HIMALAYAS_MAX_PAGES", 10)
    )
    himalayas_browse_pages: int = field(
        default_factory=lambda: _env_int("HIMALAYAS_BROWSE_PAGES", 50)
    )
    themuse_max_pages: int = field(
        default_factory=lambda: _env_int("THEMUSE_MAX_PAGES", 5)
    )
    hn_threads: int = field(default_factory=lambda: _env_int("HN_THREADS", 2))


@dataclass
class LLMConfig:
    enabled: bool = False
    provider: str = "anthropic"  # "anthropic" | "openai" (OpenAI-compatible)
    model: str = "claude-haiku-4-5-20251001"
    api_key_env: str = "ANTHROPIC_API_KEY"
    base_url: str = ""  # override for OpenAI-compatible endpoints
    rerank_top_n: int = 25


@dataclass
class Config:
    # profile
    title_queries: list = field(default_factory=lambda: list(DEFAULT_TITLE_QUERIES))
    title_signal: list = field(default_factory=lambda: list(DEFAULT_TITLE_SIGNAL))
    # scoring
    fit_weights: dict = field(default_factory=lambda: dict(DEFAULT_FIT_WEIGHTS))
    title_penalty: dict = field(default_factory=lambda: dict(DEFAULT_TITLE_PENALTY))
    agency_penalty: dict = field(default_factory=lambda: dict(DEFAULT_AGENCY_PENALTY))
    applied_door: list = field(default_factory=lambda: list(DEFAULT_APPLIED_DOOR))
    frontier_penalty: int = 10
    local_bonus: int = 10
    score_len_b: float = 0.75
    # NOT term-frequency saturation, despite the name and despite what this comment
    # and the README both used to claim. `_present` returns each keyword AT MOST
    # ONCE, so tf is pinned at 1 and there is no term frequency left to saturate.
    # Measured on this codebase: repeating a keyword 1x / 5x / 50x / 500x scores
    # 26 / 26 / 25 / 22 -- never rising, and mildly falling as the repetition
    # lengthens the document. What score_k1 actually does is set the GAIN on length
    # normalization: 0.1 / 1.2 / 100 gives 24 / 36 / 64 on the same posting. A real
    # knob, honestly described.
    #
    # Keeping presence-based scoring is a deliberate choice, not an oversight: it is
    # precisely why a keyword-stuffed posting cannot buy rank. The claim about it
    # was the only thing wrong. 1.2 stays because it is the literature default and
    # measured best on a live board (aggregator-vs-employer wins 16/20 -> 7/20).
    score_k1: float = 1.2
    # The "normal" JD length the BM25 length penalty divides by. Was 400, which is
    # roughly a job-board SUMMARY -- but this tool reads full ATS descriptions, and
    # the real median measured across a live Greenhouse board is ~1590 tokens, 4x
    # that. Every thorough description was therefore treated as abnormally long and
    # had its keyword score divided by ~3.2, which inverted the intent: BM25 length
    # normalization exists to stop an UNUSUALLY long document accruing score by
    # sheer size, not to penalize the standard length of the corpus. Employers write
    # the longest descriptions, so the setting penalized precisely the
    # direct-from-employer roles this tool exists to surface -- on one real board,
    # 0 of 20 relevant remote roles cleared min_score; at 1600, 7 of 20 do.
    avg_jd_tokens: int = 1600
    blob_score_cap: int = 60
    # Cap the title-keyword double-count too, so a keyword-stuffed TITLE can't
    # outrank a thorough JD (the body already caps at blob_score_cap). Keyword
    # scoring favors recall by design; the optional LLM re-rank is the precision layer.
    title_score_cap: int = 12
    # Cap the agency penalty the way the two scores above are capped. It was the
    # only uncapped component, and the only one that goes NEGATIVE -- so a long,
    # thorough job description had more chances to accumulate penalty while its
    # body score was being length-normalized DOWN. The result was that
    # direct-from-employer roles, whose descriptions are the longest, scored below
    # the surfacing threshold: measured on a clean install, the depth lane
    # harvested 436 roles and surfaced 1. A staffing post announces itself in a
    # phrase or two; it does not need an unbounded budget to do so.
    agency_penalty_cap: int = 15
    tier_strong: int = 30
    tier_look: int = 22
    fuzzy_title_threshold: int = 90
    # Secondary gate on the fuzzy de-dup. token_set_ratio alone returns 100 for a
    # SUBSET ("ai engineer" ⊂ "ai engineer, payments"), which would wrongly merge
    # two distinct openings at one company. token_sort_ratio penalizes the length
    # gap, so a reorder/punctuation retitle still matches but a subset-with-an-extra
    # -token does not. Both gates must clear for a fuzzy merge.
    fuzzy_title_sort_floor: int = 82
    # filters
    remote_only: bool = True
    location: str = (
        "remote"  # "remote" or a place ("Louisville, KY") for general sources
    )
    radius_miles: int = 0  # 0 = API default; >0 sets a search radius around `location`
    exclude_titles: list = field(default_factory=lambda: list(DEFAULT_TITLE_EXCLUDE))
    exclude_locations: list = field(default_factory=lambda: list(DEFAULT_NON_US))
    max_age_days: int = 60
    stale_after_days: int = 30
    min_score: int = 22
    # sources
    # None = "every adapter this build registers" (resolved against sources.DEPTH_ALL /
    # BREADTH_ALL at use time). Expressing the default as ABSENCE rather than a copied
    # list is what removes the duplicate registry: adding a fetcher to sources.py is
    # now sufficient to enable it, and there is no second list that can go stale.
    # A YAML `sources.ats` / `sources.boards` key still sets an explicit subset.
    depth_sources: list | None = None
    breadth_sources: list | None = None
    adzuna_app_id_env: str = "ADZUNA_APP_ID"
    adzuna_app_key_env: str = "ADZUNA_APP_KEY"
    # Google for Jobs via SerpApi — the meta-aggregator that indexes company career
    # sites + enterprise ATSs (Workday, iCIMS) Google crawled, queryable by title +
    # location like Adzuna/USAJOBS (no per-tenant polling). Metered: SerpApi's free
    # tier is 250 searches/mo, and each PAGE is one search, so default to a single
    # page (~10 roles/query) and raise GOOGLE_JOBS_PAGES only when the quota allows.
    serpapi_key_env: str = "SERPAPI_KEY"
    # THE QUOTA GUARD. google_jobs spends `pages x title_queries` SerpApi searches per
    # run -- with the shipped six title queries and one page that is 6/run, i.e. 180
    # of a 250/month free tier at daily cadence (72%). One extra title query or one
    # extra page overruns it mid-month, and SerpApi reports exhaustion as a JSON
    # `error` rather than an HTTP failure, so the adapter degrades into a printed
    # notice and the shortlist just gets quieter. That is the failure this prevents.
    #
    # `serpapi_reserve` is left UNSPENT so an overrun cannot consume the last of the
    # month; `serpapi_max_searches_per_run` bounds a single run regardless of what the
    # account says. Set the reserve to 0 to spend the quota to the floor.
    serpapi_max_searches_per_run: int = field(
        default_factory=lambda: _env_int("SERPAPI_MAX_SEARCHES_PER_RUN", 12)
    )
    serpapi_reserve: int = field(
        default_factory=lambda: _env_int("SERPAPI_RESERVE", 25)
    )
    google_jobs_pages: int = field(
        default_factory=lambda: _env_int("GOOGLE_JOBS_PAGES", 1)
    )
    # Pages to pull per query. Adzuna caps a page at 50 results, so N pages ≈ N×50
    # jobs/query before dedup; a selective filter (e.g. remote-only) then carves it
    # down, so fetch generously. Env-overridable for prod tuning without a redeploy.
    adzuna_pages: int = field(default_factory=lambda: _env_int("ADZUNA_PAGES", 3))
    # USAJOBS allows up to 500 results/page (default 25) — one big page covers most
    # federal queries, so a single call is both complete and frugal.
    usajobs_results_per_page: int = field(
        default_factory=lambda: _env_int("USAJOBS_RESULTS_PER_PAGE", 500)
    )
    # USAJOBS pages with `&Page=N`, and the adapter never sent it -- so any keyword
    # with more than one page of matches was silently truncated. Measured in
    # catalog/usajobs.md: "medical assistant" 736 and "registered nurse" 620 against a
    # 500-row page. 3 pages = 1,500 rows/query, bounded because this is a federal API
    # taking the largest page size in the codebase.
    usajobs_max_pages: int = field(
        default_factory=lambda: _env_int("USAJOBS_MAX_PAGES", 3)
    )
    funnel_auto_grow: bool = True
    funnel_max_new_per_run: int = 25
    # A budget on PROBES ATTEMPTED, which max_new_per_run is not. That one counts
    # successes, and a dead slug is not a success -- so it never incremented, the
    # loop never hit its break, and every candidate got probed serially. Measured:
    # 150 dead candidates = 150 requests, ~60 seconds, 0 companies added, on every
    # scan, with auto_grow on by default. Discovery is incremental and runs each
    # time, so a bounded budget defers work rather than losing it.
    funnel_max_probes_per_run: int = 50
    # http
    timeout: int = 25
    user_agent: str = "job-radar/1.0 (https://github.com/hawkesj12/job-radar)"
    # ai
    llm: LLMConfig = field(default_factory=LLMConfig)
    # harvest depth — see HarvestDepth. YAML: `sources.harvest_depth.*`
    harvest_depth: HarvestDepth = field(default_factory=HarvestDepth)

    def env(self, key: str) -> str:
        """Read a credential from the environment.

        STRIPPED on the way out, and that is load-bearing rather than tidiness.
        Keys arrive with trailing newlines constantly -- a .env file, `$(cat key)`,
        a CI secret, CRLF on Windows -- and a newline in a credential is not inert:
        it makes http.client raise `ValueError: Invalid header value b'sk-ant-...'`
        with the whole key in the message, and it corrupts the query string for the
        sources that pass their key as a URL parameter. This is the single
        chokepoint every credential passes through (Adzuna, USAJOBS, SerpApi, LLM),
        so stripping here fixes all of them at once.
        """
        return (os.environ.get(key, "") or "").strip()


def without_redirects(cfg: Config) -> Config:
    """Return `cfg` with the request-redirecting keys reset to their defaults.

    Storing credentials in environment variables only protects you if something
    else decides where they are sent. These keys decide exactly that: `base_url`
    picks the host a request goes to, and the `*_key_env` names pick which
    environment variable rides along with it. A config file that sets both can make
    job-radar POST your ANTHROPIC_API_KEY to a server of its choosing -- no network
    attacker needed, just a `job-radar.yaml` you did not write in a directory you
    happened to run from.

    So the CLI applies this to any config it DISCOVERED rather than one you named
    with --config. Everything else in the file still applies; only the redirect
    keys revert, and the caller is told.
    """
    d = Config()
    # Report the key NAMES that were ignored, never their values. Two reasons, and
    # the first one is not hypothetical: `api_key_env` is free text, and writing the
    # key itself there instead of the variable's NAME is a common mix-up
    # (`api_key_env: sk-ant-...`), so echoing the value would print a live secret --
    # exactly the bug this release fixed in llm.py. The second: these values come
    # from a file we have just decided not to trust, and untrusted text printed to a
    # terminal carries ANSI escapes that can rewrite what the user sees.
    changed = []
    if cfg.llm.base_url != d.llm.base_url:
        changed.append("llm.base_url")
    if cfg.llm.api_key_env != d.llm.api_key_env:
        changed.append("llm.api_key_env")
    if cfg.serpapi_key_env != d.serpapi_key_env:
        changed.append("sources.google_jobs.key_env")
    if cfg.adzuna_app_key_env != d.adzuna_app_key_env:
        changed.append("sources.adzuna.app_key_env")
    if not changed:
        return cfg
    print(
        f"note: ignoring {', '.join(changed)} from a job-radar.yaml found in this "
        "directory rather than passed with --config. Those keys choose where a "
        "request goes and which secret it carries. Re-run with an explicit "
        "--config if you meant it.",
        file=sys.stderr,
    )
    return replace(
        cfg,
        llm=replace(cfg.llm, base_url=d.llm.base_url, api_key_env=d.llm.api_key_env),
        serpapi_key_env=d.serpapi_key_env,
        adzuna_app_key_env=d.adzuna_app_key_env,
    )


def load_config(path: str | os.PathLike | None) -> Config:
    """Load a YAML config, merging over the generic defaults. Missing file /
    missing keys are fine -- you get defaults for anything unset."""
    cfg = Config()
    if not path:
        return cfg
    p = Path(path)
    if not p.exists():
        return cfg
    import sys

    import yaml

    try:
        doc = yaml.safe_load(p.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        print(f"  config parse error in {p} — using defaults ({e})", file=sys.stderr)
        return cfg
    if not isinstance(doc, dict):  # empty file, or a scalar/list top level
        return cfg
    # `or {}` guards a present-but-empty section (`profile:` with no body -> None)
    prof, scor = doc.get("profile") or {}, doc.get("scoring") or {}
    filt, srcs = doc.get("filters") or {}, doc.get("sources") or {}
    llm = doc.get("llm") or {}

    def take(section: dict, key: str, attr: str):
        if section.get(key) is not None:
            setattr(cfg, attr, section[key])

    take(prof, "title_queries", "title_queries")
    take(prof, "signal_titles", "title_signal")
    for k, a in [
        ("fit_weights", "fit_weights"),
        ("title_penalties", "title_penalty"),
        ("agency_penalties", "agency_penalty"),
        ("local_bonus", "local_bonus"),
        ("length_norm_b", "score_len_b"),
        ("score_k1", "score_k1"),
        ("avg_jd_tokens", "avg_jd_tokens"),
        ("body_cap", "blob_score_cap"),
        ("title_cap", "title_score_cap"),
    ]:
        take(scor, k, a)
    if isinstance(scor.get("tiers"), dict):
        cfg.tier_strong = scor["tiers"].get("strong", cfg.tier_strong)
        cfg.tier_look = scor["tiers"].get("worth_a_look", cfg.tier_look)
    for k, a in [
        ("remote_only", "remote_only"),
        ("location", "location"),
        ("radius_miles", "radius_miles"),
        ("max_age_days", "max_age_days"),
        ("stale_after_days", "stale_after_days"),
        ("min_score", "min_score"),
        ("exclude_titles", "exclude_titles"),
        ("exclude_locations", "exclude_locations"),
    ]:
        take(filt, k, a)
    for k, a in [
        ("ats", "depth_sources"),
        ("boards", "breadth_sources"),
    ]:
        take(srcs, k, a)
    if isinstance(srcs.get("adzuna"), dict):
        cfg.adzuna_app_id_env = srcs["adzuna"].get("app_id_env", cfg.adzuna_app_id_env)
        cfg.adzuna_app_key_env = srcs["adzuna"].get(
            "app_key_env", cfg.adzuna_app_key_env
        )
        cfg.adzuna_pages = srcs["adzuna"].get("pages", cfg.adzuna_pages)
    if isinstance(srcs.get("google_jobs"), dict):
        cfg.serpapi_key_env = srcs["google_jobs"].get("key_env", cfg.serpapi_key_env)
        cfg.google_jobs_pages = srcs["google_jobs"].get("pages", cfg.google_jobs_pages)
        cfg.serpapi_max_searches_per_run = srcs["google_jobs"].get(
            "max_searches_per_run", cfg.serpapi_max_searches_per_run
        )
        cfg.serpapi_reserve = srcs["google_jobs"].get("reserve", cfg.serpapi_reserve)
    if isinstance(srcs.get("harvest_depth"), dict):
        # Only keys that exist on the dataclass, so a typo in YAML cannot invent a
        # setting that silently does nothing -- it is reported instead.
        for k, v in srcs["harvest_depth"].items():
            if hasattr(cfg.harvest_depth, k):
                setattr(cfg.harvest_depth, k, v)
            else:
                # Warn and continue, matching how this loader treats every other bad
                # input: a malformed config must not crash the CLI. Silence would be
                # worse than either -- a typo'd depth key would read as "that setting
                # had no effect", which is indistinguishable from a quiet market.
                print(
                    f"  config: unknown sources.harvest_depth key {k!r} — ignored "
                    f"(valid: {', '.join(sorted(vars(cfg.harvest_depth)))})",
                    file=sys.stderr,
                )
    if isinstance(srcs.get("usajobs"), dict):
        cfg.usajobs_results_per_page = srcs["usajobs"].get(
            "results_per_page", cfg.usajobs_results_per_page
        )
        cfg.usajobs_max_pages = srcs["usajobs"].get("max_pages", cfg.usajobs_max_pages)
    if isinstance(srcs.get("funnel"), dict):
        cfg.funnel_auto_grow = srcs["funnel"].get("auto_grow", cfg.funnel_auto_grow)
        cfg.funnel_max_probes_per_run = srcs["funnel"].get(
            "max_probes_per_run", cfg.funnel_max_probes_per_run
        )
        cfg.funnel_max_new_per_run = srcs["funnel"].get(
            "max_new_per_run", cfg.funnel_max_new_per_run
        )
    if llm:
        cfg.llm = replace(
            cfg.llm,
            **{k: v for k, v in llm.items() if k in LLMConfig.__dataclass_fields__},
        )
    return cfg


# ── active-config accessor (set once by the CLI; tests pass explicit cfg) ────
_ACTIVE: Config = Config()


def set_active(cfg: Config) -> None:
    global _ACTIVE
    _ACTIVE = cfg


def active() -> Config:
    return _ACTIVE
