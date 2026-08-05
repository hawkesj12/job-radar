---
name: lever
display_name: Lever
status: wired
lane: depth
verdict: >
  A documented, unauthenticated, one-request-per-company board API with the best remote
  signal in the depth lane — `workplaceType` is a real enum, not a string to grep. Its
  weakness is not the API but the slug: boards are addressed by a name you cannot guess,
  and every freshness number below is `unknown` because the only board reachable without
  slug discovery is Lever's demo tenant, whose postings are six years old.

auth:
  type: none
  env: []
  signup: null
  notes: GET requires no authentication; only the POST apply endpoint needs a key

license: # a CONTRACT — quoted, not paraphrased
  commercial_use: unclear
  commercial_terms: >
    Lever's own terms of service govern the relationship with its employer customers and
    do not address the public postings API, its consumers, or redistribution of posting
    data at all. The API documentation establishes that GET is public and unauthenticated
    and describes it as the mechanism by which an employer's published jobs appear on
    their own site — it grants access without granting terms. Absent any clause, this is
    `unclear`, and `unclear` is not `allowed`.
  attribution_required: unknown # never addressed
  redistribution: unknown # never addressed
  derivative_works: unknown # never addressed
  cache_policy: none_stated
  on_termination: none_stated
  personal_data: none # employer postings only; the API cannot reach applications or internal roles
  terms_url: https://www.lever.co/legal/terms-of-service/
  docs_url: https://github.com/lever/postings-api
  read_at: 2026-08-03
  read_depth: full # both the ToS and the public API documentation
  verbatim:
    - "All published job postings are also automatically viewable via your Lever-hosted job site."
    - "You can use the HTTP `Accept: application/json` header or `&mode=json` GET parameter to specify the output mode."
    - "Lever will return a `429` status code if your custom job site issues more than 2 application POST requests per second."
    - "does not support cross-origin HTTP requests from sites outside of your company's domains/subdomains"

endpoint:
  base: https://api.lever.co/v0/postings/{slug}?mode=json
  method: GET
  slug_pattern: "api.lever.co/v0/postings/{slug}"

query:
  title_search: false # whole-board fetch; the postings endpoint has no query interface
  location_search: false
  filters: []
  param_validation: ignores_unknown

limits:
  page_size: null # returns the entire board in one response, unpaged
  max_page: null
  reachable_per_query: null # the whole board, whatever its size
  rate_limit: unknown # the documented 429 is for application POSTs, NOT for GETs
  quota: unknown
  concurrency_safe: unknown
  requests_per_company: 1
  cheap_liveness: true # sources.LIVENESS has live_lever

volume:
  advertised: null
  reachable: null # a function of how many boards you can name — see traps
  measured_at: 2026-08-03

freshness:
  median_age_days: unknown # DELIBERATELY unknown — see below
  pct_within_100d: unknown
  date_sorted: unknown
  measured_at: 2026-08-03
  note: >
    The probe measured 2,673 days median against `leverdemo`. That number is discarded
    rather than recorded: leverdemo is Lever's demonstration tenant, holding fictional
    postings created in 2019. It says nothing about how fresh a real Lever board is, and
    writing it here would be the catalog lying with a real measurement.

coverage:
  countries: [unknown] # the demo board reports US; one fictional tenant is not coverage
  us_share: null
  sector_skew: "per-company by construction — the board is whatever that employer posts"

location:
  shape: semi_structured
  primary_path: categories.location
  type: string
  state_available: unknown # "Arlington, TX" parses, but from ONE fictional posting
  state_path: null
  city_path: null
  country_path: country # a separate top-level field, "US" in the captured record
  multi_value: unknown
  multi_delimiter: null
  free_text_fallback: categories.location
  gazetteer_needed: unknown
  notes: >
    `categories.location` was "Arlington, TX" in the captured record — city plus a
    two-letter state, which is the friendliest shape in the depth lane if it holds. It is
    one row from a demo board, so `state_available` stays `unknown` until a real board is
    sampled. A separate top-level `country` field exists and is worth keeping regardless.

remote:
  signal: structured
  path: workplaceType
  rule: "workplaceType in ('remote', 'hybrid', 'on-site')" # captured value: "hybrid"
  reliability: >
    Present as a dedicated field rather than inferred from text — the only depth adapter
    with a first-class workplace type. The full enum was not confirmed from one record.

fields:
  title: { path: text, type: str } # NOT `title` — Lever calls it `text`
  company: null # the board IS the company; the slug is the only company identifier
  body: { path: descriptionPlain, type: str } # `description` is the HTML twin
  posted: { path: createdAt, type: int } # epoch MILLISECONDS — see traps
  url: { path: hostedUrl, type: str } # `applyUrl` is the same posting's apply form
  tags: null
  salary: null
  employment_type: { path: categories.commitment, type: str } # "Regular Full Time (Salary)"
  function: null
  org_unit: { path: categories.department, type: str } # "Customer Success" — the company's own team
  employer_org: null
  seniority: null
  # unmapped keys in the record: additional, additionalPlain, descriptionBody,
  # descriptionBodyPlain, id, lists, opening, openingPlain, workplaceType, country

traps:
  - "`createdAt` is epoch MILLISECONDS. Feeding it to a seconds-based parser yields the year 51188 — it crashed this catalog's own prober before it was caught."
  - "The title field is `text`, not `title`. Every other source in this catalog uses `title`, so a generic mapper silently produces empty titles here."
  - "There are FIVE overlapping body fields — `description`, `descriptionPlain`, `descriptionBody`, `descriptionBodyPlain`, `opening`/`openingPlain`, plus `additional`/`additionalPlain` and a structured `lists` array of qualification bullets. They are not duplicates of each other: `opening` is the intro, `descriptionBody` is a separate paragraph, `lists` holds the bullets. Taking only `description` drops the requirements."
  - "`categories.department` is the company's own org unit, NOT a job family. It must map to `org_unit`; routing it to `function` is the exact mistake _SCHEMA.md documents."
  - "A 404 means the SLUG is wrong, not that the platform is dead. Ten guessed slugs returned seven 404s. Slugs must be mined (discover.py), never guessed."
  - "A 200 with `[]` is a real board with no open roles — `plaid`, `kraken` and `mistral` all answered that way on 2026-08-03. A caller that treats an empty array as a dead board would blacklist live employers."
  - "Do NOT measure anything about Lever against `leverdemo`. It is a fictional demo tenant whose postings are six years old and whose text includes 'you will never find a job better than this one!!!'."
---

# lever

## A real record

Captured 2026-08-03 from `leverdemo`, the only board reachable without slug discovery. Long
text truncated with a marker; rest verbatim. **This is a demonstration posting, not a real
job** — it is included because it is the true shape of the response, and excluded from every
freshness and coverage number above for the same reason.

```json
{
  "id": "33538a2f-d27d-4a96-8f05-fa4b0e4d940e",
  "text": "AbelsonTaylor Writer",
  "categories": {
    "commitment": "Regular Full Time (Salary)",
    "department": "Customer Success",
    "location": "Arlington, TX",
    "team": "…"
  },
  "country": "US",
  "workplaceType": "hybrid",
  "createdAt": 1553186035299,
  "descriptionPlain": "Welcome to the Demo Job Listing for Lever! This is a fictional job created solely for demonstration purposes …[truncated]",
  "description": "<div>Welcome to the <b>Demo Job Listing</b> for Lever! …[truncated, HTML twin of the above]",
  "lists": [
    {
      "text": "Qualifications",
      "content": "<li>be smart</li><li>be very smart</li>"
    },
    { "text": "Duties", "content": "…[truncated]" }
  ],
  "descriptionBodyPlain": "this job is AMAAAAAAAAAAAAZING!\n",
  "additionalPlain": "you will never find a job better than this one!!!\n\n\nLever builds modern recruiting software for teams to s…[truncated]",
  "hostedUrl": "https://jobs.lever.co/leverdemo/33538a2f-d27d-4a96-8f05-fa4b0e4d940e",
  "applyUrl": "https://jobs.lever.co/leverdemo/33538a2f-d27d-4a96-8f05-fa4b0e4d940e/apply"
}
```

**What this record proves that the field table did not.** Three things a mapping table would
get wrong on its own.

The title is under `text`. Nothing else in this catalog does that, so a generic field mapper
keyed on `title` produces a board of blank titles and no error.

`workplaceType: "hybrid"` sits beside the location as a first-class field. Every other depth
adapter makes remoteness something you grep out of a location string; Lever states it. That is
the single most valuable thing this API sends and the current adapter already reads it.

And the body is not one field but seven. `descriptionPlain` is the intro, `descriptionBodyPlain`
is a further paragraph, `additionalPlain` is more still, and `lists` carries the qualifications
and duties as structured bullets. A consumer that maps `description` and stops has silently
dropped the requirements section of every Lever posting — the part a keyword score most wants.

## Why freshness is `unknown` and not 2,673 days

The probe ran cleanly and returned a median posting age of 2,673 days with 1% inside 100 days.
Those numbers are real and they are worthless: `leverdemo` is Lever's demonstration tenant, its
postings were created in March 2019, and one of them says "you will never find a job better than
this one!!!".

Recording that as Lever's freshness would be worse than recording nothing, because it carries
the authority of a measurement. `unknown` with this paragraph attached is the honest state, and
it stays that way until a real board is sampled.

Getting one is harder than it sounds, which is itself a fact about this source. Ten well-known
company names were tried as slugs: seven returned 404 and three — `plaid`, `kraken`, `mistral` —
returned HTTP 200 with an empty array, meaning a real board carrying no open roles today. None
returned postings. That is not evidence that Lever boards are empty; it is evidence that slugs
cannot be guessed, which is exactly why `discover.py` mines them from Common Crawl and then
probes them. The distinction between 404 (wrong slug) and 200-with-`[]` (right slug, no roles)
is the one a caller must not collapse.

## How this was probed

`catalog/_probe.py` on 2026-08-03, one request per second, with steps 2 and 3 skipped — a
whole-board endpoint has no query interface to test and no pages to walk. Ten slug candidates
were tried by hand afterward, one request each. Terms and API documentation read the same day.

```
junk:   control 200 (361 rows) vs junk 200 (361 rows) -> ignores_unknown
fresh:  361 dated rows, median 2673d, 1% within 100d   [DISCARDED — demo board]
body:   median 1016 chars over 361 rows                [demo text, not representative]
slugs:  netflix/brex/ramp/figma/scaleai/anduril/quora/benchling/sardine/attentive -> 404
        plaid, kraken, mistral -> 200 with []
```

**Not checked:** everything that needs a real board — freshness, coverage, whether
`categories.location` reliably carries `, XX`, whether one posting can list several locations,
and the full `workplaceType` enum. The rate limit is `unknown`: the documented 429 governs
application POSTs at 2/second and says nothing about GETs, and bursting to find the GET limit
was not run. Lever's terms of service were read in full and simply do not mention the postings
API, so `commercial_use: unclear` reflects an absence of terms rather than an unread page.
