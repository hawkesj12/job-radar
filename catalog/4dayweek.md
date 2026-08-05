---
name: 4dayweek
display_name: 4 Day Week
status: rejected
lane: breadth
verdict: >
  Technically the best-behaved unwired source measured — a real title search, 23,236 rows,
  deep paging that works, and the most structured location schema in the whole catalog. Rejected for two independent reasons, either of which alone is fatal: no
  posting body exists anywhere, in the list endpoint OR the detail endpoint, so nothing
  downstream can score it; and there is no documented public API, while the terms prohibit
  automated extraction without prior written permission. The second reason fails this
  repo's own admission rule and is the one that settles it.

auth:
  type: none
  env: []
  signup: null
  notes: >
    Keyless — but keyless is not the same as permitted. `4dayweek.io/api/jobs` is an
    undocumented endpoint the site's own front end uses, not a published developer API.

license: # a CONTRACT — quoted, not paraphrased
  commercial_use: unclear
  commercial_terms: >
    The terms never address commercial use of job data, because they never contemplate a
    third-party API at all. What they DO say is that automated access to the Service
    requires prior written permission, which covers this endpoint by its plain wording.
    So the operative question is not "may we sell it" but "may we call it" — and the
    stated answer is not without asking.
  attribution_required: unknown # never addressed
  redistribution: unknown # never addressed
  derivative_works: unknown # never addressed
  cache_policy: none_stated
  on_termination: none_stated
  personal_data: none # employer postings only
  terms_url: https://4dayweek.io/terms
  read_at: 2026-08-03
  read_depth: full
  verbatim:
    - "Scraping, crawling, harvesting, or using any automated means to access, collect, or extract data from the Service without our prior written permission."
    - "The Service contains content provided by third parties, including employer-submitted job postings, company profiles, and content sourced from external career pages. Such third-party content is the responsibility of the party that provided it."

endpoint:
  base: https://4dayweek.io/api/jobs
  method: GET
  slug_pattern: null
  detail: https://4dayweek.io/api/jobs/{slug} # returns {job, similar_jobs}; still no body

query:
  title_search: true # MEASURED — `q=nurse` returned 25 rows, 68% with "nurse" in the title
  location_search: unknown
  filters: [q, page]
  param_validation: ignores_unknown

limits:
  page_size: 25
  max_page: 925 # walked; page 926 returned no rows, confirmed on retry
  reachable_per_query: 23125
  rate_limit: unknown # not probed — see "How this was probed"
  quota: unknown
  concurrency_safe: unknown

volume:
  advertised: 23236 # the payload's own `total`
  reachable: 23125 # 25 x 925 — advertised and reachable agree here, which is rare
  measured_at: 2026-08-03

freshness:
  median_age_days: 14
  pct_within_100d: 1.0
  date_sorted: false # MEASURED across 5 pages and non-monotonic — see traps
  measured_at: 2026-08-03

coverage:
  countries: [US, GB, multi]
  us_share: null # 39 of 75 sampled LOCATIONS were US; not the same as 39 of 75 rows
  sector_skew: "all sectors, but filtered to employers offering a 4-day or reduced week"

location:
  shape: structured
  primary_path: locations
  type: array_of_object
  state_available: false # the KEY exists; the DATA almost never does — see notes
  state_path: locations[].state
  city_path: locations[].city
  country_path: locations[].country
  multi_value: true # `locations` is an array with an `is_primary` flag
  multi_delimiter: null
  gazetteer_needed: false # lat/long ship with every location
  free_text_fallback: null
  notes: >
    The richest location schema of any source here — city, state, country, continent,
    latitude, longitude, and a PER-LOCATION `work_arrangement`, so one posting can be
    remote in one place and on-site in another. But the schema is not the data: across 75
    rows sampled from pages 5, 40 and 200, 39 locations were in the United States and
    exactly ONE carried a state ("Dublin, California"). US rows are overwhelmingly
    nationwide-remote, expressed as a country plus the geographic centre of the US
    (39.83, -98.58). A consumer filtering by US state would get roughly 3% coverage from
    this source, which is why `state_available` is false despite `state_path` existing.

remote:
  signal: structured
  path: work_arrangement
  rule: "work_arrangement == 'remote'"
  reliability: "present on every row AND on every entry inside locations[]"

fields:
  title: { path: title, type: str }
  company: { path: company_name, type: str }
  body: null # THE defect — see traps
  posted: { path: posted, type: int } # epoch SECONDS
  url: null # only `slug`; a URL must be built from it
  tags: { path: skills, type: list } # detail endpoint only, as [{id, name, slug}]
  salary: null
  employment_type: { path: schedule_type, type: str } # e.g. "4_day_week"
  function: { path: category, type: str } # a job family: "marketing"
  org_unit: null
  employer_org: null
  seniority: { path: level, type: str } # e.g. "entry" — already normalized
  # unmapped keys in the record: company, company_id, id, is_expired, work_life_score

traps:
  - "There is NO body. Not in the list endpoint and not in the detail endpoint — `/api/jobs/{slug}` returns `{job, similar_jobs}` with more structure (contract_type, hours_per_week_min/max, skills[], role) and still not one string over 150 characters. Anything that scores on description text gets nothing here."
  - "`/api/jobs/{id}` 404s; the detail endpoint keys on `slug`, not `id`. The list row carries both, and picking the wrong one looks like a dead endpoint."
  - "The detail response nests the posting under a `job` key and echoes an EMPTY `slug` inside it — so a round-trip through the detail endpoint loses the identifier you used to get there."
  - "**It is NOT date-sorted, despite an early measurement saying so.** Per-page medians run 0d, 79d, 5d, 55d, 46d across pages 0/231/462/693/925 — non-monotonic. A freshness cut has to happen at ingest; you cannot page to the fresh part. The first probe compared only the first and last sampled page, whose ends sloped the right way while the middle was noise."
  - "`state` exists in the schema and is populated for about 1 in 39 US locations. Reading the schema and concluding that structured state filtering works would be wrong by an order of magnitude."
  - "US remote rows carry latitude 39.83, longitude -98.58 — the geographic centre of the United States, not a real workplace. Plotting these puts every nationwide-remote job in rural Kansas."
  - "`company.inserted` and `company.updated` are `0001-01-01T00:00:00Z`, a zero value rather than a date. Parsing them yields year 1."
  - "Keyless is not the same as permitted. The endpoint is undocumented and the terms prohibit automated access without prior written permission."
---

# 4dayweek

## A real record

Captured 2026-08-03 from the list endpoint, first row. Verbatim and complete — no truncation
was needed, which is itself the finding.

```json
{
  "id": "019fc806-1679-7bed-8872-85703d72dc01",
  "title": "Communications Support Intern",
  "slug": "communications-support-intern-at-humanitarian-openstreetmap-team-8198c32b",
  "company_name": "Humanitarian OpenStreetMap Team",
  "company_id": "019b779a-6e68-70fb-9a44-49b4add2656e",
  "work_arrangement": "remote",
  "locations": [
    {
      "continent": "Africa",
      "latitude": 8.78,
      "longitude": 21.09,
      "work_arrangement": "remote",
      "is_primary": true
    }
  ],
  "posted": 1785767270,
  "schedule_type": "4_day_week",
  "category": "marketing",
  "level": "entry",
  "is_expired": false,
  "company": {
    "id": "019b779a-6e68-70fb-9a44-49b4add2656e",
    "name": "Humanitarian OpenStreetMap Team",
    "slug": "humanitarian-openstreetmap-team",
    "logo_url": "https://media.4dayweek.io/files/1767278265000_36e54528f4373e95.jpg",
    "hires_worldwide": false,
    "inserted": "0001-01-01T00:00:00Z",
    "updated": "0001-01-01T00:00:00Z"
  },
  "work_life_score": 93
}
```

**What this record proves that the field table did not.** The whole record fits on one screen,
and that is the defect. Every other source in this catalog needed its body truncated to be
pasted here; this one has nothing to truncate. A field table listing `body: null` states the
fact, but seeing the complete record is what makes it obvious that no amount of mapping work
recovers a description that was never sent.

It also shows two things worth having that most sources do not send: `level` is an already
normalized seniority ("entry"), and `category` is a clean job family ("marketing") rather than
an org unit or an employer name. If a body ever appeared, this source would be a strong
candidate on those two fields alone.

## Why it is rejected, in the order the reasons matter

**1. The terms prohibit automated access, and there is no public API to grant an exception.**
The terms of service never mention an API for third parties. They do say that "using any
automated means to access, collect, or extract data from the Service without our prior written
permission" is prohibited. `4dayweek.io/api/jobs` is the endpoint the site's own front end
calls — keyless, but undocumented and ungranted. This repo's rule is that a new source must be
a documented public API, and this is not one. That alone disqualifies it, independent of
anything measured below.

**2. No body exists anywhere.** Confirmed against both endpoints, not assumed from the list
response. The detail endpoint returns strictly more structure and still no prose.

Everything else about it is good, which is the frustrating part: `q` genuinely filters (68%
relevance), the payload's own `total` of 23,236 agrees with the 23,125 that paging actually
reaches — the only source measured where advertised and reachable agree — and the median row
is 14 days old with 100% inside 100 days.

## How this was probed

`catalog/_probe.py` on 2026-08-03, one request per second. Terms read the same day and quoted
verbatim above. Detail-endpoint and location sampling done by hand afterward, also at one
request per second.

```
junk:  control 200 (25 rows) vs junk 200 (25 rows)
title: control: 25 rows, 0% already match 'nurse'
title: q=nurse -> 200, 25 rows, 68% relevant <- FILTERS
title: filters on: ['q']
pages: last page returning rows: 925 (first failure at 926)
fresh: median 14d, 100% within 100d
sorted: per-page medians p0 0d · p231 79d · p462 5d · p693 55d · p925 46d -> NOT sorted
body:  no prose field found in any sampled row
detail: /api/jobs/{id} -> 404; /api/jobs/{slug} -> 200 {job, similar_jobs}, longest string 36 chars
locations: 75 rows across pages 5/40/200 -> 39 US locations, 1 carrying a state
```

**Not checked:** the rate limit stays `unknown` — bursting until a 429 was not run against any
source, and least of all one whose terms say automated access needs permission. Also unchecked:
whether `q` searches a body as well as the title (there is no body, so the question is moot
here), and whether an authenticated or partner API exists that would supply descriptions, since
the terms point to written permission rather than to a product.
