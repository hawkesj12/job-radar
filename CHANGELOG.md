# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.1] - 2026-07-22

### Fixed

- `dedup.ats_from_url` stopped at `/ ? #` but not `&`. Greenhouse's embed form puts
  the slug inside the query string (`embed/job_app?for=SLUG&token=...`), so the
  pattern consumed the `?` itself and the capture ran on through, yielding slugs like
  `gemini&token=7743177&gh_jid=7743177`. Harmless on its own — a malformed slug just
  probes as a 404 — but it corrupts any consumer that compares parsed slugs against
  known boards, which is exactly what apply-URL ownership auditing does.

## [0.3.0] - 2026-07-22

### Added

- **Workday adapter** (`fetch_workday`) over the public CxS endpoint — the first
  enterprise ATS in the set, reaching the manufacturers, insurers, municipalities and
  national labs that never appear on the startup boards. Needs a three-part key
  (tenant, `wdN` host, site slug) rather than a slug, so `DEPTH_EXTRA_FIELDS` lets an
  adapter declare the extra watchlist fields it requires. Job descriptions are fetched
  from the per-job detail endpoint behind `WORKDAY_FETCH_DETAILS` (default on) —
  without a body a job cannot be ranked or read, so this is a precondition rather than
  an enhancement. Budget one request per role for it, on top of one per 20 for the
  listing. Two design notes worth knowing before relying on it: Workday reports
  `total` only on the first page (it is latched once — re-reading it per page ends the
  loop after two pages), and each employer is silently truncated at
  `WORKDAY_MAX_PAGES` × 20 = 200 roles.
- **`job_radar.discover`** — bulk company discovery. Mines the Common Crawl CDX index
  by ATS URL pattern to recover slugs (and Workday's full triple) in bulk instead of
  one company at a time, and resolves a company NAME to a slug for employers the index
  never saw. Every candidate is verified by a live probe before it is trusted.
- **Board-ownership verification** (`verify_identity`). A probe proves a board is
  LIVE; it cannot prove the board is the RIGHT one. `jobs.lever.co/capital` is a real
  board with real jobs owned by someone other than Capital One. Greenhouse reports who
  owns a board, so we now ask, and a mismatch is rejected.
- `util.post_json` for POST-only read APIs.

### Changed

- **`engine.harvest` accepts a company array** (`companies=[...]`) as well as a
  watchlist path, so a caller that keeps its universe somewhere other than a JSON file
  can drive the engine.
- **The engine no longer writes files.** Discovered companies are RETURNED instead of
  being appended to the caller's watchlist; persistence belongs to whoever owns the
  universe. `cli.py` does it for the standalone CLI, so its behaviour is unchanged.
- Source defaults are now expressed as absence rather than a copied list of adapter
  names. `config.ALL_DEPTH`/`ALL_BREADTH` are gone: they duplicated the registries in
  `sources.py` and had already drifted, silently disabling a newly added adapter.
- Rate-limiting (429) is distinguished from a hard refusal (401/403) and a miss (404).
  Conflating them let a transient throttle be recorded as permanent.

### BREAKING

- `job_radar/store.py` is renamed to `job_radar/shortlist.py`. It is the CLI's
  shortlist.csv store and was imported only by `cli.py`, but the name collided with
  the store module of the app built on this library. Anyone importing
  `job_radar.store` must update the import.

## [Unreleased]

### Added

- `--verbose` (print which sources failed and why) and `--strict` (exit nonzero if
  any source errored, for scheduled runs / CI) flags on `scan`.
- Quality-tier tags (`★ strong` / `◆ worth a look`) on each surfaced role, driven by
  the `scoring.tiers` config (previously loaded but unused).
- `seed` gained its own `--max` flag (default 500) instead of reusing the print
  `--limit` (which capped it at 25).

### Changed

- Keyword scoring is faster (tokenize-once + set membership for single-word keywords,
  a first-token prefilter for multi-word ones); output is byte-identical, verified by
  a differential-equivalence test over 20,000 randomized postings.
- Starter watchlist repaired: fixed five dead Greenhouse slugs (→ Ashby / corrected),
  added Harvey / Sierra / LangChain / ElevenLabs — a clean first run with 0 feed errors.
- README now describes what a fresh clone actually does (a starter watchlist + ten
  aggregator feeds, growable via `seed`) instead of overstating out-of-box coverage.
- Store writes use a unique temp file (`mkstemp`) so overlapping runs can't collide.

### Fixed

- **Windows:** every file open and stdout/stderr are UTF-8, so non-ASCII job titles
  and the `✓ ⚠ ↳ ★` glyphs no longer crash a run (`UnicodeEncodeError`) on a cp1252
  console or a redirected/scheduled-task stdout.
- A total source outage no longer wipes the shortlist / resets `first_seen`; the prior
  file is kept and the run exits nonzero.
- A corrupt `watchlist.json` now surfaces a loud error instead of silently dropping the
  entire depth harvest.
- `seed` degrades gracefully (a clean message, exit 1) on any Common Crawl failure,
  including a mid-stream connection reset — no raw traceback.
- De-duplication no longer over-merges distinct roles that share a title prefix
  (e.g. "AI Engineer" vs "AI Engineer, Payments").
- Keyword-stuffed titles can't run away the score (the title double-count is capped).
- Remote/on-site negation is read from the title and location, not only the body.
- `first_seen` is Eastern Time (was naive local), matching the age math.
- `apply` / `dismiss` on a non-existent id exits nonzero.

### Security

- SmartRecruiters no longer hard-codes `?q=AI`; it harvests generically like the other
  ATS sources and lets the relevance gate filter.
- Braintrust pagination only follows a `next` URL that stays on its own host (SSRF guard).
- Watchlist slugs are validated (`[A-Za-z0-9._-]`) before being spliced into ATS URLs.

## [0.2.0] - 2026-07-14

### Added

- `job-radar init` — writes a starter `job-radar.yaml` + `watchlist.json` into the
  current folder (refuses to overwrite existing files). The example config and
  starter watchlist now ship inside the package.
- CI (GitHub Actions): `ruff` + `pytest` on Python 3.10–3.13, plus CodeQL.
- `SECURITY.md`, `CONTRIBUTING.md`, this changelog.
- Tests for the source parsers, `engine.harvest` end-to-end, the watchlist funnel,
  and the date/salary/word-match helpers.

### Changed

- **De-duplication is now linear instead of O(n²)** — a company-block index plus
  block/title precomputed on insert. Output is byte-identical to before; a run over
  ~8k postings drops from ~31s to ~3s of CPU.
- Breadth sources are fetched **in parallel** (like the depth sources); removed the
  pointless cross-host delay between independent providers.
- Keyword scoring scans the fit-weights **once** per posting (was twice).
- Seniority is **kept** in the de-dup key: `Staff` / `Senior` / `Lead` are treated
  as distinct roles instead of collapsing into one.
- Dates are now Eastern Time throughout (fixes off-by-one role ages near midnight).
- Install: use `pipx install git+https://github.com/hawkesj12/job-radar` until a
  PyPI release is published.

### Fixed

- A non-integer `ADZUNA_PAGES` / `USAJOBS_RESULTS_PER_PAGE` no longer crashes every
  command at import; a malformed `job-radar.yaml` now warns and falls back to
  defaults instead of dumping a traceback.
- Auto-discovery no longer writes into the shipped `watchlist.example.json`
  template; it seeds and grows a real `watchlist.json`.
- A recruiter re-titling a role you already applied to no longer resurfaces it as a
  new row (sticky status now re-matches on the stable job URL).
- Salary parsing no longer mistakes funding figures ("$20-40 million") for pay.
- Broken job sources surface in the run's error count instead of silently looking
  like "no jobs."
- The LLM re-rank path writes the shortlist once per run instead of twice.

### Removed

- The unimplemented `[scrapers]` extra and its config key (it never did anything).
- Dead code (`util.env`, an unused constant) and the misleading optional-rapidfuzz
  fallback (rapidfuzz is a required dependency).

## [0.1.0]

- Initial public release.
