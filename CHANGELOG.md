# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
