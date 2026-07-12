"""Configuration for job-radar.

Every tunable that used to be a module constant lives here, loaded from one
YAML file the user edits. The engine reads the *active* Config (set once at
startup); tests pass an explicit Config. Defaults are generic tech-role
defaults -- copy job-radar.example.yaml and make it yours.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path

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
    "on behalf of": 10,
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
ALL_DEPTH = ["greenhouse", "lever", "ashby", "smartrecruiters", "workable"]
ALL_BREADTH = [
    "remotive",
    "jobicy",
    "arbeitnow",
    "remoteok",
    "himalayas",
    "adzuna",
    "usajobs",
    "hn",
    "braintrust",
    "techtree",
]


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
    avg_jd_tokens: int = 400
    blob_score_cap: int = 60
    tier_strong: int = 30
    tier_look: int = 22
    fuzzy_title_threshold: int = 90
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
    depth_sources: list = field(default_factory=lambda: list(ALL_DEPTH))
    breadth_sources: list = field(default_factory=lambda: list(ALL_BREADTH))
    scraper_sources: list = field(default_factory=list)  # opt-in, off by default
    adzuna_app_id_env: str = "ADZUNA_APP_ID"
    adzuna_app_key_env: str = "ADZUNA_APP_KEY"
    funnel_auto_grow: bool = True
    funnel_max_new_per_run: int = 25
    # http
    timeout: int = 25
    user_agent: str = "job-radar/1.0 (https://github.com/hawkesj12/job-radar)"
    # ai
    llm: LLMConfig = field(default_factory=LLMConfig)

    def env(self, key: str) -> str:
        return os.environ.get(key, "") or ""


def load_config(path: str | os.PathLike | None) -> Config:
    """Load a YAML config, merging over the generic defaults. Missing file /
    missing keys are fine -- you get defaults for anything unset."""
    cfg = Config()
    if not path:
        return cfg
    p = Path(path)
    if not p.exists():
        return cfg
    import yaml

    doc = yaml.safe_load(p.read_text())
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
        ("avg_jd_tokens", "avg_jd_tokens"),
        ("body_cap", "blob_score_cap"),
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
        ("scrapers", "scraper_sources"),
    ]:
        take(srcs, k, a)
    if isinstance(srcs.get("adzuna"), dict):
        cfg.adzuna_app_id_env = srcs["adzuna"].get("app_id_env", cfg.adzuna_app_id_env)
        cfg.adzuna_app_key_env = srcs["adzuna"].get(
            "app_key_env", cfg.adzuna_app_key_env
        )
    if isinstance(srcs.get("funnel"), dict):
        cfg.funnel_auto_grow = srcs["funnel"].get("auto_grow", cfg.funnel_auto_grow)
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
