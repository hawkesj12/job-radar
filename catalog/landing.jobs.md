---
name: landing.jobs
display_name: Landing.jobs
status: evaluated
lane: breadth
verdict: >
  A working per-request title search — `q=engineer` returns 42 rows at 95% title relevance —
  on a small, Portugal-and-EU tech board with structured city/country and structured salary.
  Two things keep it `evaluated`: its terms flatly prohibit copying, using, displaying or
  distributing anything obtained from the platform, and the body arrives split across four
  separate HTML fields with no single description.

auth:
  type: none
  env: []
  signup: null
  notes: keyless; `/api/v1/jobs` answered with no key and no registration

license: # a CONTRACT — quoted, not paraphrased
  commercial_use: prohibited
  commercial_terms: >
    The most restrictive terms of any keyless source in this catalog, and the least ambiguous.
    §8.2 prohibits copying, using, displaying OR distributing any information obtained from the
    platform — all four verbs, with no carve-out for an API — and separately prohibits both
    automated and manual copying, and monetizing "the Platform or related data or access". A
    consumer that stores and displays these rows is doing the thing the clause names. This is
    not an `unclear` produced by silence; it is a prohibition produced by text.
  attribution_required: unknown # never addressed — attribution is not offered as a remedy
  redistribution: prohibited
  derivative_works: prohibited
  cache_policy: none_stated
  on_termination: none_stated
  personal_data: none # employer postings only, no candidate data in the response
  terms_url: https://landing.jobs/tos
  read_at: 2026-08-03
  read_depth: full
  verbatim:
    - "Copy, use, display or distribute any information obtained from the Platform"
    - "Rent, lease, loan, trade, sell/re-sell or otherwise monetize the Platform or related data or access"
    - "…software, devices, scripts, robots or any other means or processes to access, monitor, scrape or copy the Platform…"
    - "Use any manual process to monitor or copy any…material of the Platform, unless expressly permitted"

# ── ADMISSION TEST — keyless is not the same as permitted. Both halves, separately.
admission:
  documented_public_api: unknown # `/api/v1/jobs` is versioned, but no developer docs were found
  terms_permit_automated_access: false # §8.2 bars scripts, robots AND manual copying
  verdict: >
    FAILS the second half outright and cannot answer the first. The endpoint is versioned
    (`/api/v1/`), which is the shape of something built to be consumed rather than a front-end
    route, but no developer documentation, API page or terms-of-API section was found to
    confirm it. The terms settle it regardless: §8.2 prohibits accessing the platform with
    "software, devices, scripts, robots or any other means or processes to access, monitor,
    scrape or copy", and then prohibits manual copying separately, so there is no access mode
    left open. Barred is enough on its own — the documentation question does not need
    answering.

endpoint:
  base: https://landing.jobs/api/v1/jobs
  method: GET
  slug_pattern: null

query:
  title_search: true # MEASURED on retry — see "The `q` false negative" below
  location_search: unknown # not probed; no location parameter was tried
  filters: [q]
  param_validation: ignores_unknown

limits:
  page_size: 50 # the unfiltered response returned 50 rows
  max_page: null # `page` had no effect — page 999 returned the identical 50 rows
  reachable_per_query: 50 # for the bare feed. `q=engineer` returned 42, so the filter is applied
  rate_limit: unknown # none published; not probed
  quota: unknown
  concurrency_safe: unknown

volume:
  advertised: null # the response carries no total
  reachable: 50 # unfiltered; the true corpus size is not reported by the API
  measured_at: 2026-08-03

freshness:
  median_age_days: 59.0
  pct_within_100d: 0.6 # ███░░
  date_sorted: unknown # `page` is ignored, so there was nothing to sort across
  measured_at: 2026-08-03
  note: >
    The stalest breadth source measured — 40% of rows are over 100 days old, and the captured
    row was published in February 2025, eighteen months before the probe. See traps: `published_at`
    and `updated_at` are 17 months apart on that row, and only one of them is a posting date.

coverage:
  countries: [PT, EU] # the captured row is Lisbon; salaries are in EUR
  us_share: 0.0 # no US rows observed; this is a Portugal-centred European board
  sector_skew: "software engineering almost exclusively — Java, Spring, Kafka, Kubernetes"

location:
  shape: structured
  primary_path: locations
  type: array_of_object
  state_available: false # the object has city and country_code and no region key at all
  state_path: null
  city_path: locations[].city # "Lisbon"
  country_path: locations[].country_code # "PT" — an ISO code
  multi_value: true # `locations` is an array
  multi_delimiter: null
  free_text_fallback: null
  gazetteer_needed: false # city plus ISO country is enough to place a row
  notes: >
    Small and clean: two keys, both populated, the country already an ISO-2 code. There is no
    state or region field, which for a Portugal-centred board is a reasonable omission rather
    than a gap — but it means US state filtering is structurally impossible here, and the
    source has no US rows to filter anyway.

remote:
  signal: structured
  path: remote
  rule: "remote == true"
  reliability: >
    A real boolean field, `false` on the captured row. Its fill rate and accuracy across the
    feed were not measured, but a dedicated boolean is a stronger signal than the string
    matching most sources here require.

fields:
  title: { path: title, type: str }
  company: null # NO company field — the employer is named only inside `role_description` prose
  body: { path: role_description, type: str, median_chars: 2025 } # ONE OF FOUR — see traps
  posted: { path: published_at, type: str } # ISO 8601 with milliseconds and a Z
  url: { path: url, type: str }
  tags: { path: tags, type: list } # ["Spring Boot", "Java", "SQL", …] — clean skill tags
  salary: {
      path: [gross_salary_low, gross_salary_high, currency_code],
      type: int,
    } # structured
  employment_type: { path: type, type: str } # "Full-time"
  function: null
  org_unit: null
  employer_org: null
  seniority: null
  # unmapped keys in the record: id, expires_at, main_requirements, nice_to_have, perks,
  # relocation_paid, created_at, updated_at

traps:
  - "There is NO company field. The captured row's employer is described only as 'Our client is a Global retail chain' inside `role_description`, and the real employer name (Inscale) appears nowhere except inside the `url` slug. A consumer needs a company for deduplication and for display, and this source does not send one."
  - "The body is FOUR fields, not one: `role_description`, `main_requirements`, `nice_to_have` and `perks`, each separate HTML. Taking the longest — which is what the probe's 2,025-char median measures — drops the requirements, which is the part keyword scoring most wants. Concatenating all four is the correct read."
  - "`published_at` is 2025-02-26 and `updated_at` is 2026-08-01 on the captured row — a posting listed for seventeen months and touched three days ago. Freshness measured on `published_at` is honest and unflattering; measuring on `updated_at` instead would make this the freshest source in the catalog, and it would be a lie."
  - "`q` returning ZERO rows is not `q` being broken. `q=nurse` returned 0 and the probe scored the parameter dead; `q=engineer` returns 42 rows at 95% title relevance. A term this board could not contain and a parameter it does not know look identical to a relevance test — see below."
  - "`page` is ignored. Page 999 returned the identical 50 rows as the control, so 50 is everything the bare feed will give."
  - "Salaries are EUR and the field is named `gross_salary_low`/`_high` — gross annual, and on the captured row 14 salaries a year are mentioned in `perks`, which is a Portuguese convention. Comparing the number against a US annual figure without that context understates it."
  - "The terms prohibit copying, using, displaying and distributing anything obtained from the platform. That is not a caching restriction, it is the whole activity."
---

# landing.jobs

## A real record

Captured 2026-08-03 from `landing.jobs/api/v1/jobs`, first row. The four body fields truncated
with markers; rest verbatim.

```json
{
  "id": 19066,
  "title": "Senior Java Software Developer",
  "url": "https://landing.jobs/at/inscale/senior-java-software-developer-in-lisbon-2025",
  "type": "Full-time",
  "remote": false,
  "currency_code": "EUR",
  "gross_salary_low": 50000,
  "gross_salary_high": 67000,
  "tags": [
    "Spring Boot",
    "Java",
    "SQL",
    "Apache Kafka",
    "Kubernetes",
    "Docker"
  ],
  "locations": [{ "city": "Lisbon", "country_code": "PT" }],
  "created_at": "2025-02-26T08:53:02.254Z",
  "published_at": "2025-02-26T09:38:38.127Z",
  "updated_at": "2026-08-01T01:00:02.373Z",
  "expires_at": "2026-09-02",
  "relocation_paid": false,
  "role_description": "<div>Our client is a Global retail chain that brings Scandinavian design and quality to the world through an extensive range of quality products for sleeping and living…[truncated, 1312 chars total]",
  "main_requirements": "<ul><li>6-8 years of experience in software development with Java.</li><li>5+ years working with Spring Boot.</li><li>Experience with SQL.</li>…[truncated, 622 chars total]",
  "nice_to_have": "<ul><li>Experience with Cloud Native engineering.</li><li>Experience with Kafka.</li><li>Experience with Docker and Kubernetes.</li><li>Test Driven Development (TDD).</li></ul>",
  "perks": "<ul><li>Permanent contract with Inscale</li><li>14 salaries</li><li>22 days vacation</li><li>Portugal Holidays</li><li>Health insurance</li><li>Flex benefits</li><li>Career plan&nbsp;</li></ul>"
}
```

**What this record proves that the field table did not.** Two absences and one arithmetic.

There is no company. `role_description` says "Our client is a Global retail chain" — the board
is anonymizing the employer on the employer's behalf — and the only place the string "Inscale"
appears is in the URL slug and in `perks`. A field table with `company: null` is accurate and
undersells the consequence: `dedup` groups by company, the shortlist displays it, and this
source cannot supply it without parsing prose or a URL.

There is no single body either. Four HTML fields carry four parts of one posting, and the one
the probe measured (`role_description`, the longest) is the marketing preamble about the client.
The requirements — Java, Spring Boot, SQL, the years of experience — are in `main_requirements`,
which a consumer that maps "the description field" never reads. That is the difference between
scoring this row on "Scandinavian design and quality" and scoring it on its actual tech stack.

The arithmetic is `published_at: 2025-02-26` against `updated_at: 2026-08-01`. This job has been
listed for seventeen months and was touched three days before the probe. Whichever date a
consumer picks decides whether this source looks like the stalest one in the catalog (it is) or
the freshest one (it is not), and both numbers are in the same record.

## The `q` false negative

The probe reported `title_search: false`. It was wrong, and the way it was wrong is worth
keeping because it is not specific to this source.

`q=nurse` returned HTTP 200 with **zero rows**, against a control of 50. The scoring rule was
`status == 200 and rows and relevance >= 0.3` — an empty result fails the `rows` test, so the
parameter was recorded as doing nothing. But an empty result is what a _working_ filter returns
when a Portugal-focused software board is asked for nurses.

Retried the same day with terms this board could plausibly contain:

```
q=java     -> 200, 17 rows, 24% title-relevant (the rest match on tags/description)
q=engineer -> 200, 42 rows, 95% title-relevant
```

95% against a 0% control is not ambiguous. `q` is a real keyword filter, this source can serve
a live per-request fetch, and `title_search` is `true`.

`_probe.py` was changed to catch this class: a 200 with zero rows against a non-empty control
now reports `unknown` with a "RETRY with a term this source could plausibly match" note, rather
than `false`. The distinction it now preserves is between a parameter that is ignored and a
parameter that is honoured and matched nothing — which look identical on one query and mean
opposite things for routing.

## How this was probed

`catalog/_probe.py` on 2026-08-03, one request per second, plus two hand-run requests to retest
`q`. Terms read the same day and quoted verbatim above.

```
junk:  control 200 (50 rows) vs junk 200 (50 rows) -> ignores_unknown
title: control: 50 rows, 0% already match 'nurse'
title: q=nurse -> 200, 0 rows, 0% relevant       <- FALSE NEGATIVE, see above
title: query / search / keyword / keywords / title / name / term = nurse -> 200, 50 rows, 0%
retry: q=java -> 200, 17 rows, 24% ; q=engineer -> 200, 42 rows, 95%  <- FILTERS
pages: `page` appears IGNORED — page 999 returned the same 50 rows as the control
fresh: 50 dated rows across pages [0]; p0 median 59.0d, 60% within 100d
body:  median 2025 chars over 50 rows (longest of four body fields, not their sum)
```

**Not checked:** the rate limit stays `unknown` — none is published and bursting was not run.
The true corpus size is unknown: the API reports no total, `page` is ignored, and 50 rows is
simply what the bare feed returns, so whether `q` searches a larger corpus than those 50 was
NOT established — that is the open question that decides how useful the working title search
actually is. `location_search` was never probed. The fill rate of the `remote` boolean was not
measured. And `us_share: 0.0` is recorded from a 50-row sample of a board whose salaries are
all in euros; it is an observation, not a census.
