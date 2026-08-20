# job-radar

**A CLI and Python library that turns nineteen job-board APIs into one record shape.**

Every job board answers the same question in a different vocabulary. Greenhouse calls the body `content`, Lever calls it `descriptionPlain`, Ashby calls it `descriptionPlain` but nests compensation three levels down, and Workday doesn't return a body at all until you ask a second endpoint. job-radar knows all nineteen dialects, asks each one correctly, and hands you back a single normalized record — de-duplicated across sources, with the employer's own apply link preferred over an aggregator's redirect.

It ships with a working job-search tool on top of that engine. But the engine is the product: harvesting, normalizing, de-duplicating, and knowing what each API will and won't do.

"One record shape" means every adapter emits the same keys with the same types, and that an unknown is always `None` rather than a plausible-looking guess. It does not mean every source fills every key — what each one actually sends is measured per source in `catalog/`, and what is still missing is named in [Known limits](#known-limits-of-the-record-shape).

```
pipx install job-radar                      # or: pip install job-radar
job-radar init                              # write a starter config + watchlist here
job-radar                                   # harvest → ranked shortlist.csv
```

Runs on Linux, macOS, and Windows, Python 3.10 or newer. Apache-2.0.

## The record it produces

Every adapter, whatever it was handed, emits the same dict:

| field                                                   | what it holds                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| ------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `title` · `company` · `url`                             | the role, the employer, the apply link (direct-to-employer first)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `posted`                                                | `YYYY-MM-DD`, or `None` — never a vendor's arbitrary string                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| **`posted_basis`**                                      | `stated` (the vendor sent a date) · `relative` (computed from "Posted 26 Days Ago"). Provenance, NOT accuracy — `stated` means they sent one, not that it's right                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| **`expires`** · **`harvested_at`**                      | the vendor's deadline where it sends one; when WE fetched the row                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `text`                                                  | the full description, HTML stripped and whitespace collapsed — with one line break kept per bullet or paragraph, because a posting's list items are the only sentence boundaries it has                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| **`sections`**                                          | the posting's own structure, as spans into `text`: `[{type, header, start, end}]`. `type` is one of ten (`responsibilities` · `requirements` · `about_company` · `benefits` · `compensation` · `location_travel` · `eeo_legal` · `metadata` · `apply_cta` · `fraud_warning`) or `null` when the employer's header is just prose, which is most of them. `header` is the employer's own words, kept so a misclassification is fixable without re-harvesting. **`null` = no body to read · `[]` = a body with no headers.** A section that could not be located carries `type` and `header` and no span — never a guessed one. **Effectively Greenhouse-only; see Known limits** |
| `salary`                                                | the vendor's own display string, kept verbatim                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| **`employment_type`**                                   | a closed set — `FULL_TIME` · `PART_TIME` · `CONTRACTOR` · `TEMPORARY` · `INTERN` · `VOLUNTEER` · `PER_DIEM` · `OTHER` · `None`. Nineteen vendors spell these eight ideas nineteen ways                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| **`employment_type_raw`**                               | what the vendor actually said, verbatim. `None` when they said nothing — never back-filled from the normalized value                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `source`                                                | which adapter produced this record                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| **`category`**                                          | the job family — "Healthcare & Nursing Jobs"                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| **`team`**                                              | the company's own team — "Engineering - Pipeline"                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| **`parent_company`**                                    | the umbrella org, where a source distinguishes one                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| **`title_root`** · **`title_level`**                    | the matchable role with decoration stripped; `I`–`IV`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| **`salary_min`** · **`salary_max`**                     | what an employer COMMITTED to. Floats, or `None` — a zero is dropped, because RemoteOK sends `0` on all 100 rows of its feed                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| **`salary_currency`** · **`salary_period`**             | ISO currency; `year` · `month` · `week` · `day` · `hour` · `fixed`. A period is **never guessed** — `65` and `135000` are both valid numbers, so a wrong period makes every aggregate silently wrong                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| **`salary_basis`**                                      | `stated` (real numeric fields) · `parsed` (read out of free text)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| **`salary_estimated_min`** · **`salary_estimated_max`** | a MODEL's guess, in separate keys on purpose. Adzuna predicts 93% of its salaries; merging those into `salary_min` would make a guess indistinguishable from a commitment                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| **`city` · `state` · `country`**                        | structured geography. **`country` is ISO alpha-2 or `None`** — normalized at the boundary, never a display name. `state` is a US two-letter code where the place is in the US — canonicalized at the boundary, so `California` and `CA` never both appear — and, where a source sends one, its own subdivision name elsewhere (`Greater London`), which has no code to map to. Note the asymmetry: a subdivision a source states is kept, but one only inferable from a location string (`Toronto, ON, CA`) is left `None` rather than invented — outside the US there is no canonical form to put there. Filter it together with `country`: a two-letter value is only a US state when `country == "US"` (`CT` is Catalonia on a Spanish row). Derived from the location string when a source sends no structured fields, and left `None` when it cannot be read with confidence. |
| **`remote`**                                            | `True` / `False` / **`None`** — see below                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| **`remote_type`**                                       | `remote` · `hybrid` · `onsite` · `None` — a bool cannot say hybrid                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| **`remote_basis`**                                      | how we decided: `stated` (the row's own vendor field) · `board` (a remote-only board, so every row is remote by scope) · `location` · `title` (the title says so and the location is silent — on Greenhouse and Lever, which send no remote field, this is often the only evidence in existence) · `text` (weakest: the body **asserts this role** is remote, which is a stricter test than the word appearing)                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| **`remote_areas`**                                      | **WHERE a remote worker may sit** — a list of ISO 3166-1 alpha-2 country codes (`["US","CA"]`), or ISO 3166-2 for a stated US state (`["US-TX"]`, never a bare `TX`, because seven codes are both a US state and an ISO country). **Three states, all different:** `null` = the posting said nothing · `[]` = it said **anywhere** · non-empty = these places. STATED boundaries only — a country parsed out of an office address (`Munich, Germany`) is not an eligibility claim and stays `null`; that geography lives in `city`/`state`/`country`. Per schema.org's `applicantLocationRequirements`, which models this concept, it records where applicants may **apply from** and is explicitly **not** a citizenship or work-visa claim. |
| **`remote_regions`**                                    | multi-country groupings the posting named — `EMEA`, `APAC`, `LATAM`, `AMERICAS`, `EUROPE`, `ASIA`, `AFRICA`, `OCEANIA`, `NORTH AMERICA`, `NORTHERN AMERICA`, `TIMEZONE` and a few aliases (17 in all; `vocab.REMOTE_REGION_TOKENS` is the list). A closed token set, and **never a country code**: whether a region includes a given country is a policy question only the employer can settle, so it is answered by your filter, not stored in the row. Deliberately not expanded into country lists — that would be a confidently wrong enumeration in a filterable column. |
| **`remote_scope_raw`**                                  | the vendor's own words, verbatim — `"Americas, Europe"`, `"USA, EMEA"`. Kept because components alone cannot be audited when a mapping turns out wrong, and it is the only thing that lets a corrected parser re-run over historical rows. |
| **`tags`** · **`seniority`**                            | skills list; the source's own level string, verbatim                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| **`seniority_basis`**                                   | `stated` (the source has a level field) · `title` (parsed out of the title)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| **`title_qualifiers`**                                  | the decoration stripped off `title_root` — `["applied"]` from "AI Engineer, Applied"                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| **`locations`**                                         | every place ONE posting names, each with `raw`/`city`/`state`/`country`/`url`. Always the same five keys                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| **`direct_apply`**                                      | does this URL reach the EMPLOYER, or an aggregator that bounces you onward? The product's whole differentiator                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| **`source_extra`**                                      | the third tier: fields ONE source sends that no other can, kept verbatim. Read it by key; never index it                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| `location`                                              | the raw location string, kept alongside the parsed fields                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| `department`                                            | **deprecated** — see below. Removed at 1.0.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |

**`None` is not `False` and not `""`.** `remote: None` means the source did not say,
which is a different fact from "this role is not remote". That distinction is the
whole point of the contract — it is what lets a consumer write `WHERE remote IS NOT
NULL` and mean it, instead of inheriting a guess. Every unknown is `None`.

**Every derived value carries its basis.** `remote` is a real boolean on some sources
(SmartRecruiters' `location.remote`, Ashby's `isRemote`, Lever's `workplaceType`) and
inferred from prose on others. `remote_basis` says which, so a consumer that
disagrees with the inference can override it rather than re-deriving everything.

That is now true of the _gate_ as well, and it was not before: a row admitted because its
title said "Remote" used to come out with `remote_type` and `remote_basis` both `None` —
indistinguishable from a row nobody classified. An unknown arrangement stays
`None`, and it is deliberately distinct from `onsite`: employer boilerplate ("we are a fully
remote company", which appears verbatim on postings in three different countries at once)
and a bare mention of remoteness with no claim attached both come back unknown, because
neither says _this role_ is remote.

**`department` is deprecated because it was four different things.** It held an org
unit on Greenhouse and Ashby, a job function on Adzuna (`IT Jobs`), a seniority level
on Braintrust, and _the employer_ on USAJOBS (`Department of Veterans Affairs`) — so
a consumer pouring it into one column got a category dimension it could not filter
on. It is still emitted byte-identically; use `category` / `team` /
`parent_company` / `seniority` instead.

Type safety is enforced at one boundary rather than trusted from ~500 third parties: every field above is coerced to `str` inside `harvest` before anything reads it, because a JSON `null` arriving as `None` used to crash the whole harvest on the first `.lower()`.

`posted` is validated, not passed through. Anything that isn't date-shaped becomes `""`, because eighteen adapters route third-party strings into that column and a blank date is safer than a vendor's ten arbitrary characters.

The engine then adds `score`, `signals`, `sources` (the set of adapters that saw this role), and `dedup_key`.

## Three lanes, nineteen adapters

**Depth — 8 adapters, keyless.** Greenhouse, Lever, Ashby, SmartRecruiters, Workable, Workday, Rippling, Teamtailor. One request per company for six of them; Workday and Rippling additionally fetch one detail call per role, because neither returns a description on its list endpoint. These are the employer's own applicant-tracking system, so the record is canonical: the real apply URL, the full description, the accurate department. You see a role the hour it posts. Workday is the enterprise one — it reaches the manufacturers, insurers, hospitals, municipalities, and national labs that never appear on the startup boards.

**Breadth — 8 keyless adapters, whole-market.** Remotive, Jobicy, Arbeitnow, RemoteOK, Himalayas, Hacker News "Who is Hiring", Braintrust, The Muse. The Muse is the least tech-skewed source here — 11% tech titles when measured — which is why it is carried despite having no title search.

**Keyed search — 3 adapters, title and location queryable.** Adzuna, USAJOBS, and Google for Jobs via SerpApi. This is the only lane that can serve a per-request live fetch, and the only one that reaches every field and location rather than remote-tech. All three degrade to a printed notice and an empty list when their key is unset, so the tool always runs free.

**Liveness — the cheap question.** Six of the eight depth adapters have a second, minimal variant that answers "does this board exist and have roles?" without downloading the board. It matters more than it sounds: answering that with the full adapter cost **210 requests** for a Workday tenant, against **1** for the liveness call, and roughly twenty times the bytes for a Greenhouse board (figures under Legal & etiquette). Callers never pick — `liveness_for(ats)` hands back the cheap variant where one exists and transparently falls back where it doesn't.

## Building the company universe

The depth lane's bottleneck was never fetching. It was **supply**: every per-company fetch needs a slug you already know.

`job_radar.discover` solves that two ways, and both end at the same gate.

**Mine.** Common Crawl has already crawled the web and published a queryable URL index, so every company hosting a public board is already in it as a `boards.greenhouse.io/{slug}` URL. One HTTP call per ATS enumerates them. We aren't crawling anyone — we query an index someone else built and published for this purpose.

**Resolve a name.** For companies the index never saw, `from_names(["Cloudflare", "DoubleVerify"])` generates candidate slugs and tests them.

**Then probe — and this is what makes bulk mining safe.** Every candidate is hit against its real ATS API and kept only if it returns at least one live role. A dead, churned, or misparsed slug returns nothing and costs one cheap request.

**And where the ATS will say who owns a board, check identity too.** A probe proves `jobs.lever.co/capital` is a real board with real jobs. It does not prove it belongs to Capital One — it doesn't. Liveness and identity are different questions, and conflating them files a stranger's jobs under a real employer, invisibly. Greenhouse is the one ATS that answers the second question, so the riskiest slug guesses are generated only for it.

`probe` reports _why_ each candidate failed, split into terminal (`refused`, `wrong-owner`, `unsupported`) and retryable (`throttled`, `missing`, `empty`, `error`). The split is load-bearing: 429 means slow down, not go away, and a caller that blacklists on it will discard hundreds of good employers in one bad run.

## Use it as a library

The engine doesn't touch your disk. `harvest` takes companies as **data** and _returns_ everything it produced, including companies it discovered along the way — persisting them is the caller's job.

```python
from job_radar import config, engine

config.set_active(config.load_config("job-radar.yaml"))   # optional; defaults apply otherwise

rows, discovered, errors = engine.harvest(companies=[
    {"name": "Anthropic", "ats": "greenhouse", "slug": "anthropic"},
    # Workday needs a three-part key instead of a bare slug:
    {"name": "Example Corp", "ats": "workday", "slug": "examplecorp",
     "host": "wd1", "site": "External"},
])
```

That signature is deliberate. The engine used to append discovered companies straight into the caller's `watchlist.json`, which made a library function silently write a file it didn't own — and left a store-backed consumer with nowhere to put them. Whoever owns the universe decides.

`errors` is a list, never an exception: one dead aggregator must not sink a harvest. Pass `--strict` on the CLI to make any source failure a nonzero exit instead.

## The source catalog

Every one of these APIs behaves in some way its documentation won't tell you. The Muse advertises 404,460 listings and hard-caps at page 99, so 2,000 is all you can reach. USAJOBS silently ignores a misspelled parameter, forever — your typo is a permanent no-op with no error. None of that is written down anywhere; all of it was found by probing.

The mirror of that is worth stating too, because it's the more common failure: **most "the API is broken" findings turn out to be our own bug.** `where=remote` returns zero rows from Adzuna — not because Adzuna is wrong, but because `where` is a place hierarchy and "remote" isn't a place. That's a caller error, not an API defect — and it's live in this codebase right now, queued for the next release.

`catalog/` is where both kinds of knowledge live — one profile per source, with machine-readable frontmatter covering auth, endpoint, query capabilities, measured limits, field paths, and **license as a contract**: the deciding clauses quoted rather than paraphrased, with a `read_at` date, because terms change silently and a summary drifts toward what the reader hoped it said.

Twenty-two profiles are complete and dated, with every source that's been probed listed in `catalog/INDEX.md` whatever came of it — including the ones rejected on rights, which keep their profile so the evidence doesn't leave with the source. Two checkers run in CI: one that every profile parses and carries the keys the schema marks required, and one that INDEX.md still agrees with the profiles. The schema and the probe procedure are the durable parts; the profiles accumulate.

The rule that orders everything else: **probe the junk parameter first.** An API that 400s on `?zzz_not_real=1` validates its input, so every parameter that returns a count is real and your measurements are trustworthy. An API that silently ignores it will accept your typo forever and tell you nothing. That single boolean decides what every other number about that source is worth.

## How much it retrieves

Every source has a ceiling, and most of them are not obvious from the API's own
documentation. job-radar pages to a **bounded, configurable** depth rather than an
accidental one — the caps are visible knobs, not hidden in a URL.

| source                       | per run                         | why it stops there                                                                                                 |
| ---------------------------- | ------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| depth boards                 | the whole board, up to each cap | one request each — except SmartRecruiters (pages at 100), and Workday/Rippling (one request per role for the body) |
| himalayas                    | ~1,000 newest                   | browse is date-ordered, so a bounded lane still gets the freshest rows                                             |
| themuse                      | ~1,960 (20 slices x 5 pages)    | the vendor caps each slice at page 99; all 20 slices verified disjoint                                             |
| adzuna / usajobs             | 3 pages/query                   | vendor page sizes of 50 and 500                                                                                    |
| google_jobs                  | 1 page/query                    | metered — SerpApi's free tier is 250 searches a **month**                                                          |
| remotive / remoteok / jobicy | one request                     | that is the entire corpus                                                                                          |

Every depth ceiling lives in one config block, `sources.harvest_depth` — nine keys
covering SmartRecruiters, Workday, Himalayas' two lanes, The Muse, HN, and the two
per-role detail passes. Each still reads an environment variable of the same name in
caps (`WORKDAY_MAX_PAGES`, `HN_THREADS`, …), which is what the config defaults to, so
nothing that worked before stopped working. Paging for the keyed APIs stays with its
credentials: `sources.usajobs.max_pages` and `sources.adzuna.pages`.

**SerpApi is the one metered source, so it has a quota guard.** `google_jobs` spends
`pages × title_queries` searches per run — six a run at the shipped defaults, which is
180 of a 250/month free tier at daily cadence. SerpApi reports exhaustion as a JSON
error rather than an HTTP failure, so an overrun would degrade into a printed notice
while the shortlist quietly shrank. Before spending anything, the adapter checks the
remaining quota against SerpApi's free `/account` endpoint and holds
`sources.google_jobs.reserve` (default 25) back, so an overrun cannot consume the end
of the month. It says what it dropped rather than trimming silently.

**Two adapters buy job descriptions one request at a time** — Workday and Rippling
return no body on their list endpoint. Those are gated: the relevance filter runs
against the titles the list already returned, so a harvest never pays for a
description it is about to discard. Measured across ten enterprise employers, that is
every role for roughly half the requests the previous truncated version cost.

## Known limits of the record shape

Stated plainly, because they're the difference between "normalized" and "actually comparable":

- **Structured location is per-source, not universal.** Adzuna, USAJOBS, SmartRecruiters and Ashby send real `city`/`state`/`country`; Lever sends `country`; The Muse is parsed from its `"Waco, TX"` display string. Greenhouse, RemoteOK, Remotive, HN and Braintrust are free text and stay unparsed, because the parser refuses anything it cannot read with confidence — `"Taiwan, Taipei"` is country-first and `"Toronto, ON"` names a province, and guessing either produces a permanently wrong row. `location` always carries the raw string, so nothing is lost, but **do not assume `state` is populated.** `country` and `state` are normalized to ISO alpha-2 across every source; a posting naming several places keeps the rest in `locations`.
- **`department` still exists.** Deprecated, emitted byte-identically, removed at 1.0. It is the one contract field whose meaning varies by source; `category` / `team` / `parent_company` / `seniority` are the replacements.
- **SmartRecruiters returns no body** through its list endpoint, so `text` and `salary` are empty for that adapter.
- **`sections` carries a classification, not an assertion — and its precision is unmeasured.** The
  `type` is a regex over the employer's header text; published figures say how often a type is
  *found*, never how often it is *right*. Two buckets are known to be loose: `eeo_legal` matches a
  bare `commitment to` / `privacy` / `sponsorship`, and `location_travel` matches a bare `remote`,
  so "Lead Remote Teams:" files as location. The employer's own `header` is kept beside the type
  precisely so you can disagree with it.
- **`sections` is effectively a Greenhouse field.** Greenhouse sends HTML on 100% of postings; every other adapter sends plain text and gets `[]`. Measured across 478 employers: `responsibilities` on 92% of them and `requirements` on 94%, but **that is 92% of the Greenhouse employers, not of the market**. A consumer that builds ranking or summarisation on `sections` is building it on roughly two thirds of the corpus and none of the local/non-tech lane — adzuna and google_jobs, the two local sources, return nothing here. A handful of Ashby, HN, Remotive and Arbeitnow postings carry HTML and do get sections; that is a property of those employers, not of those adapters. One more `null`: a row that was in the store but not re-harvested this run carries `sections: null` from the store path, which is a third state on top of the two above — true of every contract field, and visible here because this is the first with explicit two-state semantics.
- **Workday truncates at 500 roles per employer** by default (`WORKDAY_MAX_PAGES` 25 × 20 per page), silently, in Workday's own ordering rather than newest-first. Raise `WORKDAY_MAX_PAGES` to widen it. Its list endpoint sends no `location` field, so the city is recovered from the posting path; a role whose path carries neither a location nor a requisition id keeps an empty location.

## Structured output for a database

`shortlist.csv` is the human artifact. For a machine consumer, ask for NDJSON — one
JSON object per line, streaming, appendable, and typed, so `remote: null` survives as
null instead of collapsing into an empty CSV cell.

```
job-radar --format ndjson --all > jobs.ndjson
```

Rows go to **stdout**, the run manifest and progress to **stderr**, so the redirect
above produces a clean file with no status lines to grep out. `--all` emits every
tracked role rather than just the surfaced shortlist, which is usually what a store
wants — it does its own filtering.

```json
{"id":"a3f9c21","dedup_key":"anthropic|ai engineer|san francisco ca|4020123",
 "title":{"raw":"AI Engineer, Applied","root":"AI Engineer","level":null,"qualifiers":["applied"]},
 "company":"Anthropic","parent_company":null,"category":"Engineering","team":"Applied AI",
 "seniority":"Senior","seniority_basis":"stated","tags":["python","llm"],
 "location":{"raw":"San Francisco, CA","city":"San Francisco","state":"CA","country":"US","all":[...]},
 "remote":{"is_remote":false,"type":"onsite","region":null,"basis":"stated"},
 "posted":"2026-08-01","posted_basis":"stated","expires":null,
 "employment_type":"FULL_TIME","employment_type_raw":"FULL_TIME",
 "salary":{"raw":null,"min":300000.0,"max":405000.0,"currency":"USD","period":"year",
           "basis":"stated","estimated_min":null,"estimated_max":null},
 "url":"https://job-boards.greenhouse.io/anthropic/jobs/4020123","direct_apply":true,
 "text":"Build agentic LLM systems.","source":"greenhouse","score":41}
```

`title`, `location`, `remote` and `salary` are nested objects that each keep the raw
vendor value beside the parsed parts, so a consumer who disagrees with our parse can
re-read the original rather than losing it. Everything the contract knows is on the
wire — including `text`, the full description, which is the entire input to the score.

Each run also emits **one manifest object to stderr** describing the run itself —
row counts per source, which adapters failed, how many companies were discovered, and
the filter config that produced it. A store fed only rows cannot answer "why did
Tuesday have four hundred fewer jobs"; that information used to exist only as text
printed to a terminal and then lost.

## The job-search tool on top

The CLI is one consumer of the engine, and a complete one.

```
job-radar                    # harvest, score, dedup, update the shortlist
job-radar list               # show the current shortlist
job-radar apply <id>         # mark a role applied (it stops resurfacing)
job-radar dismiss <id>
job-radar seed greenhouse    # bulk-add companies from Common Crawl
job-radar --version
```

**Scoring** is a transparent weighted keyword model you fully control in `job-radar.yaml`, with BM25 length normalization so a long, thorough description isn't punished for its length. Each keyword counts **once** however many times it appears, so a posting can't buy rank by repeating "AI" forty times. Weights are yours and hand-set — there's no IDF, so a keyword is worth what you say it's worth. Tuned for recall by design.

**De-duplication** merges the same role across sources into one entry, and — just as importantly — _doesn't_ merge roles that only look alike. A level (`II` vs `III`), a trailing qualifier (`, Ads`, `(EU)`), or two different job ids on one board mean two openings. The bias is deliberate: a wrong merge deletes a role you wanted and hides the evidence, while a wrong split shows a row you can ignore. When the marks disagree, it splits.

**The store** is one upserted `shortlist.csv` tracking `first_seen`, `status`, and every score. `apply`/`dismiss` are sticky — those rows persist even after the role leaves the market, so your application history is never lost. Writes are atomic and lock-serialized on both POSIX and Windows.

**Optional LLM re-ranking.** With an API key, the top of the list is re-scored for semantic fit (0–100) with a one-line why-it-fits note. Off by default, stdlib HTTP only, one request per run, bounded to `rerank_top_n`. It's a precision layer over what already cleared the bar, not a recall layer.

## Configuration

Everything is in one file — `job-radar.yaml`. Set your **target titles**, tune the **fit-weight keywords**, and adjust **filters** (`max_age_days`, `min_score`, remote-only, excluded locations, and `allowed_scopes` — *where* a remote worker may sit, which is a different question from whether the role is remote at all). `job-radar init` writes a fully commented starter config into your folder with every knob on its default; that file is the reference (it ships at [`job_radar/data/job-radar.example.yaml`](job_radar/data/job-radar.example.yaml)).

Out of the box the defaults are tuned for **remote software/AI** roles, because the keyless sources are remote-tech boards. Nothing about the engine is tech-specific: change `signal_titles` + `fit_weights` to your field's language, set `remote_only: false` and a real `location`, and turn on the keyed lane (Adzuna, USAJOBS, Google for Jobs) where the whole market lives.

A config file found in the current directory is honored, but **not** its `llm.base_url` or `*_key_env` keys — those choose which host a request goes to and which secret rides along, which is enough to POST your `ANTHROPIC_API_KEY` to a stranger's server. Naming the file with `--config` is the opt-in.

## Legal & etiquette

This is a **personal job-search tool and a harvesting library**, not a data-resale product, and it's built to be a good citizen:

- **Default sources are official, public, no-auth APIs**, used as their vendors document them — Greenhouse, Lever, and Ashby publish these endpoints _for_ programmatic use. Consuming a public API is distinct from scraping behind a login, and job-radar does none of the latter.
- **Every loop is capped, and the caps are visible.** Remotive gets exactly **one** request per run (its own notice advises four per _day_). Paged sources pause between requests, and every page budget is a named, documented setting rather than a number buried in a URL — the nine ceilings live together in `sources.harvest_depth`, each still overridable by an env var of the same name in caps. There is no unbounded walk against anyone's API anywhere in the codebase. It sends a self-identifying `User-Agent`.
- **Depth costs vary, and the expensive ones are the ones without a body on the list endpoint.** Most ATS boards are one request per company. SmartRecruiters pages 100 at a time. **Workday and Rippling cost one request per _role_** for the description — so those two run the relevance filter against the list titles first and fetch bodies only for what survives, which is what keeps a wide watchlist affordable.
- **It asks for the smallest thing that answers the question.** Checking whether a board exists costs one request, not a full download — for a Workday employer that's 1 instead of 210, and for a large Greenhouse board roughly 280 KB instead of 5.6 MB (measured 2026-07-31; the ~20x ratio is the durable part). Discovery is where a tool like this can be rude at scale, so that's where the restraint matters most.
- **Attribution is a condition of access on five sources, not a courtesy.** Remote OK, Remotive, Himalayas, Arbeitnow and Adzuna each grant API access on stated terms, and Remote OK and Remotive both say plainly that they will **revoke** it if you don't credit them. job-radar credits the sources a run actually used in its own terminal output — and because a library can't discharge a _display_ obligation for whatever ends up showing the jobs, `--format ndjson` carries the full terms in the run manifest under `attribution`, keyed by the same `source` value on every row. **If you build on this, render it.** Two details worth knowing before you do: Remote OK requires the link back be followable (explicitly _not_ `rel=nofollow`), and Remotive separately forbids submitting its jobs to third-party job sites such as Google Jobs or LinkedIn Jobs. Adzuna is the one that a text credit doesn't satisfy — it wants a branded label and a sized logo on each advert, which is why the manifest flags it `row_link_suffices: false`. The quoted terms and the date each was read live in `catalog/`; the code that carries them is `job_radar/attribution.py`.
- **API keys** are read from environment variables only and never logged or committed. Adzuna's and SerpApi's keys travel in the request URL per their API designs.
- **Google for Jobs is reached through SerpApi**, a commercial SERP API — job-radar does not scrape Google itself. Know what that lane is, though: SerpApi scrapes Google's results, it is not licensed by Google, and Google sued it. In July 2026 a federal court dismissed Google's DMCA claims, holding that plain search results — URLs, snippets, index data — aren't works protected by copyright. The dismissal was partial: Google was given leave to amend the portion covering results that contain copyrighted content, and is doing so. The case is unresolved. It's off unless `SERPAPI_KEY` is set, and it's the one lane here whose legal footing is contested rather than merely constrained.
- **The rights split matters more than the rate limits.** ATS boards are published by employers _so that_ they're aggregated. The keyed search APIs are commercially constrained. Violating a licence produces no 429 — nothing breaks, and you find out later. That's why `catalog/` records terms as quoted contract text.

One honest exception to "used as documented": **Workday**. Its CxS endpoint is public and no-auth — the same one Workday's own careers-site widget calls — but Workday doesn't publish third-party API documentation for it the way Greenhouse and Lever do. It's a public endpoint used as its own front end uses it, which is a weaker claim than the others here, and worth knowing before you point it at hundreds of employers.

## Honest limits

The superpower — harvesting a role the hour it posts, direct from the employer's ATS — is strongest in tech, because Greenhouse/Lever/Ashby are tech-company systems. Other fields lean on the keyed lane. And the truly local, unposted, word-of-mouth job isn't in any structured feed, so no tool reaches it. Everything that _is_ posted online, this can find.

> Upgrading from 0.2.x: `job_radar.store` is now `job_radar.shortlist`.

## License

Apache-2.0.
