---
name: ashby
display_name: Ashby
status: wired
lane: depth
verdict: >
  One keyless request returns a whole board with long bodies AND a real structured postal
  address — country, region and locality as separate fields, which only two other sources in
  this catalog send. Its two weaknesses are measured, not theoretical: the two fields that
  should answer "is this remote" were both null on the captured row, and Ashby is the one depth
  platform with no cheap liveness variant, so asking whether a board exists costs a full fetch.

auth:
  type: none
  env: []
  signup: null
  notes: keyless; the public job-board endpoint requires no key and no registration

license: # a CONTRACT — quoted, not paraphrased
  commercial_use: unclear
  commercial_terms: >
    Ashby's terms of service govern its EMPLOYER customers' use of the Ashby product, not a
    third party reading a public job board, and the restrictions quoted below are written
    against "the Service" in that customer sense. The public Job Posting API documentation was
    read in full and states nothing at all about who may consume the feed, about attribution,
    or about a rate limit — that is a genuine gap in Ashby's documentation, not a page this
    probe failed to reach. No clause grants third-party use, so `unclear`, not `allowed`.
  attribution_required: unknown # never addressed
  redistribution: unknown # the ToS clause below binds customers, not feed readers
  derivative_works: unknown
  cache_policy: none_stated
  on_termination: none_stated # the clause below concerns Customer Data inside Ashby
  personal_data: none # employer postings only; the public endpoint cannot reach candidates
  terms_url: https://www.ashbyhq.com/terms
  docs_url: https://developers.ashbyhq.com/docs/public-job-posting-api
  read_at: 2026-08-03
  read_depth: full # both the ToS and the public Job Posting API documentation
  verbatim:
    - "for service bureau or time-sharing purposes or in any other way allow third parties to exploit the Service"
    - "in order to build a competitive product or service, to build a product using similar ideas, features, functions or graphics of the Service, or to copy any ideas, features, functions or graphics"
    - "Ashby may permanently erase Customer Data if Customer's account is delinquent, suspended, or terminated for 30 days or more."

endpoint:
  base: https://api.ashbyhq.com/posting-api/job-board/{slug}
  method: GET
  slug_pattern: "api.ashbyhq.com/posting-api/job-board/{slug}"

query:
  title_search: false # whole-board fetch; the posting endpoint has no query interface
  location_search: false
  filters: []
  param_validation: ignores_unknown

limits:
  page_size: null # the entire board arrives in one response, unpaged
  max_page: null
  reachable_per_query: null # the whole board — 737 rows for openai
  rate_limit: unknown # NOT documented anywhere on Ashby's developer site; not probed
  quota: unknown
  concurrency_safe: unknown
  requests_per_company: 1
  cheap_liveness: false # BY MEASUREMENT — no cheap variant exists; see traps

volume:
  advertised: null
  reachable: null # a function of how many slugs you can name
  measured_at: 2026-08-03

freshness:
  median_age_days: 59
  pct_within_100d: 0.67
  date_sorted: unknown # one unpaged document; there is nothing to sort across
  measured_at: 2026-08-03
  note: >
    Measured against ONE board (openai, 737 rows). A third of that board is over 100 days old,
    which is a fact about how long OpenAI leaves requisitions open, not about Ashby.

coverage:
  countries: [multi] # the sampled board reports United States on the captured row
  us_share: null # per-company by construction; one board is not a platform sample
  sector_skew: "per-company by construction — the board is whatever that employer posts"

location:
  shape: structured
  primary_path: address.postalAddress
  type: object
  state_available: true # `addressRegion` carried a real region on 657 of 737 sampled rows
  state_path: address.postalAddress.addressRegion # "California"
  city_path: address.postalAddress.addressLocality # "San Francisco"
  country_path: address.postalAddress.addressCountry # "United States" — a NAME, not a code
  multi_value: true # `secondaryLocations` is a parallel array, empty on the captured row
  multi_delimiter: null
  free_text_fallback: location # the flat string, "San Francisco"
  gazetteer_needed: false
  notes: >
    One of only three sources here that ship a state without a gazetteer, and the richest of
    the three: `address.postalAddress` is a schema.org PostalAddress with country, region and
    locality as separate keys, present on 657 of 737 rows sampled 2026-08-03. Two cautions.
    `addressCountry` is a full country NAME ("United States"), not an ISO code, so it needs
    normalizing before it joins to anything. And a flat `location` string sits beside the
    object carrying only the city — the current adapter keeps that string and discards the
    object, which is the single cheapest structured-location win in the depth lane.

remote:
  signal: structured
  path: [isRemote, workplaceType]
  rule: "isRemote == true, or workplaceType == 'Remote'"
  reliability: >
    BOTH fields were null on the captured row. Ashby defines two first-class remote fields and
    this board populated neither, so the signal is structurally available and empirically
    absent here. Their fill rate across the board was NOT measured — treat the rule as
    untested, and expect to fall back to string matching on `location`.

fields:
  title: { path: title, type: str }
  company: null # the board IS the company; the slug is the only company identifier
  body: { path: descriptionHtml, type: str, median_chars: 8353 } # `descriptionPlain` is the twin
  posted: { path: publishedAt, type: str } # ISO 8601 with a real offset
  url: { path: jobUrl, type: str } # `applyUrl` is the same posting's application form
  tags: null
  salary: null
  employment_type: { path: employmentType, type: str } # "FullTime" — no separator, see traps
  function: null
  org_unit: { path: team, type: str } # "Technical Program Management" — see traps re `department`
  employer_org: null
  seniority: null
  # unmapped keys in the record: address, applyUrl, department, descriptionPlain, id, isListed,
  # isRemote, location, secondaryLocations, workplaceType

traps:
  - "Ashby is the ONE depth platform with no cheap liveness variant, and that is by measurement rather than oversight. `sources.liveness_for` falls back to counting a full board fetch, so asking 'does this board exist' costs the same as harvesting it — which is what makes a 500-slug Ashby sweep expensive where the same sweep on Greenhouse or Lever is not."
  - "`isRemote` and `workplaceType` were BOTH null on the captured row. A consumer that branches on either one gets no signal from this board and no error either, which is worse than a missing field because it looks like 'not remote'."
  - "`department` and `team` held the IDENTICAL string on the captured row. They are two keys for one org unit here, so a consumer mapping both produces a duplicated value, and mapping either to `function` is the naming mistake _SCHEMA.md documents."
  - "The structured address is discarded by the current adapter in favour of the flat `location` string beside it. `address.postalAddress.addressRegion` is a real US state on 657 of 737 rows and the shortlist has no state column to put it in."
  - '`addressCountry` is a country NAME, not an ISO code. "United States" will not join to a `US`.'
  - '`employmentType` is CamelCase with no separator — "FullTime", not "Full Time" or "full_time". Every other source in this catalog spells it differently.'
  - "`descriptionHtml` and `descriptionPlain` are both full-length twins of the same body (8,604 and 6,891 chars on the captured row). Keeping both doubles storage for no information."
  - "A 404 means the SLUG is wrong, not that the platform is dead."
---

# ashby

## A real record

Captured 2026-08-03 from `api.ashbyhq.com/posting-api/job-board/openai`, first row. Both body
fields truncated with a marker; rest verbatim.

```json
{
  "id": "8fb1615c-34bf-47c4-a1d1-b7b2f836bbd3",
  "title": "Technical Program Manager, Compute Infrastructure",
  "department": "Technical Program Management",
  "team": "Technical Program Management",
  "employmentType": "FullTime",
  "location": "San Francisco",
  "secondaryLocations": [],
  "publishedAt": "2026-03-12T16:38:15.322+00:00",
  "isListed": true,
  "isRemote": null,
  "workplaceType": null,
  "address": {
    "postalAddress": {
      "addressRegion": "California",
      "addressCountry": "United States",
      "addressLocality": "San Francisco"
    }
  },
  "jobUrl": "https://jobs.ashbyhq.com/openai/8fb1615c-34bf-47c4-a1d1-b7b2f836bbd3",
  "applyUrl": "https://jobs.ashbyhq.com/openai/8fb1615c-34bf-47c4-a1d1-b7b2f836bbd3/application",
  "descriptionHtml": "<h3><strong>About the Team</strong></h3><p style=\"min-height:1.5em\">The compute infrastructure team runs the GPU fleet and large-scale compute clusters that serve the models backing ChatGPT and the API…[truncated, 8604 chars total]",
  "descriptionPlain": "ABOUT THE TEAM\n\nThe compute infrastructure team runs the GPU fleet and large-scale compute clusters that serve the models backing ChatGPT and the API…[truncated, 6891 chars total]"
}
```

**What this record proves that the field table did not.** Two things, pulling in opposite
directions.

The good one: `address.postalAddress` is a real schema.org postal address sitting quietly
beside the flat `location` string. `location` says "San Francisco" and stops; the object beside
it says California, United States, San Francisco in three separate keys. A field table listing
`location: str` would record the string the adapter actually reads and miss that the structured
version was already in the response. For Ashby, structured location is a mapping change of a
few lines — not the parsing project it is for Greenhouse.

The bad one: `isRemote` and `workplaceType` are both `null`. Ashby ships two dedicated remote
fields, which is more than almost any source here, and this board populated neither. That is
the difference between "the API has no remote signal" (false) and "you will not get one from
this board" (true, and only visible in the record). A consumer that treats `isRemote: null` as
`false` will quietly mark every remote role at OpenAI as on-site.

`department` and `team` carrying the same string is the third, smaller finding: two keys, one
org unit, and no way to tell from the names which one Ashby considers authoritative.

## Why `cheap_liveness` is false, and what it costs

Every other depth platform here answers "does this board exist and have roles?" more cheaply
than it answers "what is on it". Ashby does not — there is no count-only or head-only variant,
so `sources.liveness_for` falls back to fetching the board and counting rows. For a 737-row
board with 8 KB bodies that is a real transfer to learn one boolean.

This is recorded because it is a scaling fact, not a preference: the discovery funnel probes
far more slugs than it harvests, so a platform where probing costs a harvest changes how wide
a sweep can reasonably go. It was established by measurement rather than by reading the docs,
and it belongs in the profile for the same reason the rate limit does.

## How this was probed

`catalog/_probe.py` on 2026-08-03, one request per second, with steps 2 and 3 skipped — a
whole-board endpoint has no query interface to test and no pages to walk. The terms of service
and the public Job Posting API documentation were both read in full the same day.

```
junk:  control 200 (737 rows) vs junk 200 (737 rows) -> ignores_unknown
fresh: 737 dated rows across pages [0]; p0 median 59d, 67% within 100d
body:  median 8353 chars over 737 rows
state: addressRegion populated on 657 of 737 rows
```

**Not checked.** The rate limit is `unknown` in the strong sense: Ashby documents none
anywhere, and bursting until a 429 was not run. The fill rate of `isRemote` and `workplaceType`
across the whole board was not measured — the null pair is a single-row observation, so the
`remote.rule` above is stated but untested. Freshness and coverage come from ONE board, and
59 days median says as much about OpenAI's requisition hygiene as about Ashby. The licence is
`full` read-depth but still `unclear`: the terms exist and were read, and they simply do not
speak to a third party reading a public board, which is an absence of permission rather than a
grant of it.
