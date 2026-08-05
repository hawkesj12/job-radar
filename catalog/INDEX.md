# INDEX — the job-board API registry

**Compiled:** 2026-08-03. Every source job-radar knows of, what it is, and what state it is in.

> **What this file is.** A reference, not a work order. It records **what each source is** — lane,
> auth, endpoint, and whatever has been measured. It deliberately does **not** rank sources or
> recommend an order; which ones matter depends entirely on what a given consumer is building.
>
> Once `_rollup.py` exists, every column here is generated from the profiles' frontmatter — so the
> words in this file are the words in `_SCHEMA.md`, not synonyms for them. Until then this is
> hand-maintained. Everything marked **measured** was probed live on the date shown.

**Status** — a fact about the source's relationship to this package, not a judgement of it. One
axis; the vocabulary and the rules for choosing between these live in `_SCHEMA.md` → Vocabulary.

|                      |                                                                              |
| -------------------- | ---------------------------------------------------------------------------- |
| 🔵 **wired**         | job-radar ships an adapter — the key is in `DEPTH_ALL` / `BREADTH_ALL`       |
| 🟡 **evaluated**     | probed live, returns parseable job records; no adapter                       |
| 🔴 **rejected**      | probed and it works, but a structural defect makes it unusable               |
| 🔑 **pending-key**   | real endpoint behind a key, licence, or partner agreement we do not hold     |
| ⛔ **no-public-api** | probed; returned HTML or 404. **Not proof of absence** — `_SCHEMA.md` rule 5 |
| 💀 **dead**          | shut down or permanently unusable                                            |

**A [linked](_SCHEMA.md) source name has a profile.** Having one is independent of status and is
derived from the file existing, never written down — a source can be wired and profiled, evaluated
and profiled, or wired with no profile yet. Names match the adapter key, not the marketing name.

**The columns.** Each is one frontmatter key (`_SCHEMA.md` → Vocabulary maps them). Three carry
more weight than the rest:

- **`title?`** — `query.title_search`. The single boolean deciding whether a source can answer a
  per-request live fetch or must be harvested wholesale. `unknown` here means the adapter never
  sends a title parameter and nobody has probed whether the API accepts one — it does **not** mean
  no.
- **`junk params`** — whether `?zzz_not_real=1` 400s or is silently swallowed. This is probe step 1
  because it decides what every other number on that row is worth. Measured on every profiled
  source, and the answer is nearly unanimous: **20 of 22 silently ignore an unknown parameter**,
  only jobicy and adzuna reject it. So on almost every row here, a typo'd parameter is a
  permanent silent no-op — which is exactly how three sources were recorded as having no title
  search when they have one.
- **`rights`** — `license.commercial_use`, the one thing here that can't be worked out from a
  response and produces no error when violated.

**`unknown` is a measurement, `—` is not applicable.** 21 of 22 licences have now been read and
dated; `rights: unknown` survives on nodesk only, where the terms could not be
reached at all, and it means UNREAD rather than permissive. Same for `probed` — every current row
was probed during the 2026-08-03 compile, and `never` means exactly that.

**The rights picture, which is the most consequential thing in this file:** 6 `prohibited`,
14 `unclear`, 1 `trial_only`, 1 `unknown`. Nothing is `allowed`. One of the six prohibited —
`usajobs` — ships in the package today; `techtree` was REMOVED on 2026-08-04 (see its row below),
and `weworkremotely` was the headline new adapter in the 0.7.0 plan before its API terms were read.

_(These four counts are recomputed from the profiles, which are the authority. They read
6/13/1/2 until 2026-08-05, when a panel review recounted them — the exact drift
`_crosscheck.py` now guards structurally.)_

---

## Depth lane — per-company boards (`{slug}` addressed)

Free and keyless throughout, and one request per company **except Workday**, which costs up to 210
— the largest cost difference in this lane and the reason `req/co` is a column.

**`cheap liveness`** is whether `sources.LIVENESS` has a variant answering "does this board exist
and have roles?" in one request instead of fetching the whole board. Callers never choose; the ones
marked no fall back to counting a full fetch.

| source                                                                                                                              | status           | req/co         | cheap liveness      | rights  | endpoint pattern                                         | probed     | measured                                                                                                                                                                                                                                                                                    |
| ----------------------------------------------------------------------------------------------------------------------------------- | ---------------- | -------------- | ------------------- | ------- | -------------------------------------------------------- | ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| greenhouse                                                                                                                          | 🔵 wired         | 1              | yes                 | unclear | `boards-api.greenhouse.io/v1/boards/{slug}/jobs`         | 2026-08-03 | 808 jobs on `databricks`. Location is free text, full state **names**, never `, XX`; **143/808 multi-location** via `;`. The one ATS reporting a board owner — `verify_identity` works here only                                                                                            |
| ashby                                                                                                                               | 🔵 wired         | 1              | **no — full fetch** | unclear | `api.ashbyhq.com/posting-api/job-board/{slug}`           | 2026-08-03 | 737 on `openai`. `address.postalAddress.addressRegion` structured in **657/737**. No cheap liveness variant exists, by measurement                                                                                                                                                          |
| lever                                                                                                                               | 🔵 wired         | 1              | yes                 | unclear | `api.lever.co/v0/postings/{slug}?mode=json`              | 2026-08-03 | 388 on `leverdemo` — a DEMO board, so freshness/coverage are unmeasurable and recorded `unknown`. `categories.location` + a real `workplaceType` enum, and a top-level `country`. Title is `text`; `createdAt` is epoch MILLISECONDS                                                        |
| smartrecruiters                                                                                                                     | 🔵 wired         | 1              | yes                 | unknown | `api.smartrecruiters.com/v1/companies/{slug}/postings`   | 2026-08-03 | adapter sends `limit=100` and does not page — a board over 100 roles is silently truncated. It reads structured `location.{city,region,country}` + a `remote` boolean, so a state is available without a gazetteer (not yet probed against a live board)                                    |
| workable                                                                                                                            | 🔵 wired         | 1              | yes                 | unknown | `apply.workable.com/api/v1/widget/accounts/{slug}`       | 2026-08-03 | —                                                                                                                                                                                                                                                                                           |
| workday                                                                                                                             | 🔵 wired         | **≤210**       | yes                 | unknown | `{slug}.{host}.myworkdayjobs.com/wday/cxs/…`             | 2026-08-03 | needs a 3-part key (slug/host/site); no global search. Lists 20/page (`limit>20` is a hard 400), capped at 10 pages, **plus one detail call per role** — `WORKDAY_FETCH_DETAILS=0` drops it to ≤10                                                                                          |
| rippling                                                                                                                            | 🔵 wired         | **1 + 1/role** | yes                 | unclear | `api.rippling.com/platform/api/ats/v1/board/{slug}/jobs` | 2026-08-03 | 752 jobs, JSON array, keyless                                                                                                                                                                                                                                                               |
| teamtailor                                                                                                                          | 🔵 wired         | 1              | **no — full fetch** | unclear | `{slug}.teamtailor.com/jobs.json`                        | 2026-08-03 | JSON Feed with `_jobposting`. Confirmed on `apotea` (9). Nordic-heavy. **Terms READ 2026-08-04** at `/en/terms-and-conditions/` (not footer-linked; `/en/terms/` 404s) — a CUSTOMER agreement formed by an Order Form, so it does not reach a third party reading a public career-site feed |
| personio                                                                                                                            | 🟡 evaluated     | 1              | —                   | unknown | `{slug}.jobs.personio.de/xml`                            | 2026-08-03 | XML. **429 after ~3 requests.** EU mid-market                                                                                                                                                                                                                                               |
| breezy                                                                                                                              | 🟡 evaluated     | 1              | —                   | unknown | `{slug}.breezy.hr/json`                                  | 2026-08-03 | vendor demo returns 3; real customer slugs 403                                                                                                                                                                                                                                              |
| recruitee                                                                                                                           | ⛔ no-public-api | —              | —                   | unknown | `{slug}.recruitee.com/api/offers/`                       | 2026-08-03 | 404 on 5 real slugs — pattern unconfirmed                                                                                                                                                                                                                                                   |
| bamboohr · jazzhr · homerun · jobvite · pinpoint · comeet · freshteam · workstream · polymer · dover · icims · successfactors · gem | ⛔ no-public-api | —              | —                   | unknown | —                                                        | 2026-08-03 | returned marketing HTML or 404                                                                                                                                                                                                                                                              |
| zoho recruit                                                                                                                        | 🔑 pending-key   | unknown        | —                   | unknown | `recruit.zoho.com/recruit/v2/Job_Openings`               | 2026-08-03 | 401 — OAuth                                                                                                                                                                                                                                                                                 |

---

## Breadth lane — keyless aggregators and whole-board feeds

| source                                             | status           | title?              | junk params          | body ch  | US      | rights         | endpoint                                                             | probed     | measured                                                                                                                                                                                                                                                                                                                                                                        |
| -------------------------------------------------- | ---------------- | ------------------- | -------------------- | -------- | ------- | -------------- | -------------------------------------------------------------------- | ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [themuse](themuse.md)                              | 🔵 wired         | **no**              | **silently ignores** | 5,065    | unknown | unclear        | `themuse.com/api/public/jobs`                                        | 2026-08-03 | **page-99 hard cap** (advertises 20,223); ~36,060 via 19-category fan-out; **11% tech titles**; not date-sorted; 48% within 100d                                                                                                                                                                                                                                                |
| weworkremotely                                     | 🔴 rejected      | no                  | **silently ignores** | 7,247    | unknown | **prohibited** | `weworkremotely.com/remote-jobs.rss` + 8 category feeds              | 2026-08-03 | **API terms name "a job advertising or job search service" in the do-not-build list**, prohibit "saving, or storing our data", and require applying to route through weworkremotely.com. Rejected on rights. The data is the best measured: 603 unique from 9 requests, 97% within 100d. Company is prefixed into the title                                                     |
| remotive                                           | 🔵 wired         | no                  | **silently ignores** | 9,646    | unknown | unclear        | `remotive.com/api/remote-jobs`                                       | 2026-08-03 | longest bodies measured                                                                                                                                                                                                                                                                                                                                                         |
| jobicy                                             | 🔵 wired         | **yes**             | **rejects (400)**    | 6,259    | unknown | unclear        | `jobicy.com/api/v2/remote-jobs?count=100`                            | 2026-08-03 | adapter sends only `count`; any title parameter unprobed                                                                                                                                                                                                                                                                                                                        |
| arbeitnow                                          | 🔵 wired         | no                  | **silently ignores** | 6,606    | unknown | unclear        | `arbeitnow.com/api/job-board-api`                                    | 2026-08-03 | 175/page                                                                                                                                                                                                                                                                                                                                                                        |
| remoteok                                           | 🔵 wired         | no                  | **silently ignores** | 2,052    | unknown | unclear        | `remoteok.com/api`                                                   | 2026-08-03 | index 0 is metadata, not a job. **`location` truncated to a bare comma in 75/100** ("Cambridge, ") — the full value survives at the head of `description`. `salary_min`==0 in 98/100; `apply_url`==`url` in 100/100; feed is mojibake-corrupted. Attribution must be a FOLLOWED link                                                                                            |
| himalayas                                          | 🔵 wired         | **yes**             | **silently ignores** | 6,200    | unknown | unclear        | `himalayas.app/jobs/api/search?limit=20`                             | 2026-08-03 | **advertised 100,157 / reachable 8,020** — a hard ceiling at page 401, and `totalCount` never shrinks to warn you. `q` filters. Every reachable page is fresh (medians 0/0/0/0/1d) though NOT date-sorted. `limit` is clamped to 20; the search endpoint pages by `page`, the browse endpoint by `offset`                                                                       |
| braintrust                                         | 🔵 wired         | **yes**             | **silently ignores** | unknown  | unknown | unclear        | `app.usebraintrust.com/api/jobs/?limit=20`                           | 2026-08-03 | `level` is GONE from the response (0 of 20 rows, measured 2026-08-05); the dead mapping was removed                                                                                                                                                                                                                                                                                                                           |
| techtree                                           | 🔴 rejected      | no                  | **silently ignores** | 2,768    | unknown | **prohibited** | `jobs.techtree.dev/api/public-job-posting?visibility=job_board_only` | 2026-08-03 | **REMOVED from the package 2026-08-04.** Carries PII — every row's `delivery_owner` names an individual — and **60/76 postings are anonymised** ("TechTree's client"), collapsing unrelated employers in dedup. Stalest breadth source (45d median) and only 24/76 remote. Terms also read as prohibiting this use; that reading is contested and both sides are in the profile |
| hn                                                 | 🔵 wired         | n/a — thread search | unknown              | unknown  | unknown | unknown        | `hn.algolia.com/api/v1/search_by_date`                               | 2026-08-03 | "Who is Hiring" threads, loose `COMPANY / ROLE / …` convention. Algolia searches threads, not postings                                                                                                                                                                                                                                                                          |
| 4dayweek                                           | 🔴 rejected      | **yes**             | **silently ignores** | **none** | unknown | unclear        | `4dayweek.io/api/jobs`                                               | 2026-08-03 | **no documented public API, and the terms forbid automated access without written permission** — fails the repo's own admission rule. Also **no body anywhere**: a detail endpoint exists (`/api/jobs/{slug}`) and returns no prose either. Otherwise excellent: `q` filters at 68%, 23,236 rows, but NOT date-sorted (per-page medians 0/79/5/55/46d)                          |
| workingnomads                                      | 🟡 evaluated     | no                  | **silently ignores** | 4,530    | low     | unclear        | `workingnomads.com/api/exposed_jobs/`                                | 2026-08-03 | 28 rows, no paging, no total. `tags` is a comma-separated STRING not a list; `url` is a redirect hop, not the employer. Terms bar building a competitive product                                                                                                                                                                                                                |
| devitjobs                                          | 🔴 rejected      | no                  | **silently ignores** | **none** | 0       | **prohibited** | `devitjobs.com/api/jobsLight`                                        | 2026-08-03 | **no description field anywhere** — the longest string in a record is a 137-char redirect URL. Unscoreable. 1,574 rows, entirely Canadian. Carries structured `actualCity`/`stateCategory`/`postalCode`/`latitude`/`remoteType`                                                                                                                                                 |
| landing.jobs                                       | 🟡 evaluated     | **yes**             | **silently ignores** | 2,025    | 0/50    | **prohibited** | `landing.jobs/api/v1/jobs`                                           | 2026-08-03 | 50 rows, bodies via `role_description`                                                                                                                                                                                                                                                                                                                                          |
| jobspresso                                         | 🟡 evaluated     | no                  | **silently ignores** | 6,733    | unknown | **prohibited** | `jobspresso.co/?feed=job_feed`                                       | 2026-08-03 | 10 items                                                                                                                                                                                                                                                                                                                                                                        |
| nodesk                                             | 🟡 evaluated     | no                  | **silently ignores** | unknown  | unknown | unknown        | `nodesk.co/remote-jobs/index.xml`                                    | 2026-08-03 | 10 items (RSS)                                                                                                                                                                                                                                                                                                                                                                  |
| remote.co · wellfound · builtin · idealist · eures | ⛔ no-public-api | —                   | —                    | —        | —       | unknown        | —                                                                    | 2026-08-03 | 404                                                                                                                                                                                                                                                                                                                                                                             |
| cryptojobslist · nofluffjobs                       | ⛔ no-public-api | —                   | —                    | —        | —       | unknown        | —                                                                    | 2026-08-03 | 404; 405 (POST-only, Polish market)                                                                                                                                                                                                                                                                                                                                             |

---

## Keyed and gated sources — everything behind a credential

**A derived view, not a third lane.** What unites these rows is `auth.type != none`: a key, a
licence agreement, or a partner arrangement stands between us and the data. Each still belongs
to a code lane — `adzuna`, `usajobs` and `google_jobs` all run in `BREADTH_ALL` — and is listed
here rather than above to keep one row per source.

> **This table was previously titled "live-fetch" and derived from `query.title_search`. That
> was wrong, and probing is what showed it.** When only the keyed sources appeared searchable,
> auth and queryability looked like the same axis. They are not: **five keyless sources filter
> by keyword** — `himalayas`, `jobicy`, `braintrust`, `landing.jobs` and `4dayweek` — and three
> of those five were recorded as `false` until a probe bug was fixed. Live-fetch capability is
> now read from the **`title?` column, which appears in every table**, not from this one.

**The sources that can answer a per-request query** (`title_search: true`, measured):
`adzuna` · `usajobs` · `himalayas` · `jobicy` · `braintrust` · `landing.jobs` · `4dayweek`.
Only the first two need a key — but note `landing.jobs`, `jobspresso` and `4dayweek` are barred
on rights, so the practically usable live-fetch set is smaller than the capability set.

Rows marked **(claimed)** carry a title parameter the vendor documents but we could not probe,
because we hold no key. That is not the same as a measured `true`.

| source                                                                                | status         | auth                            | junk params          | ceiling                               | rights     | probed     | measured                                                                                                                                                                                        |
| ------------------------------------------------------------------------------------- | -------------- | ------------------------------- | -------------------- | ------------------------------------- | ---------- | ---------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [adzuna](adzuna.md)                                                                   | 🔵 wired       | free key, instant               | **rejects (400)**    | 50/page, cap unknown                  | trial_only | 2026-08-03 | `where` is a place hierarchy, `where=remote` → 0 · `what_and=remote` → 84% relevant · `area[1]` is a real US state in 246/246 · bodies capped at 500 ch by Adzuna · attribution required        |
| [usajobs](usajobs.md)                                                                 | 🔵 wired       | free key                        | **silently ignores** | 500/page, cap unknown                 | prohibited | 2026-08-03 | `RemoteIndicator=True` verified · `JobCategory[]` carries OPM occupational series codes · structured `PositionLocation[]` · US-only, 100% federal                                               |
| google_jobs **(claimed)**                                                             | 🔵 wired       | SerpApi key                     | unknown              | **250 searches LIFETIME** (free tier) | unknown    | never      | **never run — no key set.** 1 search per title. `ltype=1` is the WFH filter and the adapter never sets it                                                                                       |
| jobdataapi **(claimed)**                                                              | 🟡 evaluated   | free key                        | unknown              | unknown                               | unknown    | 2026-08-03 | **6,491,156 total / 4,087,597 US** · bodies ~5,000 ch · structured `cities`/`states`/`countries`/`has_remote` · bare endpoint keyless (100 rows) but **every parameter 403s**; rate-limits hard |
| jooble **(claimed)**                                                                  | 🔑 pending-key | free GUID, instant              | unknown              | unknown                               | unknown    | never      | POST + JSON, global, all sectors, title+location queryable per docs                                                                                                                             |
| careeronestop / NLx **(claimed)**                                                     | 🔑 pending-key | email + licence agreement       | unknown              | unknown                               | unknown    | never      | US DOL. State job banks **and** private employers, all sectors. DOLETA + Minnesota DEED attribution required                                                                                    |
| findwork **(claimed)**                                                                | 🔑 pending-key | free key                        | unknown              | unknown                               | unknown    | 2026-08-03 | 401 keyless; small, tech-focused                                                                                                                                                                |
| dice · talent.com · startup.jobs · remoterocketship · arbeitsagentur.de **(claimed)** | 🔑 pending-key | partner                         | unknown              | unknown                               | unknown    | 2026-08-03 | all 403                                                                                                                                                                                         |
| reed.co.uk **(claimed)**                                                              | 🔑 pending-key | free key                        | unknown              | unknown                               | unknown    | never      | **UK only**                                                                                                                                                                                     |
| careerjet **(claimed)**                                                               | 🔑 pending-key | free key, **`affid` mandatory** | unknown              | unknown                               | unknown    | never      | the click routes through Careerjet                                                                                                                                                              |
| whatjobs **(claimed)**                                                                | 🔑 pending-key | key on request                  | unknown              | unknown                               | unknown    | never      | unknown programme. 1,000 req/hr                                                                                                                                                                 |

---

## Dead

Shut down, but still listed in "top free job API" articles: **GitHub Jobs** (2021),
**Stack Overflow Jobs** (2022), **Authentic Jobs**.

---

## Never probed

Named in research, never tested. No claim is made about any of them.

**ATS:** Greenhouse Job Board API v2 · iCIMS partner · Taleo/Oracle · SAP SuccessFactors partner ·
Paylocity · UKG · Rippling _customer_ boards (only the vendor's own was probed) · Workday tenant
discovery

**Aggregators:** ZipRecruiter partner · Monster partner · CareerBuilder partner · SimplyHired ·
Snagajob · Handshake · YC Work at a Startup · Otta · Torre · Jobg8 · Appcast · Joblift · Jobrapido ·
Trovit

**Public sector:** individual **state job banks** (many publish feeds independently of NLx) ·
`USAJOBS HistoricJoa` · NEOGOV / GovernmentJobs municipal portals

**Non-US:** Adzuna's other `{country}` endpoints (already wired, unused) · Reed (UK) ·
Arbeitsagentur (DE) · JobBank Canada · Seek (AU)

---

## Adding to this file

A source earns a row once it has been **probed**, not when it has been read about. Follow the
procedure in `_SCHEMA.md` — the step order matters, because the junk-parameter test decides what
every later measurement on that API is worth. Steps 1–5 are deterministic and belong in
`_probe.py`; hand-rolling them per source yields numbers that cannot be compared.

Probing concurrently requires a shared rate governor: several of these sources are being probed
_for_ their 429 behaviour, and parallel requests make a real rate limit indistinguishable from
self-contention.
