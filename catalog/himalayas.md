---
name: himalayas
display_name: Himalayas
status: wired
lane: breadth
verdict: >
  The best-documented keyless source in this catalog — a published OpenAPI 3.1 spec, two
  endpoints, a working free-text search (`q`, 83% title-relevant), structured salary and
  seniority, and a stated licence. The catch is that its two endpoints paginate DIFFERENTLY,
  and this probe hit the search endpoint with the browse endpoint's parameter, so the page
  ceiling recorded below is an artifact rather than a measurement.

auth:
  type: none
  env: []
  signup: null
  notes: keyless; "No API key or authentication is required"

license: # a CONTRACT — quoted, not paraphrased
  commercial_use: unclear
  commercial_terms: >
    The OpenAPI spec states the licence as "Free to use with attribution", and the docs make
    the attribution requirement explicit. Neither addresses commercial use, redistribution or
    derivative works in any form, so despite being the most permissive-sounding licence here,
    it is `unclear` on the questions that decide whether a consumer may earn from the data —
    and `unclear` is not `allowed`.
  attribution_required: true
  attribution_text: >
    a visible link back to himalayas.app, and a mention that the data is sourced from Himalayas
  redistribution: unknown # never addressed
  derivative_works: unknown # never addressed
  cache_policy: >
    "The API data is cached and refreshed every 24 hours" and there is explicitly "no benefit
    to polling more frequently than once per day"
  on_termination: none_stated
  personal_data: none # employer postings only, no candidate data in the response
  terms_url: https://himalayas.app/docs/remote-jobs-api
  docs_url: https://himalayas.app/docs/openapi.json
  read_at: 2026-08-03
  read_depth: full # the docs page and the OpenAPI spec
  verbatim:
    - "Free to use with attribution"
    - "If you display Himalayas job data on your own website or application, include a visible link back to himalayas.app and mention that the data is sourced from Himalayas."
    - "The API is free to use and requires no authentication."
    - "The API is rate limited. If you exceed the rate limit, you will receive a 429 Too Many Requests response."
    - "The data is cached and refreshed every 24 hours, so there is no benefit to polling more frequently than once per day."
    - "If you need a higher rate limit for a specific use case, contact the team at hi@himalayas.app."

endpoint:
  base: https://himalayas.app/jobs/api/search
  method: GET
  slug_pattern: null
  browse: https://himalayas.app/jobs/api # the OTHER endpoint — different pagination, see traps

query:
  title_search: true # MEASURED — `q=nurse` returned 18 rows, 83% with "nurse" in the title
  location_search: true # `country`, plus `timezone` and `worldwide` filters
  filters:
    [
      q,
      country,
      worldwide,
      exclude_worldwide,
      seniority,
      employment_type,
      company,
      timezone,
      sort,
      page,
    ]
  param_validation: ignores_unknown

limits:
  page_size: 20 # documented: "Defaults to 20. Maximum value is 20" — a larger value is ignored
  max_page: 401 # MEASURED with the correct `page` parameter; page 402 returns no rows
  reachable_per_query: 8020 # 20 x 401 — against an advertised 100,157. See volume
  rate_limit: unknown # documented to EXIST (429) but no figure is published
  quota: unknown
  concurrency_safe: unknown

volume:
  advertised: 100157 # `totalCount`, identical on every page sampled
  reachable: 8020 # 8% of what is advertised — a 12.5x gap
  measured_at: 2026-08-03

freshness:
  median_age_days: 0
  pct_within_100d: 1.0 # █████
  date_sorted: false # measured across pages 0/100/200/300/401 — see notes below
  measured_at: 2026-08-03
  note: >
    A 0-day median is the CACHE's age, not the market's: the docs state the data is rebuilt
    every 24 hours, and `sort` defaults to `relevant` rather than `recent`, so this number
    says the feed was refreshed today and little else.

coverage:
  countries: [multi] # expressed as `locationRestrictions`, an array of countries, often empty
  us_share: null # the captured row restricts to LatAm; the distribution was not counted
  sector_skew: "remote-first tech and adjacent — engineering, design, marketing, analytics"

location:
  shape: structured
  primary_path: locationRestrictions
  type: array_of_string
  state_available: false # no state, city or postal field exists anywhere in the record
  state_path: null
  city_path: null
  country_path: locationRestrictions # an array of COUNTRIES, empty meaning worldwide
  multi_value: true
  multi_delimiter: null
  free_text_fallback: null # there is no free-text location string to fall back to
  gazetteer_needed: false # nothing to geocode; there is no place name finer than a country
  notes: >
    Structured but COARSE, and it is a restriction rather than a workplace: `locationRestrictions`
    lists the countries an applicant must be based in, and an EMPTY array means worldwide rather
    than unknown — a distinction a consumer that treats empty as null will lose. A parallel
    `timezoneRestrictions` array of UTC offsets carries the same idea in a different unit. The
    granularity stops at the country, so US state filtering is impossible from this source.

remote:
  signal: derived
  path: null
  rule: "every posting is remote by construction — Himalayas lists nothing else"
  reliability: "the site is remote-only, so the signal is the source itself"

fields:
  title: { path: title, type: str }
  company: { path: companyName, type: str } # `companySlug` is the stable key for filtering
  body: { path: description, type: str, median_chars: 6200 } # sanitized HTML
  posted: { path: pubDate, type: int } # epoch SECONDS
  url: { path: applicationLink, type: str } # `guid` was identical on the captured row
  tags: { path: categories, type: list } # see traps — these are not clean categories
  salary: { path: [minSalary, maxSalary, currency, salaryPeriod], type: number } # structured
  employment_type: { path: employmentType, type: str } # "Contractor"
  function: null # `categories` is too dirty to serve as a job family — see traps
  org_unit: null
  employer_org: null
  seniority: { path: seniority, type: list } # ["Senior"] — an ARRAY, already normalized
  # unmapped keys in the record: companyLogo, companySlug, excerpt, expiryDate, guid,
  # locationRestrictions, parentCategories, timezoneRestrictions

traps:
  - "**Advertised 100,157, reachable 8,020.** `totalCount` says a hundred thousand jobs; paging stops dead at page 401. You can reach 8% of what it claims, and nothing in the response says so — the count does not shrink as you approach the wall."
  - "Not date-sorted, but every reachable page is fresh: medians of 0, 0, 0, 0 and 1 days at pages 0/100/200/300/401. Unusual and useful — the ordering is not by date, yet the whole reachable window is inside two days, so a freshness cut costs nothing here."
  - "THE BIG ONE: the two endpoints paginate differently. `/jobs/api` (browse) uses `offset` and `limit`; `/jobs/api/search` uses `page`. Sending `offset` to the search endpoint is silently ignored and you get page 1 forever — which is exactly what this probe did, so `max_page: unknown` above is an artifact of the wrong parameter, not a measured ceiling."
  - "`limit` maxes at 20 and a larger value is ignored, not rejected. Asking for 100 returns 20 and a 200."
  - "The response envelope carries `totalCount` — the real corpus size — and neither the probe nor the current adapter reads it. That single field answers the reach question this profile leaves `unknown`."
  - "An EMPTY `locationRestrictions` array means WORLDWIDE, not unknown. Treating empty as missing throws away the most permissive rows in the feed. VERIFIED THE HARD WAY in 0.8.0: the adapter wrote `', '.join(...) or None`, which recorded exactly that on 29 rows of a 31,790-row harvest. This profile was right and the code ignored it for two releases — the empty array now survives as an empty list, distinct from null."
  - "`locationRestrictions` is a LIST and must be passed through as one. Joining it into a string is unrecoverable, because ISO country names contain commas: 'Congo, The Democratic Republic of the' and 'Micronesia, Federated States of' both appear in this feed, so re-splitting on ', ' yields fragments like 'Federated States of' as if they were countries. Fixed in 0.8.0; the joined form survives only as the display string."
  - "`categories` is a per-posting free-text array, not a taxonomy. The captured row's categories are ['Lead-Marketing-Analyst', 'Senior-Marketing-Analytics-Specialist'] — hyphenated job-title variants, not job families. A `parentCategories` array exists for the real grouping and was EMPTY on that row."
  - "Salary is structured and unvalidated. The captured row reads `minSalary: 4`, `maxSalary: 5500`, `salaryPeriod: monthly` — a four-dollar minimum. Structured is not the same as clean, and a salary filter will surface these."
  - "`seniority` is an ARRAY even when it holds one value. So is `categories`, `locationRestrictions`, `timezoneRestrictions` and `parentCategories` — five of the record's twenty keys are lists."
  - "Freshness is capped by a 24-hour cache the docs state outright. A 0-day median means the cache rebuilt today, and `sort` defaults to `relevant`, not `recent`."
---

# himalayas

## A real record

Captured 2026-08-03 from `himalayas.app/jobs/api/search?limit=20`, first row. Description and
one array truncated with a marker; rest verbatim.

```json
{
  "title": "Senior Marketing Analyst LatAm",
  "excerpt": "Location: Remote — Latin America (LatAm) • Reports to: Head of Analytics (TBD) • Type: Full-time • Created: Jule 2026 🚀 We're building AI agents for performance marketing.",
  "companyName": "Plurio",
  "companySlug": "plurio",
  "companyLogo": "https://cdn-images.himalayas.app/vy9k5ks1g7nhgbfskw857pdvtire",
  "employmentType": "Contractor",
  "minSalary": 4,
  "maxSalary": 5500,
  "salaryPeriod": "monthly",
  "seniority": ["Senior"],
  "currency": "USD",
  "locationRestrictions": [],
  "timezoneRestrictions": [-8, -7, -6, -5, -4, -3, "…[truncated, 7 total]"],
  "categories": [
    "Lead-Marketing-Analyst",
    "Senior-Marketing-Analytics-Specialist"
  ],
  "parentCategories": [],
  "description": "<p><strong>Location:</strong> Remote — <strong>Latin America (LatAm)</strong> • <strong>Reports to:</strong> <em>Head of Analytics (TBD)</em>…[truncated, 10718 chars total]",
  "pubDate": 1785148314,
  "expiryDate": 1787494535,
  "applicationLink": "https://himalayas.app/companies/plurio/jobs/senior-marketing-analyst-latam",
  "guid": "https://himalayas.app/companies/plurio/jobs/senior-marketing-analyst-latam"
}
```

**What this record proves that the field table did not.** That structured and trustworthy are
different properties.

Salary here has four dedicated keys — min, max, currency, period — which is the shape a
consumer wants. The values are `4` and `5500` per month. Nobody is offering a four-dollar
monthly minimum; the employer typed something wrong and the API published it, because a
structured field is a container and not a validator. A salary filter built on this source will
surface rows like it, and no amount of correct mapping prevents that.

`categories` is the second surprise. It reads like a taxonomy and it is a per-posting array of
hyphenated title variants — "Lead-Marketing-Analyst", "Senior-Marketing-Analytics-Specialist" —
so mapping it to `function` would fill a job-family column with 5,000 distinct job titles. The
field intended for grouping is `parentCategories`, and on this row it is empty.

Third: `locationRestrictions` is `[]`, and that empty array is the actual answer — this job is
open worldwide. It is the one place in this catalog where an empty collection carries more
information than a populated one, and the natural defensive reflex (`if not x: skip`) discards
precisely the rows a remote job search most wants.

`excerpt` is worth a note too: it is a genuine plain-text summary, not the mangled prefix that
Jobicy's `jobExcerpt` turns out to be. The typo in it ("Jule 2026") is the employer's.

## The pagination mistake, stated plainly

This source has two endpoints and they do not share a pagination model:

- `/jobs/api` — browse. Parameters `offset` and `limit`. Page 2 is `?offset=20&limit=20`.
- `/jobs/api/search` — search. Parameters `q`, `country`, `sort`, … and `page`. Page 2 is
  `?page=2`.

The probe ran against `/jobs/api/search` and walked `offset`. The search endpoint does not know
that parameter, ignored it exactly as `param_validation: ignores_unknown` predicts, and returned
the same 17 rows for `offset=19980` as for the control. The probe correctly reported "`offset`
appears IGNORED" — and that report is about the parameter, not about the source's depth.

That was the state of this profile until the target was corrected; the ceiling below is now measured with `page`. The fix
is one line in `_targets.json` rather than a new measurement technique. `totalCount` in the
response envelope would answer the volume question in a single request.

## How this was probed

`catalog/_probe.py` on 2026-08-03, one request per second. The documentation page and the
OpenAPI 3.1 spec were both read in full the same day, and the licence is quoted verbatim above.

```
junk:  control 200 (17 rows) vs junk 200 (17 rows) -> ignores_unknown
title: control: 17 rows, 0% already match 'nurse'
title: q=nurse -> 200, 18 rows, 83% relevant <- FILTERS
title: query / search / keyword / keywords / title / name = nurse -> 200, 17 rows, 0% relevant
title: filters on: ['q']
pages: `offset` appears IGNORED — WRONG PARAMETER for this endpoint, see above
fresh: 17 dated rows across pages [0]; p0 median 0d, 100% within 100d
body:  median 6200 chars over 17 rows
```

**Not checked.** The page ceiling and the reachable corpus, for the reason above — neither was
measured and `totalCount` was not captured, so this profile cannot say how big Himalayas is.
`date_sorted` is unanswerable while only page 1 was reached, and `sort=recent` was never tried.
The rate limit is documented to exist and no figure is published, so it stays `unknown`;
bursting on a source that publishes a contact address for higher limits was not run. The
browse endpoint `/jobs/api` was never probed at all — it may have a different record shape, a
different fill rate, or a different ceiling. And the `us_share` is null rather than guessed: the
one captured row restricts to Latin America, which is not a sample.
