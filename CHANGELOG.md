# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.5.0] - 2026-07-31

Two things, and the smaller headline is the more important one.

**job-radar did not work on Windows.** Not "had rough edges" — `import job_radar`
raised, so every command failed on every released version, 0.2.0 through 0.4.1.
CI had only ever run on Linux, so nothing contradicted the 0.2.0 entry claiming
Windows was supported.

**And it gains the meta-aggregator.** Every existing breadth source is one
publisher's own index; Google for Jobs is Google's index OF those publishers,
plus the company career pages and enterprise ATSs (Workday, iCIMS) that no single
feed exposes — reachable by title and location, with no per-tenant polling.

### Caller-visible contract change (jobfitr)

**A deduped role's `url` can now differ from what an earlier version returned.**
When the same role arrives from several sources at an equal fit score, the merged
row keeps the higher-preference source's copy, and `google_jobs` outranks
everything else — because its `apply_options` resolve to direct-to-employer links
rather than an aggregator redirect. The fit **score** is unchanged and remains
source-agnostic: `score_and_signals` reads only a posting's content. Source
preference breaks ties, it never contributes points.

### Added

- **`sources.search_google_jobs`** — Google for Jobs through SerpApi, queried by
  title + location exactly like `search_adzuna`/`search_usajobs`. Off unless
  `SERPAPI_KEY` is set, and skipped with a one-line note when it isn't, matching
  how Adzuna already behaves. Metered on purpose: SerpApi's free tier is 250
  searches a month and each PAGE is one search, so `google_jobs_pages` defaults
  to 1 (~10 roles per query).
- **`_best_apply_link`** prefers the first non-aggregator host from Google's
  ordered `apply_options`, so a listing routes to the employer's own careers page
  or ATS instead of LinkedIn/Indeed/ZipRecruiter. Direct-to-company links are the
  product promise; an aggregator redirect is a worse version of the same role.
- **`_google_posted`** resolves Google's relative recency strings ("16 hours ago",
  "30+ days ago", "today") to an absolute Eastern date at fetch time — the same
  rot-in-the-cache trap Workday's `postedOn` has, where a stored relative string
  silently ages into a lie.
- **`sources.google_jobs.{key_env,pages}`** in the config file, with a loader, so
  the knob the example config documents is one the code actually reads.

### Fixed

- **`tzdata` is now a dependency on Windows** (`sys_platform == 'win32'`). Windows
  ships no system time-zone database, so `zoneinfo` has nothing to read and
  `ZoneInfo("America/New_York")` raises `ZoneInfoNotFoundError`. Both `util.py` and
  `sources.py` construct that object at **module level**, so the failure landed on
  import rather than on a date calculation — the package was unusable, not merely
  wrong about times. Linux and macOS have a system tzdb and are unaffected, hence
  the environment marker instead of an unconditional dependency.
- **`job-radar init` silently disabled Workday.** The shipped starter config
  listed five of the six ATS adapters under `sources.ats`, and an explicit list is
  a SUBSET filter — so every user who ran `init` since 0.3.0 had the enterprise
  tier switched off while the README described it as the reason to use this tool
  for non-tech work. Both copies of the example config now list every adapter, and
  a test asserts the config enables everything in `DEPTH_ALL`/`BREADTH_ALL` so the
  two cannot drift apart again.
- **The README told people to install from Git.** It had said a PyPI release was
  "coming" since before 0.2.0 went up on 2026-07-19, so the headline install
  command was wrong for twelve days and five releases. It now says
  `pipx install job-radar`, and states the supported platforms.
- `CONTRIBUTING.md` counted two runtime dependencies; there are now three.

### Changed

- **CI now runs on Linux, macOS, and Windows** across Python 3.10–3.13 (12 cells,
  was 4 on Linux only), and adds `mypy`. The Windows cells found the bug above on
  their first execution. A wheel end-to-end step also builds the artifact, installs
  it into a clean virtualenv, and runs `job-radar init` from outside the repo —
  the editable install the tests use cannot prove `package-data` shipped.
- **Releases are cut by pushing a `v*` tag**, which re-runs the full matrix on the
  released commit, refuses to publish if the tag and the packaged version disagree,
  and creates the GitHub Release from this file. The previous workflow published on
  a manually-created Release without running any tests, and had never once run.
- Dependabot now tracks GitHub Actions and pip, with the workflow actions pinned by
  commit SHA rather than a mutable tag.
- Type annotations on the `DEPTH_ALL`/`LIVENESS` registries and `discover.known_keys`,
  whose deliberately non-uniform key shape (a 3-tuple for Workday, a 2-tuple
  otherwise) is now documented rather than implied. No behaviour change.
- The version sections in this file were out of chronological order — 0.4.1 sat
  between 0.3.2 and 0.3.1. Reordered, content untouched.

### Known issues

- The `[Unreleased]` section below is stale: its contents shipped with 0.3.0 and it
  has not been folded into that entry.

## [0.4.1] - 2026-07-23

### Fixed

- `discover.name_variants` leaked a bare generic word when normalization collapsed a
  multi-word name to one token. `_norm_name` strips trade words (`group`, `company`,
  `holdings`, `the`), so `Capital Group` reduced to `capital` and the "conservative"
  variants WERE that bare word — no `aggressive` opt-in, no ownership check — producing
  a real false binding (`Capital Group` -> `lever/capital`, Capital.com's board). The
  gate now fires only when a TRADE word caused the collapse; a legal-suffix-only
  collapse (`ACME LLC` -> `acme`) is still a valid slug and is preserved.

## [0.4.0] - 2026-07-22

Discovery stops using a full job harvest to answer a yes/no question. Confirming
that one Workday board exists cost **210 HTTP requests** (10 list pages plus one
detail call per role, measured against a live tenant); it now costs **1**. That
over-fetch was itself what tripped Workday's rate limiter, so the 429 handling
added in 0.3.0 was defending against a storm this code was causing.

### Caller-visible contract changes (jobfitr)

1. **`discover.probe`'s `roles` value changes meaning.** It is now the ATS's own
   reported total from a cheap liveness call, not the length of a full fetch. For
   Workday that means the true open-role count instead of the page-capped 200 — a
   more accurate number, but a **different** one. Anything sorting or displaying
   `roles` will see larger values.
2. **`seed.ATS_PATTERNS` and `seed._JUNK` are gone.** Mining lives in
   `discover._PATTERNS` only. `seed.SeedError` still exists and is still catchable
   by that name — it is now an alias of the new `discover.DiscoveryError`.
3. **`seed.enumerate_tokens` is superseded by `seed.enumerate_entries`**, which
   returns full entry dicts rather than bare slugs (Workday needs its host and site
   to be fetchable at all). `enumerate_tokens` remains for slug-only callers.

### Added

- **`sources.LIVENESS` + `sources.liveness_for(ats)`** — cheap per-ATS "is this
  board real" probes returning an exact role count. Measured against live boards on
  2026-07-22: Greenhouse without job bodies (244 KB vs 4.4 MB), Lever `?limit=1`
  (8 KB vs 379 KB), Workday one POST with no detail pass (1 request vs 210),
  SmartRecruiters `totalFound` (verified to agree with a full fetch). Workable uses
  the documented `details=false` variant but its saving is **unverified** — no
  reachable Workable account had open roles to measure against. Ashby is excluded
  on purpose: measured, it returns its whole board either way, so there is nothing
  cheaper to call. Ashby, and any future ATS without a variant, fall back to the
  full adapter transparently — callers never need to know which are cheap.
- **`job-radar seed workday`** — the miner already understood Workday's
  tenant/host/site triple; the CLI just never offered it. The choice list is now
  derived from the miner's own pattern table so the two cannot drift again.

### Fixed

- **A corrupt `watchlist.json` no longer discards a good scan.** `cli.cmd_scan`
  caught `OSError`, but `json.JSONDecodeError` is a `ValueError` — so it escaped
  _after_ the full network harvest and _before_ the shortlist write. Growing the
  watchlist is a nice-to-have; the harvest is the point.
- **A mid-pagination failure no longer loses the whole employer.** `fetch_workday`
  now keeps the pages it already fetched and stops early, matching the best-effort
  discipline the detail pass already used. A failure on page 1 still raises, since
  there is nothing to salvage and a silent empty would look like a live board with
  no jobs.
- **Nested thread pools.** The Workday detail pass opened its own 8-worker pool per
  employer inside the engine's 12-worker pool: a measured peak of **96** concurrent
  requests against a nominal cap of 12. One shared, lazily-created pool now makes
  the real ceiling ~20.
- **Mining missed `job-boards.greenhouse.io`** (Greenhouse's current host) and
  **dropped whole Workday tenants on a lowercase `en-us`** locale segment. Both
  came from this module carrying its own narrower copies of regexes
  `dedup.ats_from_url` already had right; the five single-key ATSs now route
  through that one parser. The third copy, in `seed.py`, still cut a slug at `?`
  rather than `&` — the bug 0.3.1 fixed elsewhere — and is deleted.
- **`discover.match_known` was O(names × variants × universe)**, building a lookup
  dict and then discarding it. 428.8 ms → 6.22 ms at 5k × 5k, with output pinned
  byte-identical against the previous algorithm by a differential test.

### Changed

- `seed --verify` probes concurrently through `discover.probe` instead of one
  board at a time with a full harvest fetch each, keeping the same early stop at
  `--max`.
- Removed every unsourced percentage from the discovery docstrings (mining yield
  rates, "120 real store names", per-job timings). No artifact ever existed to
  check them against, and `mine` caps CDX _rows_ rather than companies over a
  SURT-sorted index — so any rate measured that way describes an alphabetically
  truncated slice, not the population. The mechanisms they were attached to are
  unchanged and still documented.

## [0.3.2] - 2026-07-22

Documentation correctness. An independent panel review reproduced five claims in the
docs and comments that contradicted the shipped code; a docstring that confidently
states the opposite of what a function does is worse than no docstring, because it is
the one place a caller looks. No behaviour changed except the one item noted below.

### Changed

- `fetch_workday`'s docstring claimed descriptions are "deliberately NOT fetched."
  They are fetched by default. It now states the real cost — one request per role,
  not per page — and the cap comment gives the combined list+detail total rather than
  the list-only figure, which understated a realistic run by an order of magnitude.
- `verify_identity` credited `capital`/Capital One and `foundation`/Foundation for
  the NIH as catches. Neither is a live Greenhouse board, so the liveness probe drops
  them before the identity gate runs. The docstring now cites only confirmed catches
  and states two boundaries that were easy to misread: a dead slug never reaches the
  gate, and on Lever it returns `True` unconditionally — so the motivating example
  (`jobs.lever.co/capital`, a real board that is not Capital One's) is protected by
  `from_names` withholding the first-word variant, not by this function.
- `probe`'s documented outcome enum omitted `throttled` and `unsupported`. It is now
  complete and split into terminal versus retryable, since deciding whether to
  blacklist is the reason a caller reads it. Also documents that `wrong-owner`
  currently fires when the identity endpoint is merely unreachable.
- The 200-role Workday cap (`WORKDAY_MAX_PAGES` x 20) was undisclosed. It is
  documented as silent and lossy — NVIDIA reports `total=2000` and returns 200 — and
  ordering is Workday's own, so the roles kept are not necessarily the newest.
- `engine`'s module docstring still said the engine grows the watchlist and that
  `store` writes postings. Neither has been true since 0.3.0.
- The 0.3.0 entry credited a fix for a Workday pagination bug that never shipped:
  `git log -S` shows the `total` latch was present in the same commit that
  introduced the adapter. Recorded as a design note under Added instead.
- README scopes its "every source is an official, public API used as documented"
  claim to exclude Workday, whose CxS endpoint is public and no-auth but is not
  documented for third-party use the way Greenhouse's and Lever's are.

### Added

- `WORKDAY_MAX_PAGES` reads the environment, like `WORKDAY_FETCH_DETAILS` and
  `WORKDAY_DETAIL_WORKERS` already did. Default unchanged at 10. It was the only
  Workday knob that could not be tuned, and documenting an unadjustable cap is half
  a fix.

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
