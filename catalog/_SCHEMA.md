# catalog/ — one profile per job-board API

**What this is.** job-radar reaches many free job APIs. Every one of them lies about something:
The Muse advertises 404,460 listings and hard-caps at 2,000. USAJOBS silently ignores a misspelled
parameter forever. Adzuna returns zero rows for the most common thing a user types. None of that is
in anyone's documentation and all of it was found by probing.

This folder is where that knowledge lives — **one markdown per source**, frontmatter that is
**machine-readable**, so limits and rights are _carried by code_ rather than described in a README
and violated anyway.

```
catalog/
  _SCHEMA.md      this file — the contract, the probe procedure, the vocabulary
  _probe.py       runs procedure steps 1-5 against a live API, emits frontmatter
  _targets.json   per-source array/date/title paths, so the procedure runs identically
  _probe_all.sh   drives _probe.py over every target, SEQUENTIALLY (see the rate note)
  _scaffold.py    probe evidence -> a profile draft; `--check` reports TODOs + YAML health
  _crosscheck.py  diffs INDEX.md against the profiles; `--fix` rewrites drifted cells
  _rollup.py      NOT BUILT — frontmatter -> INDEX.md + job_radar/data/limits.json
  _raw/<name>.json   the captured record, so `## A real record` is never retyped
  _out/<name>.txt    the probe run's output, so `## How this was probed` is evidence
  INDEX.md        hand-maintained until _rollup.py exists — _crosscheck.py keeps it honest
  adzuna.md       one profile per source
  themuse.md
  ...
```

**Fidelity, not opinion.** A profile records what the API _does_. Whether a consumer keeps a field,
caps a body, or maps a category is that consumer's business and does not belong here.

**The bar:** an agent handed only this file and a base URL must produce a correct, comparable
profile — same procedure, same units, same words for the same things.

---

## Frontmatter contract

`_rollup.py` fails on a missing required key. An unmeasured field must be the literal `unknown` —
never a guess, never omitted, never silently dropped.

```yaml
---
name: adzuna # slug, matches the filename
display_name: Adzuna
status: wired | evaluated | rejected | pending-key | no-public-api | dead
#   ^ ONE axis: this source's relationship to the package. Whether a PROFILE exists is
#     not a status — INDEX.md derives that from the file. See "Vocabulary" below.
lane: breadth | depth # mirrors the code registry: sources.DEPTH_ALL / BREADTH_ALL
#   ^ depth = per-company slug board. There is no `keyed` lane — INDEX.md's third table
#     is a derived view of auth.type, and live-fetch capability is read from
#     query.title_search, which is a column in EVERY table.
verdict: one line — why it is in that status

auth:
  type: none | free_key | affiliate | oauth | email-request
  env: [ADZUNA_APP_ID, ADZUNA_APP_KEY]
  signup: https://developer.adzuna.com/
  notes: instant self-serve key

license: # CONTRACT — see "Rights are a contract"
  commercial_use: allowed | trial_only | license_required | prohibited | unclear
  commercial_terms: "14-day trial, then a licence agreement may be required"
  attribution_required: true
  attribution_text: '"Jobs by Adzuna" logo min 116x23px, "Jobs" linked to adzuna.co.uk'
  redistribution: allowed | license_required | prohibited | unclear
  derivative_works: allowed | license_required | prohibited | unclear
  cache_policy: "refresh displayed content within 4 hours" # or none_stated
  on_termination: "remove all acquired data from all pages" # or none_stated
  personal_data: none | contains_pii # GDPR/CCPA exposure this source creates
  terms_url: https://developer.adzuna.com/docs/terms_of_service
  read_at: 2026-08-03 # REQUIRED. terms change; an undated claim is not evidence
  read_depth: full | summary_only
  verbatim: # the deciding clauses, QUOTED not paraphrased
    - "is permitted subject to a 14 day trial period"

endpoint:
  base: https://api.adzuna.com/v1/api/jobs/{country}/search/{page}
  method: GET | POST
  slug_pattern: null # depth lane only, e.g. "{slug}.teamtailor.com/jobs.json"

query:
  title_search: true | false | claimed | unknown
  # DECISIVE: false => harvest-lane only, never live-fetch. `claimed` = the vendor
  # documents a title parameter that could NOT be probed (no key yet) — never write
  # `true` for a claim; INDEX.md marks claimed rows unverified in the keyed table.
  location_search: true # can you ask for a place?
  filters: [what, what_and, where, distance, category, max_days_old]
  param_validation: rejects_unknown_400 | ignores_unknown | unknown
  # ^ PROBE THIS FIRST. It decides what every other measurement here is worth.

limits:
  page_size: 50
  max_page: unknown # null = unbounded; a NUMBER means a hard cap
  reachable_per_query: unknown # page_size * max_page — the real ceiling
  rate_limit: unknown # e.g. "3 req then 429", "1000/hr"
  quota: unknown # e.g. "250 LIFETIME", "250/mo"
  concurrency_safe: unknown
  requests_per_company: null # depth lane only — see Vocabulary
  cheap_liveness: null # depth lane only — see Vocabulary

volume:
  advertised: null # what the API claims
  reachable: unknown # what you can actually GET (incl. fan-out)
  measured_at: 2026-08-03

freshness:
  median_age_days: unknown
  pct_within_100d: unknown
  date_sorted: false # false => you cannot page to the fresh part
  measured_at: 2026-08-03

coverage:
  countries: [US]
  us_share: 1.0 # null if unknown
  sector_skew: "all sectors — the least tech-skewed wired source"

# ── LOCATION — its own block, because it varies more than any other field
#    and it is the one the consumer's filtering depends on.
location:
  shape: structured | semi_structured | free_text
  primary_path: location.area
  type: array_of_string
  state_available: true # can you get a US state WITHOUT a gazetteer?
  state_path: "area[1]"
  city_path: "area[3] at depth 4; area[2] at depth 5"
  country_path: "area[0]"
  multi_value: false # can ONE job list several places?
  multi_delimiter: null # e.g. ";" for greenhouse
  free_text_fallback: location.display_name
  gazetteer_needed: false
  notes: >
    display_name is "City, County" and carries NO state; area[] beside it does.

remote:
  signal: structured | keyword | derived | none
  path: location.area
  rule: 'area == ["US"]'
  reliability: "23 of 50 in sample; verified by eye"

fields: # source path -> what it really is
  title: { path: title, type: string }
  company: { path: company.display_name, type: string }
  body: { path: description, type: string, median_chars: 500 }
  posted: { path: created, type: iso8601 }
  url: { path: redirect_url, type: string }
  function: { path: category.label, type: string } # the JOB FAMILY
  org_unit: null # the company's own team/department
  employer_org: null # the employing organisation — NOT a category
  tags: null
  seniority: null
  salary: { path: [salary_min, salary_max], type: number }
  employment_type: { path: contract_time, type: string }

traps: # the things that cost a session
  - "`where=remote` returns ZERO — `where` is a place hierarchy, not free text."
---
```

---

## Vocabulary — the words `_rollup.py` joins on

`INDEX.md` is generated from these profiles, so a word that means one thing here and another
there is not a style difference — it is a join that silently drops rows. Three rules.

### `status` is one axis, and it is not "does a profile exist"

Status records **the source's relationship to this package**, nothing else. It is a fact, not a
judgement of the source.

| value           | means                                                                       | INDEX icon |
| --------------- | --------------------------------------------------------------------------- | ---------- |
| `wired`         | job-radar ships an adapter; the key appears in `DEPTH_ALL` or `BREADTH_ALL` | 🔵         |
| `evaluated`     | probed live, returns parseable job records, no adapter                      | 🟡         |
| `rejected`      | probed and it works, but something structural makes it unusable — say what  | 🔴         |
| `pending-key`   | real endpoint behind a key, licence, or partner agreement we do not hold    | 🔑         |
| `no-public-api` | probed; returned HTML or 404. **Not proof of absence** — rule 5             | ⛔         |
| `dead`          | shut down or permanently unusable                                           | 💀         |

`rejected` is for a measured structural defect **or a rights bar**, not a preference:
`4dayweek` and `devitjobs` both return clean, deeply pageable JSON with **no description
field anywhere**, so nothing downstream can ever score them; `weworkremotely` returns the
best data in the catalog and its API terms name "a job advertising or job search service" in
the do-not-build list. Both are properties of the source, not opinions about it. "Small",
"non-US", or "tech-skewed" are `coverage:`, and leave a source `evaluated`.

**A licence can reject a source that every measurement says is excellent** — which is the
whole reason `license:` is a contract rather than an observation. When rights are the
deciding factor, lead the verdict with them and quote the clause, because a reader who sees
good numbers beside a `rejected` status will otherwise assume a mistake.

**Look for API terms separately from the terms of service.** There is no convention and the
difference decides the answer: Remotive and Remote OK ship their API terms _inside the JSON
payload_, We Work Remotely puts them on a page its own footer does not link, TechTree puts
them in the main ToS, and Lever's ToS never mentions its API at all. "The ToS says nothing
about the API" is an unfinished search, not a finding — it produced a `unclear` on
`weworkremotely` that the operative document flatly contradicts.

**Whether a profile exists is derived, never written.** `_rollup.py` checks for
`catalog/<name>.md` and renders the INDEX row's name as a link when it finds one. A source can
be `wired` and profiled (adzuna), `evaluated` and profiled (themuse), or wired and unprofiled —
these are independent, and folding them into one column loses the second fact.

### `name` is the adapter key

`name:` must equal the key in `sources.DEPTH_ALL` / `BREADTH_ALL` where one exists — `hn`, not
`hn-algolia` — and equal the filename. It is the join column; a friendly spelling belongs in
`display_name`.

### Every INDEX column is one key

`_rollup.py` reads only these. A column with no key behind it is a column that will go stale.

| INDEX column     | frontmatter key                                 |
| ---------------- | ----------------------------------------------- |
| source           | `name` (+ link if `catalog/<name>.md` exists)   |
| status           | `status`                                        |
| endpoint         | `endpoint.base` / `endpoint.slug_pattern`       |
| `title?`         | `query.title_search`                            |
| `junk params`    | `query.param_validation`                        |
| `req/co`         | `limits.requests_per_company` — depth lane only |
| `cheap liveness` | `limits.cheap_liveness` — depth lane only       |
| `ceiling`        | `limits.page_size` × `limits.max_page`          |
| `body ch`        | `fields.body.median_chars`                      |
| `US`             | `coverage.us_share`                             |
| `rights`         | `license.commercial_use`                        |
| `probed`         | the newest `measured_at` on the profile         |

Two of these are new keys the depth lane needs, and both belong to `limits:`:

```yaml
limits:
  requests_per_company: 1 # depth only. Workday is ≤210: ≤10 list pages + 1 detail call per role
  cheap_liveness:
    true # depth only. Is there a sources.LIVENESS variant, or does a
    # liveness question cost a full board fetch? Ashby is false BY MEASUREMENT
```

### There is no `keyed` lane

`lane:` mirrors the code registry and has exactly two values. `INDEX.md`'s third table is a
**derived view of `auth.type != none`** — everything standing behind a credential — and it is
NOT a lane.

**Auth and queryability are separate axes, and conflating them was a real error here.** That
table was originally derived from `query.title_search` on the assumption that only keyed
sources could answer a per-request query. Probing disproved it: five keyless sources filter by
keyword (`himalayas`, `jobicy`, `braintrust`, `landing.jobs`, `4dayweek`), and three of the
five read `false` until a bug in `_probe.py` was fixed — it scored a filter dead when the probe
term legitimately matched nothing. So `title_search` is a column in every table, never a
table of its own.

The lesson generalises past this one field: **a derived view is only as true as the
measurement it derives from**, and a view built while a measurement was systematically wrong
will look internally consistent right up until the measurement is fixed.

---

## Required prose sections

### `## A real record` — REQUIRED

A genuine captured response, pasted verbatim. Long text truncated with an explicit marker.
**Never hand-written from memory or reconstructed from the `fields:` table.**

It is required because the `fields:` map is an _interpretation_ and the record is the _evidence_,
and on this project the record has contradicted the table every single time:

````markdown
## A real record

Captured 2026-08-03, `what=registered nurse`, US. Description truncated; rest verbatim.

```json
{
  "id": "5826627353",
  "title": "Registered Nurse",
  "company": { "display_name": "Shannon Health" },
  "location": {
    "display_name": "Big Spring, Howard County",
    "area": ["US", "Texas", "Howard County", "Big Spring"]
  },
  "category": {
    "label": "Healthcare & Nursing Jobs",
    "tag": "healthcare-nursing-jobs"
  },
  "created": "2026-08-03T09:55:06Z",
  "salary_min": 48323.49,
  "salary_max": 48323.49,
  "salary_is_predicted": "1",
  "redirect_url": "https://www.adzuna.com/land/ad/5826627353?…",
  "description": "Job Summary Under general supervision, performs …[truncated]"
}
```

**What this record proves that the field table did not.** `display_name` carries no state — the
cause of the consumer's unparseable-location problem — while `area` beside it holds country,
**state**, county, city. The adapter keeps the string and drops the array.
````

### `## How this was probed` — REQUIRED

Date, the exact queries run, and **anything you could NOT check**. An unprobed field marked
`unknown` here is worth more than a plausible number.

---

## The probe procedure — run in this order

The order is not cosmetic. Step 1 decides what steps 2–7 are worth.

1. **Junk-parameter test.** Send `?zzz_not_real=1`. A **400** means every parameter that returns a
   count is real, and probes of this API are trustworthy. **Silently ignored** means a typo'd
   parameter is a permanent no-op and every later result is weaker evidence. Record as
   `param_validation`.
2. **Title search.** Try `q`, `query`, `search`, `keyword`, `title`, `name` **individually** and
   compare each against an unfiltered control. Identical result = no title search. This single
   boolean decides whether the source can ever serve a live per-request fetch.
3. **Page ceiling.** Walk `page` upward until it breaks; **retry the first failure 3×** to
   distinguish a hard cap from throttling. Never trust an advertised total —
   `reachable = page_size × max_page`.
4. **Rate limit.** Issue requests until throttled, or stop at a documented figure. `unknown` is
   fine and common; a guess is not.
5. **Freshness.** Sample pages spread across the whole range, not just page 1. Also record
   `date_sorted` — if false, a consumer cannot page to the fresh part and must cut at ingest.
6. **Fields + location.** Capture a real record. Fill `fields:`, then fill `location:` and
   `remote:` **from the record, not from the docs.**
7. **Rights.** Read the terms, fill `license:`, quote the deciding clauses into `verbatim:`, and
   stamp `read_at`.

---

## Rules

1. **Measured, not documented.** Every number carries a `measured_at`. Where a vendor doc and a
   probe disagree, the probe wins and the disagreement becomes a `traps:` entry.
2. **`unknown` is a valid and required answer.** Twelve of fifteen wired sources have unknown rate
   limits today. Writing `unknown` makes that visible; omitting it hides it.
3. **Advertised ≠ reachable.** The Muse says 404,460 and caps at 2,000.
4. **A 200 is not a pass.** BambooHR, Jobvite and Homerun all returned 200 with 17–171 KB of
   marketing HTML. Parse it or it does not count.
5. **A 404 is not proof of absence** — it means the pattern or the slug was wrong. Teamtailor
   looked dead until the fourth slug answered.
6. **A count is not a result.** Blanking Adzuna's `where` takes 0 → 55,052 rows at 2% relevance.
   Sample and look.
7. **Never probe from inside a normalizer.** "Adzuna sends no remote signal" was measured on
   already-adapted rows that had dropped the field. Hit the raw endpoint.
8. **`title_search: false` is a routing decision, not a footnote.**

---

## Why `function`, `org_unit` and `employer_org` are three keys

Because five adapters put five different things in one `department` field, and a consumer that
pours them into a single column gets 5,050 distinct values including employer names. Measured
2026-08-03:

| adapter    | source field                                        | what it really is |
| ---------- | --------------------------------------------------- | ----------------- |
| usajobs    | `DepartmentName` → "Department of Veterans Affairs" | **the employer**  |
| braintrust | `level` — **GONE from the API as of 2026-08-03**    | **a seniority**   |
| adzuna     | `category.label` → "IT Jobs"                        | a job function    |
| greenhouse | `departments[0].name`                               | an org unit       |
| ashby      | `team` / `department`                               | an org unit       |

The braintrust row carries a second lesson. `sources.py:1048` still reads `j.get("level", "")`,
and `level` appears in **0 of 20** rows returned today — the union of keys across the whole feed
does not contain it. So that mapping silently yields an empty string on every row: the adapter is
not mis-classifying a field any more, it is reading one that no longer exists. **A field table
written from a captured response goes stale the same way code does**, which is why every profile
records `measured_at` and why `## A real record` must be a real capture rather than a copy of the
table.

So a profile records **what the field is**, not what the vendor named it: a job family goes to
`function`, the company's own team to `org_unit`, the employing organisation to `employer_org`,
and a seniority to `seniority`. Writing `org_unit: DepartmentName` for USAJOBS because the word
matches is the exact mistake this split exists to prevent.

---

## Location deserves its own block

Location is where sources diverge most, and it is the field a consumer's filtering stands on.
Measured across the wired sources on 2026-08-03:

| source     | shape           | state without a gazetteer?                                    |
| ---------- | --------------- | ------------------------------------------------------------- |
| adzuna     | structured      | **yes** — `area[1]` is a real US state in 246/246 sampled     |
| usajobs    | structured      | **yes** — `PositionLocation[].CountrySubDivisionCode`         |
| ashby      | structured      | **yes** — `address.postalAddress.addressRegion`, 657/737      |
| greenhouse | free text       | partial — full state NAMES, never `, XX`; 143/808 multi (`;`) |
| lever      | semi-structured | `categories.location` + a real `workplaceType` field          |

Three of five already carry a structured state that the current adapters discard. So
`state_available` is the field that decides whether structured location is a _mapping change_ or a
_parsing project_ for that source — and the answer differs per source. Record it honestly.

Also record `multi_value`: one Greenhouse posting can list `Bengaluru, India; Mumbai, India`. A
consumer that assumes one place per row will silently mis-file 18% of that board.

---

## Rights are a contract, and they decide more than the limits do

A rate limit tells you how fast you may pull. **`license:` tells you whether you may keep it, show
it, or ever earn from it** — and unlike a rate limit, violating it produces no 429. Nothing breaks.
You find out later, from a lawyer.

So `license:` is a **contract**, not an observation, and carries three extra rules:

- **`read_at` is required.** Terms change silently and without versioning.
- **Quote, never paraphrase.** "Commercial use needs a licence" is a summary; the sentence that
  says so is the artifact. Summaries drift toward what the reader hoped it said.
- **`unclear` is the right answer more often than you would like, and it is not `allowed`.** The
  Muse's terms contemplate third-party apps but do not obviously speak to ad-supported sites. That
  is `unclear`; writing `allowed` would be inventing permission.

**Who a clause BINDS matters as much as what it says.** Two tests, applied in order, and
skipping the first produces false positives in both directions:

1. **Does it reach us? Find the contract-formation EVENT, not the party label.** A party name
   is something you infer from; the formation clause settles it outright. Ashby's is explicit —
   "effective as of the date Customer first completes a purchase with Ashby (via an Order or
   otherwise) **or logs into the Service**" — and reading a public board is neither, so its
   `BY USING THE SERVICE, CUSTOMER AGREES` preamble never fires. Rippling's looks
   access-binding — "BY ACCEPTING THIS AGREEMENT OR USING ANY OF THE RIPPLING SERVICES, YOU
   AGREE TO BE BOUND" — until the next sentence scopes it to "all **customers** … paid
   subscribers, prospective subscribers … Users, Account Administrators, Authorized
   Representatives", and the agreement then defines an Authorized Representative as "an
   authorized accountant, broker, HR/IT consultant or other representative **of Customer**".
   That is why Rippling's "may not extract data … as part of any data aggregation service" —
   the most on-point clause in the depth lane — still leaves it `unclear`. Where a document
   names its formation event, that is the answer; where it only implies a party, you are
   inferring, and you should say so.

   **Three outcomes, not two.** `does_not_reach` is a finding. `binds_by_access` is weaker and
   should be recorded as its own state: a clause of the form "by accessing this site you agree"
   is browsewrap, and whether calling a public JSON endpoint constitutes assent is genuinely
   unsettled — the reasonable-notice question cuts both ways. Every `prohibited` call in this
   catalog passes step 1 only under the access-binds reading, so none of them is as solid as a
   `does_not_reach`. Do not let a future reader mistake one for the other.

2. **Does it name this activity, or is it generic anti-scraping boilerplate?** "Do not build a
   competitive product" is boilerplate in nearly every SaaS agreement and cannot by itself
   make a source `prohibited`. What earns `prohibited` is a clause naming the thing:
   We Work Remotely's "a job advertising or job search service", landing.jobs' "copy, use,
   display or distribute any information obtained from the Platform", jobspresso's "any public
   display (commercial or non-commercial)", devitjobs' database-right claim over the listing
   corpus, TechTree's "enrich external databases; automate scraping".

Two careful readers disagreed about TechTree on exactly this point, which is recorded in its
profile. **Record the disagreement rather than resolving it by preference** — a contested
reading that a reader can see both sides of is more useful than a confident one they cannot
check.

**Why this lives in job-radar:** rights attach to _the source_, exactly like the rate limit and the
field paths. Every consumer inherits them and none can work them out from the JSON. A source's
licence is the most consequential thing about it that its API response cannot tell you.

**The lane split, measured 2026-08-03:**

| lane                                    | rights posture                                                                     |
| --------------------------------------- | ---------------------------------------------------------------------------------- |
| ATS boards (greenhouse/lever/ashby/…)   | public, unauthenticated, published by employers _so that_ they are aggregated      |
| keyed search APIs (adzuna/usajobs)      | **commercially constrained** — written permission needed past trial/registered use |
| SERP scrapers (google_jobs via SerpApi) | contested independent of your use — active Google litigation                       |
| public-sector feeds (CareerOneStop/NLx) | free, but licence agreement + mandatory attribution                                |

**None of this constrains job-radar itself.** The package is Apache-2.0 software that calls APIs;
the constraints attach to the _data_ a consumer stores and displays.

---

## Contracts vs observations — they rot differently

| kind            | keys                                                                      | behaviour                                                                                                    |
| --------------- | ------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| **contract**    | `auth` `license` `endpoint` `query` `limits` `location` `remote` `fields` | code branches on these; they must be true. Mostly self-healing — a wrong `max_page` throws a 400 you notice. |
| **observation** | `volume` `freshness` `coverage`                                           | drift by nature. **No code may read these.** They go stale honestly with their `measured_at`.                |

A stale `median_age_days` and a stale `max_page` must never look alike — only one is a bug.

**The anti-rot mechanism, required:** `_rollup.py` regenerates `job_radar/data/limits.json` from
the frontmatter, and a test regenerates and diffs it against the committed copy, failing on
mismatch. Without that test the frontmatter and the artifact are two writable copies of one number
and the catalog is doc-rot with extra steps. For **wired** sources, prefer generating the
frontmatter _from_ the adapter so a live number never has two homes at all.
