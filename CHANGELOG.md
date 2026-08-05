# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **A structured record contract — ten new keys, every unknown `None`.** `function`
  (the job family), `org_unit` (the company's own team), `employer_org` (the
  employing organisation), `city` / `state` / `country`, `remote` + `remote_basis`,
  `tags`, and `seniority`. Sources that already sent this data were discarding it:
  SmartRecruiters alone carries a real job function, org unit, seniority string,
  structured geography and a remote **boolean** on every posting, and the adapter
  used none of it.

  Two rules make this a contract rather than a rename. **`None` is not `False` and
  not `""`** — it means the source did not say, so a consumer can write
  `WHERE remote IS NOT NULL` instead of inheriting a guess. And **every derived value
  carries its basis**: `remote_basis` records whether remoteness came from a source
  field, a location rule, or the description, so a consumer that disagrees can
  override it rather than re-deriving everything.

- **`--format ndjson`** — the machine-facing output. One JSON object per line to
  stdout, the run manifest and progress to stderr, so `job-radar --format ndjson
  --all > jobs.ndjson` produces a clean file. CSV cannot represent a list, a boolean,
  or the difference between "unknown" and "empty", which is exactly what the contract
  above turns on.

- **A run manifest**, one object per harvest: row counts per source, which adapters
  failed, companies discovered, and the filter config that produced the run. A store
  fed only rows cannot answer "why did Tuesday have four hundred fewer jobs".

- **Three new adapters, each rights-checked before a line was written** (`catalog/`):
  - **Rippling** (depth, keyless). List endpoint returns five fields and no body or
    date; `RIPPLING_FETCH_DETAILS=1` (the default) fetches one detail call per role to
    fill `text`, `posted`, `employment_type` and the full multi-location list — the same
    trade Workday makes, and for the same reason. `live_rippling` answers liveness from
    the list alone: **1 request instead of 749** on Rippling's own 748-role board.
  - **Teamtailor** (depth, keyless). One request; the JSON Feed carries body and date,
    and its `_jobposting` schema.org block supplies location and employment type.
    Deliberately has **no** `live_*` variant — the feed is a single document, so a
    liveness call and a full fetch are the same request, and `liveness_for()` falls back
    to counting a full fetch (as it already does for Ashby).
  - **The Muse** (breadth, keyless). The least tech-skewed source in the catalog — 11%
    tech titles measured — carried for the non-tech coverage nothing else provides. It
    has **no title search**, verified across nine parameter names, so `queries` is
    accepted for signature parity and never reaches the URL. Bounded by
    `THEMUSE_MAX_PAGES` (default 5) and hard-stopped at the vendor's page-99 cap.

### Changed

- **Workday and Rippling no longer buy job descriptions they are about to discard.**
  Both fetch one detail request per role for the body, and the relevance gate ran
  afterwards — so a harvest paid for the full description of every role it rejected on
  the title alone. The gate now runs first, inside the adapter, against titles the list
  endpoint already returned. Measured across the ten shipped Workday employers:

  | | requests | roles |
  | --- | ---: | ---: |
  | before (cap 200, bodies for all) | 1,663 | 1,583 of 6,922 |
  | after (bodies after the gate) | **903** | **6,922** |

  Every role, for roughly half the requests the truncated version cost. `keep=None`
  preserves the old behaviour for a direct caller. Because of it, `WORKDAY_MAX_PAGES`
  rises from 10 to 25 (200 → 500 roles/employer): the cap was standing in for a request
  budget, and the gate is now what bounds the cost.

### Deprecated

- **`department`.** It carried four different things depending on the source — an org
  unit on Greenhouse and Ashby, a job function on Adzuna, a seniority level on
  Braintrust, and the **employer** on USAJOBS — so a consumer pouring it into one
  column got a category dimension it could not filter on. Still emitted
  byte-identically, and a test pins that. Use `function` / `org_unit` /
  `employer_org` / `seniority`. Removed at 1.0.

### Removed

- **The TechTree adapter (`search_techtree`)**, and its entry in `BREADTH_ALL`, the shipped
  example config, the live canary's whole-board list, and its parser test. Removed for two
  measured reasons rather than a judgement call: the feed carries **personal data** — every
  row's `delivery_owner` names an individual — and **60 of 76 postings are anonymised**, with
  `company_name` reading "TechTree's client", which collapses unrelated employers in any
  dedup keyed on company. It was also the stalest breadth source measured (45-day median,
  74% inside 100 days) and not a remote board (24 of 76 remote). Its terms additionally read
  as prohibiting this use; that reading is contested and is recorded in full, both sides, in
  `catalog/techtree.md`. Breadth stays at **8** keyless adapters — TechTree out, The Muse
  in, in the same release; the record shape,
  scoring, dedup and every other adapter are untouched.

- **The repo-root copies of `job-radar.example.yaml` and `watchlist.example.json`.**
  Each example file existed twice — once at the root, once under `job_radar/data/` —
  and only the packaged copy is what the wheel ships and `job-radar init` writes. The
  two had already drifted once (a 2026-07-18 fix corrected the packaged watchlist and
  left the root one pointing at five boards that now 404), and the guard against that
  was a test pinning them byte-equal. Deleting the second copy removes the failure
  mode instead of policing it. **Nothing a user receives changed**: `init` read the
  packaged copy before and reads it now.
- **`prompts/build-config-with-ai.md`**, the paste-into-an-AI config interview, and
  the README paragraph advertising it. It carried a third copy of the adapter lists,
  which drifted 19 days behind and handed people a config with `workday`,
  `google_jobs` and `usajobs` silently switched off — the same bug 0.5.0 fixed in the
  shipped example. A doc that generates config is config, and this one had no reason
  to be a separate copy of it.
- `_resolve_config` no longer probes `./job-radar.example.yaml` as a fallback
  candidate. It existed for running from a clone with the root copy present; with
  that copy gone it could only match a file the user placed there. The generic
  defaults it falls through to are the same configuration the example encodes.

### Fixed

- **SmartRecruiters returned 100 rows of every board, however large.** The API clamps
  `limit` at 100 and says nothing — `?limit=200` returns 100 rows and echoes
  `limit: 100` — and the adapter made a single call. Measured on a real board
  (`boschgroup`): **100 of 4,716 postings, 97.9% dropped**, silently, on every run.
  The module already knew the true number: `live_smartrecruiters` reads `totalFound`
  and feeds discovery's role-count sort while the fetch returned 100 — two functions
  in one file disagreeing by 46x. Now pages with `&offset=`, bounded by
  `SMARTRECRUITERS_MAX_PAGES` (default 10 = 1,000 roles/company).

- **The Muse fetched 100 rows out of ~36,060.** The unfiltered feed hard-caps at page
  99 = 2,000 rows, and the cap applies **per category slice** — so the 20-category
  fan-out is the only way past it, and it was never implemented. All 20 category
  values were probed individually before shipping — necessary because this API
  silently ignores an unrecognised parameter *value* and serves the unfiltered feed,
  so an unverified slice would look healthy while being a copy of the others. What blocked it was a
  wrong entry in our own catalog claiming category filtering was unreliable;
  re-measured, `category=Healthcare` returns 20/20 Healthcare rows with zero overlap
  against the unfiltered page. The trap is corrected in `catalog/themuse.md`. The Muse
  also now emits `seniority` from its `levels` field.

- **Himalayas was paged on the wrong endpoint.** This source has two, with different
  pagination: `/jobs/api/search` takes `page` and walls at ~8,020 rows;
  `/jobs/api` takes `offset` and walks the whole corpus (**96,934** measured). Sending
  `offset` to the search endpoint is silently ignored and returns page 1 forever. A
  browse lane is added alongside the search lane (`q` does nothing on browse, so it
  cannot replace it). Bounded by `HIMALAYAS_BROWSE_PAGES` (default 50 = 1,000 rows).
  Browse is **date-ordered** — measured offset 0 → median age 0 days, 20,000 → 8 days,
  60,000 → 28 days — which is what makes a bounded lane worth having: those 1,000 rows
  are the newest 1,000, not an arbitrary slice. An age stop exists as a secondary
  guard, but at the default 60-day window it cannot fire inside the page cap; the cap
  is the budget. Walking the full corpus would be ~4,850 requests.

- **USAJOBS never sent `&Page=`.** One request per keyword, so anything over one page
  was truncated — `SearchResultCountAll` reports the true total and nothing read it.
  Measured in `catalog/usajobs.md`: "medical assistant" 736 and "registered nurse" 620
  against a 500-row page, i.e. 236 and 120 postings lost invisibly. Now pages, bounded
  by `sources.usajobs.max_pages` (default 3), with the politeness pause applied between
  pages as well as between queries.

- **Hacker News read one thread.** On the 1st of a month that thread is nearly empty
  and the entire prior month vanished. The Algolia search already returns four, so
  reading the two newest costs one request: measured 2026-08-04, 138 rows became 383.

- **Adzuna returned ZERO rows whenever the configured location was "remote".**
  `where` resolves against a place hierarchy, so the word was being sent as a town
  name — and zero rows is indistinguishable from "no such jobs" behind the adapter's
  error handling. Measured on `what="AI Engineer"`, US: `where=remote` → 0;
  `where=""` → 55,052 at **2%** actually remote; `what_and=remote` → 15,500 at
  **84%**. Blanking `where` was the tempting wrong fix; the remote keyword filter is
  the right one. Real places are unaffected and compose with it.

- **The Adzuna radius guard tested the wrong thing.** It checked
  `location != "remote"` on its own, so "anywhere" slipped through and a `distance`
  was sent with no place to anchor it. Both branches now share one predicate.

- **Adzuna nationwide postings are recognised as remote.** `location.area == ["US"]`
  exactly means nationwide, and the adapter kept only `display_name` — which for
  those rows is the bare string `"US"`, invisible to any text rule. `area[1]` is also
  a real US state in 246 of 246 rows sampled, and was being thrown away.

- **Google for Jobs named a remote filter and never applied it.** The adapter's own
  comment said Google treats "remote" as a filter rather than a place, then dropped
  the word and set nothing — so a remote search silently ran unfiltered and
  nationwide. It now sets SerpApi's documented `ltype=1` work-from-home filter.

- **The remote gate ignored every structured remote signal.** `is_remote` re-derived
  remoteness from prose even when the source stated it outright. A structured flag now
  wins, and `None` still falls through to the text rule — unknown is not `False`.
  Without this the mapped `remote` field would have been decorative.

- **Remotive was called four times per run, identically.** Every parameter on that
  endpoint is ignored, so `?search={query}` filtered nothing and the four calls were
  four copies of one request. Remotive's own notice advises a maximum of **four
  requests per day**; four per run is 96 on an hourly schedule. Now one unfiltered
  request, which returns the whole 31-row corpus anyway.

- **Himalayas was under-fetched by roughly 60x.** The adapter sent `limit=20` and no
  page parameter, taking 20 rows per query out of a measured 8,020 reachable. The
  trap: this source has two endpoints with different pagination models — `/jobs/api`
  takes `offset`, `/jobs/api/search` takes `page`, and sending `offset` to the search
  endpoint is silently ignored and returns page 1 forever. Now pages properly,
  bounded by `HIMALAYAS_MAX_PAGES` (default 10 = 200 rows/query).

## [0.6.0] - 2026-07-31

The release that stops the engine quietly deleting jobs you wanted, plus a
faster, lighter scan. **Scores are unchanged** — a role's number is exactly what
it was in 0.5.3.

### Fixed

- **Different openings stopped being merged into one.** A company's `AI Engineer`
  and `AI Engineer, Ads` were collapsing into a single row, as were `II` vs `III`
  and `(East)` vs `(West)` — and a merge **discards the loser's apply URL**, so
  the second role was deleted before you ever saw it. Which copy survived depended
  on the order the feeds happened to answer in, which is why this was invisible.

  The matcher decided on string similarity alone, and string similarity is
  positive evidence only — nothing in it could argue _against_ a match. The
  outcome therefore tracked suffix **length** rather than meaning: `, Payments`
  was just long enough to fall below the threshold, `, Ads` was not. Titles are
  now checked for **disqualifying marks** — a seniority/level mark, and a trailing
  qualifier like `(EU)` or `, Ads` — and a disagreement vetoes the merge before
  any similarity is consulted. Two different job ids on the same board are also an
  absolute veto: that is two openings by definition.

  **You will see more duplicate rows**, and that is the intended trade. A wrong
  merge deletes a job you wanted and hides it; a wrong split shows a redundant row
  you can ignore. The provenance tiebreak from 0.5.0 still puts the employer's own
  ATS link first, so the extra row is the redirect, not the real one.

  Honest about the limits: this is a rule-based approximation of proper record
  linkage, not the real thing — it still contains a hand-tuned threshold. And two
  postings with **byte-identical** titles on one board still merge, because
  splitting those would mean putting the job id into the store's primary key,
  which would orphan every existing row and could reattach an "applied" status to
  the wrong opening. That is a worse bug than the one it would fix.

- **A shortlist saved in the wrong encoding no longer bricks every command.**
  Excel's "Unicode Text" save writes UTF-16, and that raised a raw decoder
  traceback out of _every_ command — including `apply` and `dismiss`, so you
  couldn't reach your own file to fix it. UTF-16 now loads; anything genuinely
  unreadable gets one sentence naming the file and the fix.

### Performance

None of these change a single score, ranking, or row — only how long a scan takes
and how much memory it uses.

- **HTTP connections are reused per host.** A scan is ~500 companies concentrated
  onto a handful of ATS hosts, and every request was opening a fresh TCP + TLS
  handshake: 149ms cold against 84ms on a reused connection. The pool is
  per-thread, so connections are never shared between workers, and a socket the
  server closed while idle is transparently retried once on a fresh one.
- **The discovery funnel probes in parallel.** It was serial: ~150 dead candidates
  measured at roughly 60 seconds of requests to add zero companies. The probe
  budget added in 0.5.x already caps how many requests go out, so this is purely
  wall-clock — the load on third-party boards is unchanged.
- **Peak memory during a scan is bounded.** All ~500 companies were submitted at
  once, so every fetched job description stayed in memory whether or not it had
  been processed yet (~1.25 GB at 500 companies). Fetching now runs on a sliding
  window and results are released as they are consumed.

### Changed

- **The README's scoring claim was wrong and has been corrected.** It advertised
  "term-frequency saturation (`score_k1`)". There is none: each keyword counts at
  most once, so there is no term frequency to saturate — repeating a keyword can
  never raise a score (measured 26 / 26 / 25 / 22 at 1x / 5x / 50x / 500x, falling
  only because repetition lengthens the document). `score_k1` is real but it is a
  _gain_ on length normalization. Counting each keyword once is a deliberate
  anti-keyword-stuffing choice and is unchanged; only the description of it was
  false. This also corrects the 0.5.2 entry below, which claimed the BM25 label was
  "true for the first time" — the length-normalization half was, the
  term-frequency-saturation half never was.
- Three `title_penalty` keys were reported as unreachable dead config. They are
  not: a bare "Research Scientist" is filtered by the relevance gate before
  scoring, but "AI Research Scientist" reaches it and the penalty fires correctly.
  Kept, with a test proving it rather than a deletion.

## [0.5.3] - 2026-07-31

### Fixed

- **A scan with the LLM re-rank enabled stored nothing.** Introduced in 0.5.2 and
  fixed here. `cli.cmd_scan` passed `write=not llm_on` to `upsert` — skipping the
  write to avoid one rewrite — and the `annotate()` call added in the same release
  then re-read the file, which therefore never contained the harvested rows, and
  wrote that back. The scan evaporated while the CLI printed that it had tracked
  the roles: `apply <id>` could never find an id, `first_seen` never accumulated,
  and every role stayed "new" forever.

  It only affected runs with `llm.enabled: true` (off by default, needs an API
  key), and only 0.5.2. The harvest is now always persisted, then annotated.

  Why it shipped: `write=False` had one caller and zero tests, and there was no
  end-to-end test of `cmd_scan` with the LLM on — so the one production path
  without coverage was the one that broke. There is now a test that drives the real
  `cmd_scan`, because the defect was in how the CLI wired two correct functions
  together rather than in either of them.

- The AI-config prompt (`prompts/build-config-with-ai.md`) emitted a `sources.ats`
  list that omitted **workday** — reintroducing, through the "let AI write your
  config" path, the exact bug 0.5.0 fixed in the shipped example. It also predated
  `google_jobs` and `usajobs`. It now tells the assistant to OMIT those keys unless
  narrowing (absent means "every adapter this build ships"), and a test asserts the
  prompt mentions every registered adapter. A doc that generates config is config.

## [0.5.2] - 2026-07-31

Three ways the store could lose your work or fail without saying so. All three were
found by an independent review of 0.5.0 and classified minor; all three are
reproducible, and two of them break the promise this tool leads with — that it
remembers what you have applied to.

### Fixed

- **`apply` during a scan was silently discarded.** `upsert` and `mark_status` both
  read the whole store, change it in memory and write it back. The write is atomic,
  but atomic is not serialized: a scan that read BEFORE your `apply` and wrote after
  it put the pre-apply rows back, so the role lost its status and resurfaced. A scan
  takes about a minute, so a cron run overlapping a manual `apply` — or simply two
  terminals — is enough. Both paths now hold an exclusive `flock` across the whole
  read-modify-write.

  Locked on **both** platforms: `fcntl.flock` on POSIX, `msvcrt.locking` on
  Windows, polled to a deadline because its blocking mode gives up after ten
  seconds and a scan can run longer. The first attempt fell through to "unlocked"
  on Windows — so a Windows user would have silently kept the exact bug this
  fixes. The Windows CI cells caught it, because the test asserts the guarantee
  rather than the implementation.

  Deliberately an OS lock on a descriptor rather than a lock FILE. The existing note in
  `funnel.append_watchlist` rejects locking because "a lock file only risked getting
  stuck after a crash" — true of lock files, and not of `flock`, which the kernel
  releases when the process dies. Best-effort: a platform without `fcntl` proceeds
  unlocked rather than refusing to run.

- **The LLM path could undo concurrent changes.** It read the store, made a network
  request that can take many seconds, then wrote back the rows it had read —
  discarding anything that changed meanwhile. It now re-reads under the lock and
  grafts the scores on by `dedup_key`.

- **A CSV saved by Excel broke `apply` and `dismiss` permanently.** Excel writes a
  UTF-8 BOM; read as plain UTF-8 those bytes become part of the first header name,
  so the column is `\ufeffid` rather than `id`, every id lookup misses, and the CLI
  reports "no role with id ..." forever — on a file that looks perfect in a
  spreadsheet. The store is documented as user-editable, so this was reachable by
  doing the obvious thing. Now read as `utf-8-sig`.

- **`--config <path-that-does-not-exist>` silently loaded a different config.** It
  fell through to `./job-radar.yaml`, so a typo in the path ran happily against
  someone else's settings and reported nothing. Now exits 2 with the path named.
  Same trust rule as the auto-discovery guard in 0.5.0, applied to the path where
  the user was explicit.

## [0.5.1] - 2026-07-31

### Fixed

- **One malformed posting could kill an entire harvest.** A JSON `null` from any of
  the ~500 sources arrived as `None`, and `.get(k, "")` does not guard against that —
  its default fires only when the key is ABSENT, so a present-but-null `title` yielded
  `None` and the first `.lower()` raised `AttributeError`. The damage was
  disproportionate: `engine._consume` runs OUTSIDE both of `harvest`'s try blocks, so
  the crash escaped per-source error handling entirely, discarded a network harvest
  that had already completed, and skipped the "keep your existing shortlist" guard on
  the way out — a raw traceback and a lost run, caused by one bad row from one vendor.

  Every text field is now coerced once at `_consume`'s boundary, which is the single
  point every posting from every adapter must cross. A bad ROW is dropped; it is not
  treated as a bad SOURCE. Wrong-typed values (a list, a dict, an int) are handled by
  the same guard, since `null` and `123` fail identically downstream.

  This was found during the 0.5.0 review and deferred as non-blocking. That was the
  wrong call — it is a crash on the main path, reachable from any source, and it
  should not have shipped.

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
- **`job-radar --version`.** It had none — the first thing anyone types at an
  unfamiliar CLI, and what a packaging smoke test calls to prove the entry point
  works. Top level only, so `job-radar list --version` is still an error rather than
  quietly printing a version instead of running the subcommand.

### Caller-visible: the fit score changed

**Every score moves, and merged roles may now carry a different URL.** Two fixes to
the scorer, found by an independent review after the rest of this release landed:

- **The score rewarded brevity, and it inverted the product.** `raw / norm` is the
  k1→∞ limit of BM25 — term-frequency saturation switched off — and because `norm`
  bottoms out at `1 − length_norm_b` = 0.25, a very short posting had its score
  multiplied by up to **4×**. That floor is independent of `avg_jd_tokens`, so
  raising it from 400 to 1600 (below) could not fix this and did not. The
  consequence: an 80-word aggregator stub outscored the same role's full employer
  description, and since the merge tiebreak led with score, **14 of 20 merged roles
  on a live board handed the user a RemoteOK redirect instead of the company's own
  ATS link** — the opposite of "routes you to the source." Scoring here is
  presence-based (a keyword counts once), so BM25 collapses to
  `raw·(k1+1)/(1+k1·norm)`; that is now what runs, with `score_k1` (1.2) exposed in
  the config. The README's "BM25 length-normalized" claim is true for the first time.
- **The merge tiebreak now leads with provenance, not score.** Two copies of one job
  are the same job, so "which fits better" was never the right question between
  them — only "which is the better record." `_SRC_PREF` also ranked a company's own
  Greenhouse board **equal to** a RemoteOK redirect; depth sources (the employer's
  ATS) now outrank Google-for-Jobs, which outranks aggregators. Measured across
  three live boards in both arrival orders: aggregator retention **70–88% → 0%**.

Expect an existing `shortlist.csv` to re-rank on the next run, some short aggregator
stubs to fall below `min_score` (they were clearing on inflation), and merged roles
to carry employer URLs where they carried redirects. If you tuned `min_score` against
the old inflated scale, lower it. No CSV schema change.

### Security

- **The LLM API key could be printed to stdout.** `llm.rerank` reported failures as
  `({type}: {e})`, and the exception carries the request header: a key with a
  trailing newline — a `.env` file, `$(cat key)`, CRLF on Windows — made
  `http.client` raise `ValueError: Invalid header value b'sk-ant-…'`. A scheduled
  run redirects stdout to a log file, so the key landed on disk. It now reports the
  exception **type only**, matching the eight other error paths that already did.
  `Config.env()` also strips whitespace now, removing the trigger for every
  credential at the one chokepoint they all cross.
- **Formula injection via the `posted` column.** `_csv_safe` was applied only to a
  curated `TEXT_COLS` set that omitted `posted`, which is fed from vendor data by
  `to_date` — and `to_date` returned `str(val)[:10]` for any string, so any of ~500
  boards could put a live formula in your spreadsheet. `to_date` now returns `""`
  for anything not date-shaped, and every column is sanitized. `TEXT_COLS` is
  deleted rather than corrected: curating "the untrusted columns" is a judgement
  that must be re-made on every new column, and it was already wrong once.

### Fixed

- **The depth lane was buried.** On a clean install, company ATS feeds — what this
  tool leads with — harvested 436 roles and surfaced **1**, while aggregators
  surfaced 14 from 168. Three compounding causes, all fixed:
  1. `"on behalf of"` scored −10 as an agency signal. It is generic English, and it
     appears verbatim in employers' own anti-recruitment-fraud boilerplate ("we may
     partner with vetted recruiting agencies who will identify themselves as
     working on behalf of X") — present on **20 of 20** relevant roles on one live
     board. Removed.
  2. `agency_penalty` was the only uncapped score component and the only one that
     goes negative, so a long thorough JD accrued penalty without limit while its
     body score was normalized down. Now capped by `agency_penalty_cap` (15), like
     `blob_score_cap` and `title_score_cap`.
  3. `avg_jd_tokens` was 400 — roughly a job-board _summary_. Real full ATS
     descriptions measure a median of ~1590 tokens, so every thorough posting was
     treated as abnormally long and had its score divided by ~3.2. That inverts what
     BM25 length normalization is for. Now 1600.

  Measured on one live board: 0 of 20 relevant remote roles surfaced before, 7 of 20
  after. **Read that as a smoke test, not as validation.** n=20, one board, no
  labelled ground truth, and the success metric is output volume — which is exactly
  what the tuned parameter controls. It does reproduce across five boards, and 400
  was measurably wrong, so the direction is right; but a defensible calibration needs
  a few dozen hand-labelled roles and a precision@k number, and that has not been
  done. (A reviewer's claim that simply lowering `min_score` would produce the same
  result was tested and is **false**: matching the surfaced count needs
  `min_score=9`, and the resulting sets differ at Jaccard 57%, with ~10% of ordered
  pairs re-ranking. A length-dependent transform genuinely re-orders; a threshold
  cannot.)

- **The discovery funnel spent up to a minute per scan to add nothing.**
  `funnel_max_new_per_run` caps companies ADDED, and a dead slug is never an "add" —
  so on a run where candidates were dead the cap never fired and every one of them
  got probed, serially. Measured: 150 dead candidates, ~60 s, 0 added, on every scan,
  with `auto_grow` on by default. New `funnel_max_probes_per_run` (50) bounds
  attempts. Not parallelized on purpose: probing concurrently would make the healthy
  case worse by hitting every candidate every run, which is more load on other
  people's ATS endpoints from a tool strangers install.
- **The agency penalty scored keywords found in a role's title or location.** It is
  meant to read company + description only. Introduced earlier in this same release
  when that penalty was routed through the keyword prefilter with the wrong token
  set, so a role titled "Staffing Engineer" was penalised as a staffing agency.
  Caught by the equivalence gate below, on its first honest run.
- **`search_usajobs` had no politeness delay** while five sibling sources pause
  between queries, and it requests the largest page in the codebase.
- The repo-root and packaged copies of `watchlist.example.json` had **already
  drifted**: a 2026-07-18 fix corrected the packaged copy and left the root one —
  the file a GitHub visitor reads — pointing at five boards that now 404. Synced,
  and a byte-equality test now pins both copies of both example files.
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
- **Jobicy roles carried a Python list in the `department` column.** Jobicy returns
  `jobIndustry` as an array, and the adapter normalized `jobType` but not this one,
  so `department` reached `shortlist.csv` as the literal text `['Engineering']`
  instead of a value you could filter on. Found by the hand-written Jobicy parser
  test built from that vendor's real response shape — which is the argument for
  building fixtures from real payloads rather than from the code.

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
- **A weekly audit** (`audit.yml`) runs four checks that catch problems appearing
  without this repo changing: a CVE published against a dependency, a rotted README
  link, an action tag repointed upstream, and the repo's OpenSSF Scorecard. A
  commit-triggered check cannot catch any of them — there is no commit to trigger
  on. `uv.lock` is committed to feed the CVE scan an exact dependency set; without
  one the scanner finds no package sources and passes having examined nothing. It
  does **not** change what `pip install job-radar` resolves, and it is not shipped
  in the wheel.
- Type annotations on the `DEPTH_ALL`/`LIVENESS` registries and `discover.known_keys`,
  whose deliberately non-uniform key shape (a 3-tuple for Workday, a 2-tuple
  otherwise) is now documented rather than implied. No behaviour change.
- The version sections in this file were out of chronological order — 0.4.1 sat
  between 0.3.2 and 0.3.1. Reordered, content untouched.
- **Adapter test coverage.** Six of the eleven breadth sources — Remotive, Jobicy,
  Arbeitnow, Himalayas, USAJOBS and TechTree — had no test at all, despite
  `CONTRIBUTING.md` requiring one for every source. All six now have parser tests
  built from their real response shapes, plus a **registry contract test** that
  iterates `DEPTH_ALL`/`BREADTH_ALL` and asserts the posting contract.
  134 tests, up from 107.
- **A weekly live canary** (`canary.yml`) asks the real APIs whether they still
  return the shape the parsers read. Fixture tests freeze a vendor's shape as of
  the day they were written and cannot detect drift; this can. It is scheduled, not
  part of CI, so a third party's outage never blocks a pull request, and it
  separates "unreachable" (skip) from "reachable but unparseable" (fail).
- **Scoring is 2.5x faster** — 961 to 388 µs per posting. The agency penalty ran 13
  whole-word regexes over the full description for every posting (68% of scoring
  CPU) while the `_present` prefilter twenty lines above solved exactly that for
  `fit_weights` and had never been extended to it. Output verified identical over
  4,000 adversarial postings.
- **The test guards added earlier in this release did not guard.** An independent
  review broke three of them, and they are rebuilt here:
  - The registry contract test stubbed the transport with `{}`; every adapter
    returns `[]` from that, so its per-key assertions iterated an empty list and a
    deliberately non-conforming adapter passed. Each adapter now has a sample
    payload of its own real response shape, and the test asserts a row was actually
    produced before judging it.
  - The example-config parity test read the repo-root copy while `init` ships the
    packaged one, so deleting `workday` from the file users receive left the suite
    green — the very bug this release fixed, reachable again.
  - The canary asserted non-blank only on `url` and `title`, so a vendor blanking
    every description kept it green while all scores went to zero; and because
    pytest exits 0 on an all-skip run, an aggregator blocking CI's IP range
    produced a green run that checked nothing. Both now fail. `search_remotive` and
    `search_himalayas` swallowed network errors internally, making outage and drift
    indistinguishable to the canary; they take `strict=True` from it now.
  - `search_google_jobs`, this release's headline feature, had **no executed
    coverage** of its parsing path. Now covered end to end.
  - **The scoring equivalence gate guarded nothing** — and this one is the reason
    the agency-penalty bug above shipped. `test_scoring_matches_bruteforce_reference`
    declares itself the gate that must fail if the scoring optimization changes
    results. Its reference still subtracted the agency penalty _uncapped_ after
    production started capping it, and it passed regardless, because its generated
    vocabulary contained not one agency keyword — so that branch was never compared
    in any of its 2,000 cases. Forced to one: production 49, reference −44. The
    reference now matches production and the corpus reaches every branch, verified by
    reverting each fix in turn and confirming the gate goes red.
  - A test now pins an **absolute** score for a fixed posting. Every other scoring
    test asserted a relative property (A > B), which survives any global rescale —
    which is how `avg_jd_tokens` stayed wrong by 4× for three releases with a green
    suite.

### Housekeeping

- The stale `[Unreleased]` section is folded into **[0.2.0]**, where it belongs.
  An earlier draft of this entry guessed 0.3.0; `git log -S` puts the introducing
  commit (`a6927cb`) at the v0.2.0 tag itself, so those notes were 0.2.0's all
  along and simply never got promoted. Content moved verbatim, nothing rewritten.

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

## [0.2.0] - 2026-07-14

### Added

- `job-radar init` — writes a starter `job-radar.yaml` + `watchlist.json` into the
  current folder (refuses to overwrite existing files). The example config and
  starter watchlist now ship inside the package.
- CI (GitHub Actions): `ruff` + `pytest` on Python 3.10–3.13, plus CodeQL.
- `SECURITY.md`, `CONTRIBUTING.md`, this changelog.
- Tests for the source parsers, `engine.harvest` end-to-end, the watchlist funnel,
  and the date/salary/word-match helpers.
- `--verbose` (print which sources failed and why) and `--strict` (exit nonzero if
  any source errored, for scheduled runs / CI) flags on `scan`.
- Quality-tier tags (`★ strong` / `◆ worth a look`) on each surfaced role, driven by
  the `scoring.tiers` config (previously loaded but unused).
- `seed` gained its own `--max` flag (default 500) instead of reusing the print
  `--limit` (which capped it at 25).

### Changed

- **De-duplication costs a factor of the company count less** — a company-block
  index plus block/title precomputed on insert, so the fuzzy pass compares only
  same-company candidates. Output is byte-identical to before; a run over ~8k
  postings drops from ~31s to ~3s of CPU. (This entry originally called the result
  "linear instead of O(n²)". Measured later, it is not: blocking divides the
  constant, not the asymptotic, so cost is still quadratic in postings for a fixed
  company universe — per-posting time rises ~1.5x per doubling. The speed-up is
  real; the label was wrong, and nothing measured the growth curve to catch it.)
- Breadth sources are fetched **in parallel** (like the depth sources); removed the
  pointless cross-host delay between independent providers.
- Keyword scoring scans the fit-weights **once** per posting (was twice).
- Seniority is **kept** in the de-dup key: `Staff` / `Senior` / `Lead` are treated
  as distinct roles instead of collapsing into one.
- Dates are now Eastern Time throughout (fixes off-by-one role ages near midnight).
- Install: use `pipx install git+https://github.com/hawkesj12/job-radar` until a
  PyPI release is published.
- Keyword scoring is faster (tokenize-once + set membership for single-word keywords,
  a first-token prefilter for multi-word ones); output is byte-identical, verified by
  a differential-equivalence test over 20,000 randomized postings.
- Starter watchlist repaired: fixed five dead Greenhouse slugs (→ Ashby / corrected),
  added Harvey / Sierra / LangChain / ElevenLabs — a clean first run with 0 feed errors.
- README now describes what a fresh clone actually does (a starter watchlist + ten
  aggregator feeds, growable via `seed`) instead of overstating out-of-box coverage.
- Store writes use a unique temp file (`mkstemp`) so overlapping runs can't collide.

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

### Removed

- The unimplemented `[scrapers]` extra and its config key (it never did anything).
- Dead code (`util.env`, an unused constant) and the misleading optional-rapidfuzz
  fallback (rapidfuzz is a required dependency).

### Security

- SmartRecruiters no longer hard-codes `?q=AI`; it harvests generically like the other
  ATS sources and lets the relevance gate filter.
- Braintrust pagination only follows a `next` URL that stays on its own host (SSRF guard).
- Watchlist slugs are validated (`[A-Za-z0-9._-]`) before being spliced into ATS URLs.

## [0.1.0]

- Initial public release.
