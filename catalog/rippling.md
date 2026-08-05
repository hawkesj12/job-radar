---
name: rippling
display_name: Rippling
status: wired
lane: depth
verdict: >
  A confirmed new DEPTH platform — 750 rows from one keyless request — and the thinnest record
  shape in the whole catalog: five keys, no description, no date, no salary, no employment
  type. Nothing downstream can score a posting with no body and nothing can age one with no
  date, so wiring it means finding a detail endpoint first. Rippling's own customer terms also
  say, in as many words, that data may not be extracted "as part of any data aggregation
  service", which is closer to on-point than any other ATS's terms in this lane.

auth:
  type: none
  env: []
  signup: null
  notes: >
    The board endpoint answered with no key. A search summary claimed the Job Board API
    requires a Recruiting Pro subscription on the EMPLOYER side; that is about who can publish
    a board, not who can read one, and it was not page-verified.

license: # a CONTRACT — quoted, not paraphrased
  commercial_use: unclear
  commercial_terms: >
    Rippling's customer terms of service are the only contract that could be read, and they
    bind Rippling's CUSTOMERS rather than a third party reading a public board. Read that way
    they still land closer to this use than any other ATS's terms do: they prohibit extracting
    data "for use outside of the Rippling Services or as part of any data aggregation service"
    and bar automated request volumes above what a human could produce. Whether those clauses
    reach a stranger reading a public job board is exactly the question no clause answers, so
    `unclear` — and given how specifically aggregation is named, this is an `unclear` that
    deserves legal eyes before anything is wired.
  attribution_required: unknown # never addressed
  redistribution: unclear # the aggregation clause below is on-point but binds customers
  derivative_works: unclear
  cache_policy: none_stated
  on_termination: "trial data 'will be permanently lost unless Customer purchases a subscription'"
  personal_data: none # employer postings only, from what the response contains
  terms_url: https://static-assets.ripplingcdn.com/legal/en-US/customer_terms_of_service.html
  docs_url: https://developer.rippling.com/documentation/job-board-api # NOT read — see below
  read_at: 2026-08-03
  read_depth: full # the customer ToS in full; the Job Board API docs could NOT be retrieved
  verbatim:
    - "Authorized Representatives may not extract data from Rippling for use outside of the Rippling Services or as part of any data aggregation service."
    - 'an authorized accountant, broker, HR/IT consultant or other representative of Customer (an "Authorized Representative")'
    - 'The terms of this Agreement applies to all customers of the Rippling Services, including, as applicable, paid subscribers, prospective subscribers accessing the Rippling Services for evaluation purposes and current and prospective subscribers'' Users, Account Administrators, Authorized Representatives and any other persons authorized to act on behalf of an entity or other organization with respect to the Rippling Services (collectively, "Customers").'
    - "use any tool, system, or process that sends more requests to our servers…than a human can reasonably produce in the same period"
    - "develop…software, devices, agents, scripts, robots…to scrape, distill the Rippling Services"
    - "transfer, resell, lease, license, or assign any Rippling Services…without express permission from Rippling"
    - "use or access any Rippling Service (1) to build, maintain, or improve a similar or competitive product"

endpoint:
  base: https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs
  method: GET
  slug_pattern: "api.rippling.com/platform/api/ats/v1/board/{slug}/jobs"

query:
  title_search: false # whole-board fetch; the board endpoint has no query interface
  location_search: false
  filters: []
  param_validation: ignores_unknown

limits:
  page_size: null # the entire board arrives in one response, unpaged — 750 rows
  max_page: null
  reachable_per_query: null # the whole board, whatever its size
  rate_limit: unknown # the ToS states a qualitative limit, not a number; not probed
  quota: unknown
  concurrency_safe: unknown
  requests_per_company: 1 # for the LIST. Bodies would cost a detail call per role — unmeasured
  cheap_liveness: unknown # no cheap variant was looked for; unmeasured, not absent

volume:
  advertised: null
  reachable: null # a function of how many slugs you can name
  measured_at: 2026-08-03

freshness:
  median_age_days: unknown # NOT unmeasured — unmeasurABLE. There is no date field at all
  pct_within_100d: unknown
  date_sorted: unknown
  measured_at: 2026-08-03
  note: >
    The list response carries NO date key of any kind. This is a property of the API, not a
    gap in the probe: there is nothing to parse, so age cannot be computed, a freshness cut
    cannot be applied at ingest, and "is this posting still recent" is unanswerable from the
    list endpoint alone.

coverage:
  countries: [US] # inferred from the ONE board sampled (Rippling's own); not a platform sample
  us_share: null # not established
  sector_skew: "per-company by construction — the board is whatever that employer posts"

location:
  shape: free_text
  primary_path: workLocation.label
  type: string
  state_available: false # a US state appears INSIDE the string, never as a field
  state_path: null
  city_path: null
  country_path: null
  multi_value: unknown
  multi_delimiter: null
  free_text_fallback: workLocation.label
  gazetteer_needed: true
  notes: >
    `workLocation` is an object, which looks structured and is not: it has exactly two keys,
    `id` and `label`, and on the captured row both hold the identical string
    "Remote (New Jersey, US)". So the object is a label with a duplicate, and the state,
    country and remoteness are all trapped inside one free-text value that must be parsed.

remote:
  signal: keyword
  path: workLocation.label
  rule: "the label starts with 'Remote (' — e.g. 'Remote (New Jersey, US)'"
  reliability: >
    Observed on the captured row only. The prefix looks conventional rather than free-form,
    but its consistency across the 750-row board was NOT measured, and there is no boolean or
    enum anywhere in the record to check it against.

fields:
  title: { path: name, type: str } # NOT `title` — Rippling calls it `name`
  company: null # the board IS the company; the slug is the only company identifier
  body: null # THE defect — no description field exists in the list response. See traps
  posted: null # no date field of any kind exists. See freshness.note
  url: { path: url, type: str }
  tags: null
  salary: null
  employment_type: null
  function: null
  org_unit: { path: department.label, type: str } # "Sales" — the company's own team
  employer_org: null
  seniority: null
  # unmapped keys in the record: uuid (and department.id / workLocation.id, both duplicates
  # of their sibling labels)

traps:
  - "There is NO body in the list response. The whole record is `uuid, name, department, url, workLocation` — five keys — so relevance scoring, keyword matching and LLM re-ranking all get nothing. Whether a detail endpoint supplies one was NOT tested; that single question decides whether this source is wirable."
  - "There is NO date in the list response either. Age cannot be computed, the engine's age gate cannot fire, and `first_seen` in the store would be the only timestamp a row ever gets."
  - "The title field is `name`, not `title`. A generic mapper keyed on `title` produces a board of blank titles and no error — the same shape as Lever's `text`."
  - "`department` and `workLocation` are objects whose `id` and `label` hold the IDENTICAL string. They look like reference objects with a stable key and a display name; they are a string stored twice. Do not treat `department.id` as an identifier — it is 'Sales'."
  - "Remoteness is a prefix inside a location string, so a role whose label reads 'Remote (New Jersey, US)' is both remote AND state-tagged, and only string parsing gets either fact out."
  - "Rippling's customer terms specifically name 'any data aggregation service' as a prohibited destination for extracted data. That is the most on-point clause any ATS in this lane has, and it was written for customers — read it before wiring, not after."
  - "A 404 means the SLUG is wrong, not that the platform is dead."
---

# rippling

## A real record

Captured 2026-08-03 from `api.rippling.com/platform/api/ats/v1/board/rippling/jobs`, first row.
Verbatim and complete — no truncation was needed, which is itself the finding.

```json
{
  "uuid": "2f0674e6-f01f-4ecd-b459-e947241c211f",
  "name": "Account Executive - Accountants Channel (East Coast)",
  "department": { "id": "Sales", "label": "Sales" },
  "url": "https://ats.rippling.com/rippling/jobs/2f0674e6-f01f-4ecd-b459-e947241c211f",
  "workLocation": {
    "label": "Remote (New Jersey, US)",
    "id": "Remote (New Jersey, US)"
  }
}
```

**What this record proves that the field table did not.** That 750 rows is not the same as 750
usable rows.

The whole record is five keys and it fits on six lines. Every other depth source in this
catalog needed its body truncated to be pasted here; this one has nothing to truncate, no date
to parse, no salary, no employment type, no seniority. A field table with `body: null` and
`posted: null` states both facts correctly — but seeing the complete record is what makes it
obvious that no mapping work recovers them, because they were never sent.

The second finding is subtler and would fool a schema reader. `department` and `workLocation`
are objects, and an object with `id` and `label` is the universal shape of a reference: a
stable machine key beside a human name. Here both keys hold the same string. `department.id`
is literally `"Sales"`. So the structure signals more than it carries, and a consumer that
keys a join or a dedup on `department.id` is keying on a display label that changes whenever
the employer renames a team.

What IS here is worth noting fairly: `workLocation.label` reads "Remote (New Jersey, US)",
which packs remoteness, state and country into one string. That is three useful facts in a
field this catalog would normally call free text — recoverable, but only by parsing.

## The one question that decides this source

Does a detail endpoint return a description? Everything else about Rippling is fine — keyless,
one request per company, 750 rows from a single board, a URL per posting. The list response
simply does not carry the two fields (body, date) that the pipeline is built on.

That question was NOT tested here, deliberately: the endpoint pattern is undocumented from this
side, the developer docs could not be retrieved, and guessing detail-URL shapes against a live
API is exactly the kind of probing this catalog's own rules ask to be done sparingly and on
purpose. It is the first thing to establish before anyone considers wiring this source, and it
should be established alongside the licence question below, not after it.

## The data-aggregation clause, and why it does not reach us

This is the most on-point restriction in the depth lane — "Authorized Representatives may not
extract data from Rippling for use outside of the Rippling Services or as part of any **data
aggregation service**" — and a job harvester is a data aggregation service by any plain reading.
So the whole `unclear` rests on whether we are an Authorized Representative. Checked directly
rather than assumed, 2026-08-03:

**We are not, and the agreement defines the term explicitly:** "an authorized accountant, broker,
HR/IT consultant or other representative **of Customer**". It is someone acting on a Rippling
customer's behalf.

The agreement's own hook looks access-binding at first — "BY ACCEPTING THIS AGREEMENT OR USING
ANY OF THE RIPPLING SERVICES, YOU AGREE TO BE BOUND" — which is exactly the shape that makes
TechTree's terms reach a third party. The next sentence is what settles it: the terms apply "to
all **customers** of the Rippling Services, including … paid subscribers, prospective subscribers
accessing the Rippling Services for evaluation purposes and current and prospective subscribers'
Users, Account Administrators, Authorized Representatives". Someone reading a public job board is
none of those.

So `commercial_use: unclear` is not a shrug here — it is a checked result. The strongest-sounding
clause in the lane is scoped to a population we are not in, and no other clause addresses a third
party reading the public board at all.

## How this was probed

`catalog/_probe.py` on 2026-08-03, one request per second, with steps 2 and 3 skipped — a
whole-board endpoint has no query interface to test and no pages to walk. Rippling's customer
terms of service were read in full the same day.

```
junk:  control 200 (750 rows) vs junk 200 (750 rows) -> ignores_unknown
fresh: no parseable dates in the sample — there is no date FIELD, see freshness.note
body:  no prose field over 200 chars in any sampled row
```

**Not checked.** Whether a detail endpoint exists and returns a body or a date — the question
that decides everything, and it is open. The Job Board API documentation could not be
retrieved (the page returned empty content, most likely JavaScript-rendered), so the endpoint's
own parameters, pagination and any documented rate limit are unread; the endpoint shape above
is the one that answered, not one confirmed against a doc. Whether reads require a Recruiting
Pro subscription on the employer side is a search-summary claim that was NOT page-verified.
`cheap_liveness` is `unknown` because no cheap variant was looked for. The rate limit is
`unknown`: the ToS states a qualitative limit ("than a human can reasonably produce") and no
figure, and bursting was not run. Coverage comes from ONE board — Rippling's own — so
`us_share` is null rather than guessed, and the `Remote (` prefix's consistency across the
other 749 rows is unmeasured.
