---
name: braintrust
display_name: Braintrust
status: wired
lane: breadth
verdict: >
  Not a job board — a freelance marketplace, and the record shape says so: budgets in dollars
  per task, `expected_hours_per_week: 3`, `openings_left: 100`. It is genuinely date-sorted
  (the only breadth source measured where paging deeper reliably gets older), and it has NO
  description field anywhere, which is the defect that makes 4dayweek and devitjobs `rejected`.
  It is wired only because the adapter manufactures a body out of the metadata.

auth:
  type: none
  env: []
  signup: null
  notes: keyless for reads; the marketplace itself requires an account, the jobs API does not

license: # a CONTRACT — quoted, not paraphrased
  commercial_use: unclear
  commercial_terms: >
    Braintrust's terms (operated by Talent Node) govern use of "the Site Services" and never
    mention an API, a feed, or third-party consumption of job listings. They do prohibit
    mirroring or framing the service without written consent and prohibit accessing it with
    "any unauthorized engine, software, tool, or mechanism" — a clause broad enough to reach an
    unauthenticated API client, though the API is public and unauthenticated by design. Nothing
    grants third-party use, so `unclear`, and `unclear` is not `allowed`.
  attribution_required: unknown # never addressed
  redistribution: unclear # "use, display, mirror or frame" needs express written consent
  derivative_works: unknown
  cache_policy: none_stated
  on_termination: >
    removed content "may not be completely removed and copies…may continue to exist" — a
    caveat against deletion rather than a deletion requirement
  personal_data: none # employer postings only, no candidate data in the response
  terms_url: https://www.usebraintrust.com/terms
  read_at: 2026-08-03
  read_depth: full
  verbatim:
    - "Use, display, mirror or frame the Site Services without Talent Node's express written consent"
    - "Attempt to access or search the Site Services using any unauthorized engine, software, tool, or mechanism"
    - "for your business purposes only and not for personal, household, or consumer use"

endpoint:
  base: https://app.usebraintrust.com/api/jobs/?limit=20
  method: GET
  slug_pattern: null

query:
  title_search: true # CORRECTED on retry — `search=engineer` is 85% title-relevant. See below
  location_search: false
  filters: [search, limit] # plus an API-supplied `next` cursor the adapter follows
  param_validation: ignores_unknown

limits:
  page_size: 20
  max_page: 7 # walked; page 8 returned no rows, confirmed on retry
  reachable_per_query: 140 # the smallest reachable corpus of any wired source here
  rate_limit: unknown # none published; not probed
  quota: unknown
  concurrency_safe: unknown

volume:
  advertised: unknown # the response envelope carries a count that was not recorded
  reachable: 140
  measured_at: 2026-08-03

freshness:
  median_age_days: 7
  pct_within_100d: 1.0 # █████
  date_sorted: true # MEASURED and monotonic — p1 0d, p3 5d, p5 17d, p7 62d
  measured_at: 2026-08-03
  note: >
    The cleanest date-sorted result in the catalog: four sampled pages, ages rising at every
    step with no reversal. A consumer can page until the ages exceed its cutoff and stop,
    instead of fetching everything and filtering at ingest.

coverage:
  countries: [US, multi] # `locations[].country` is an ISO code; the captured row is US-only
  us_share: null # one row is not a distribution; not counted across the feed
  sector_skew: >
    freelance and contract work, heavily AI-training and data tasks. This is a gig lane, not
    a salaried-job lane, and the adapter filters out the lowest-paid labeling crowdwork

location:
  shape: structured
  primary_path: locations
  type: array_of_object
  state_available: false # the KEY exists and was null; see notes
  state_path: locations[].state
  city_path: locations[].city
  country_path: locations[].country # "US" — an ISO code
  multi_value: true # `locations` is an array
  multi_delimiter: null
  free_text_fallback: locations[].location # "United States only"
  gazetteer_needed: false
  notes: >
    A properly shaped location object — `location`, `place_id`, `custom_location`,
    `location_type`, `country`, `state`, `city` — whose city and state were both null on the
    captured row, with `location_type: "custom"` and `custom_location: "united_states_only"`
    carrying the real meaning. So the structure is for real places and the value is an
    eligibility rule; a remote-first marketplace mostly emits the latter. `state_available` is
    false because the slot exists and the data did not.

remote:
  signal: derived
  path: null
  rule: "every posting is remote by construction — the adapter appends '(Remote)' to every row"
  reliability: >
    Braintrust is a distributed talent network and the record carries no workplace field at
    all. There is a `timezones` array (empty on the captured row) which is the closest thing
    to a geography constraint.

fields:
  title: { path: title, type: str }
  company: { path: employer.name, type: str }
  body: null # THE defect — no description field exists. See "There is no body" below
  posted: { path: created, type: str } # ISO 8601 with microseconds and a Z
  url: null # must be BUILT from `id` — https://app.usebraintrust.com/jobs/{id}/
  tags: { path: "main_skills[].name", type: list } # plus a parallel `job_skills` of bare INTs
  salary: {
      path: [budget_minimum_usd, budget_maximum_usd, payment_type],
      type: str,
    } # STRINGS, and per-task
  employment_type: { path: [job_type, contract_type], type: str } # "freelance" / "short"
  function: { path: role.name, type: str } # "Other" — a job family, mostly unhelpfully filled
  org_unit: null
  employer_org: { path: employer.name, type: str } # "Scaler AI Labs" — the employing org
  seniority: null # see traps — _SCHEMA.md cites a `level` field that this record does NOT have
  # unmapped keys in the record: deadline, expected_hours_per_week, id, openings_left,
  # start_date, timezones

traps:
  - 'There is NO description field. Not a short one, not an HTML one — none. The probe found no prose over 200 characters in any sampled row, and the wired adapter SYNTHESIZES a body: `f"{title}. Skills: {skills}. {contract_type} contract, ~{hrs}h/wk."`. Every relevance score, keyword match and LLM re-rank on a Braintrust row is scoring that sentence, not a job description.'
  - '`budget_minimum_usd` and `budget_maximum_usd` are STRINGS ("350.00"), and `payment_type` on the captured row is `per_task` — so 350 is the price of the whole task, not a salary. Comparing it against an annual figure from any other source is comparing a task fee to a year''s pay.'
  - "_SCHEMA.md's field table lists braintrust `level` as an example of a seniority field. The captured record has NO `level` key. Either the field is conditional or the table is stale — `seniority` is left null here rather than mapped to something that was not in the response."
  - "There is no `url` field. It must be built as `https://app.usebraintrust.com/jobs/{id}/`, so a generic mapper produces rows nobody can click."
  - "`main_skills` is an array of objects with a `name`; `job_skills` beside it is an array of bare integer IDs with no names. They look like a pair and only one is readable."
  - "`search=nurse` returned 200 with ZERO rows while every other parameter name returned the full 20, and the probe scored the parameter dead. It is alive: `search=engineer` returns 20 rows at 85% title relevance against a 5% control. A freelance marketplace has no nurses, so the empty answer was CORRECT behaviour from a working filter. Anything reading an older copy of this profile saw `title_search: false` and would have routed a live-fetchable source to the harvest lane."
  - "140 rows is the entire reachable corpus. Page 8 returns nothing, confirmed on retry."
---

# braintrust

## A real record

Captured 2026-08-03 from `app.usebraintrust.com/api/jobs/?limit=20`, first row. Verbatim and
complete — no truncation was needed, which is itself the finding.

```json
{
  "id": 17662,
  "title": "Financial App Data Contributor, AI Training Project",
  "employer": {
    "id": 8816,
    "name": "Scaler AI Labs",
    "link": "/employers/8816/",
    "full_link": "https://app.usebraintrust.com/employers/8816/",
    "has_logo_set": false,
    "logo": null
  },
  "budget_minimum_usd": "350.00",
  "budget_maximum_usd": "350.00",
  "payment_type": "per_task",
  "main_skills": [
    { "id": 2643, "name": "Data Collection", "is_default": true, "order": 427 }
  ],
  "created": "2026-08-03T21:24:48.361742Z",
  "contract_type": "short",
  "deadline": null,
  "timezones": [],
  "expected_hours_per_week": 3,
  "role": { "id": 10, "name": "Other", "name_plural": "Other Professions" },
  "openings_left": 100,
  "job_skills": [121434, 121435],
  "locations": [
    {
      "location": "United States only",
      "place_id": null,
      "custom_location": "united_states_only",
      "location_type": "custom",
      "country": "US",
      "state": null,
      "city": null
    }
  ],
  "start_date": null,
  "job_type": "freelance"
}
```

**What this record proves that the field table did not.** That this is not a job.

`payment_type: "per_task"`, `budget_minimum_usd: "350.00"`, `expected_hours_per_week: 3`,
`openings_left: 100`. One employer is buying three hours a week of data collection from up to a
hundred people at $350 a task. A field table mapping `budget_minimum_usd` to `salary` records
the path correctly and produces a number that means something entirely different from every
other salary in the shortlist — and the strings-not-numbers detail (`"350.00"`) is the smaller
half of that problem.

The second thing is the absence. There is no `description`, no `content`, no `body`, no
`summary`. The whole posting is metadata, and the title is the only prose in it. That is the
exact defect `_SCHEMA.md` gives as its example of when a source is `rejected` — "4dayweek and
devitjobs both return clean, deeply pageable JSON with no description field anywhere, so
nothing downstream can ever score them."

And the location object is a good idea meeting a remote marketplace. Seven keys, room for a
city and a state, `location_type: "custom"`, and the actual value is the string
`"united_states_only"` — a right-to-work rule wearing a place's clothes.

## There is no body, and the adapter knows

Braintrust is `wired` while 4dayweek and devitjobs are `rejected`, on the same defect. The
difference is not in the API, it is in `search_braintrust`, which builds one:

```
text = f"{t}. Skills: {skills}. {j.get('contract_type', '')} contract" + (f", ~{hrs}h/wk." if hrs else ".")
```

So a row reaching the scorer reads roughly _"Financial App Data Contributor, AI Training
Project. Skills: Data Collection. short contract, ~3h/wk."_ That is a defensible workaround —
the skills array is real signal and the sentence is honest about what it is — but it should be
recorded as what it is. Any measurement of "body length" or "description quality" for this
source is measuring `sources.py`, not Braintrust, and rule 7 says never probe from inside a
normalizer.

Recorded as fidelity, not opinion: the API sends no body; the package supplies one.

## The one genuinely best-in-catalog result

`date_sorted: true`, and unlike 4dayweek's, this one is monotonic across every sampled page:
page 1 median 0 days, page 3 median 5, page 5 median 17, page 7 median 62. Four samples, four
increases, no reversal.

That matters more than its small size suggests. A date-sorted feed lets a consumer stop paging
when the ages pass its cutoff; an unsorted one forces a full fetch and a filter at ingest. With
only 140 rows reachable the saving is small here — but it is the cleanest demonstration in the
catalog of what the field is for.

## How this was probed

`catalog/_probe.py` on 2026-08-03, one request per second. Terms read the same day and quoted
verbatim above.

```
junk:  control 200 (20 rows) vs junk 200 (20 rows) -> ignores_unknown
title: control: 20 rows, 0% already match 'nurse'
title: q / query / keyword / keywords / title / name = nurse -> 200, 20 rows, 0% relevant
title: search=nurse -> 200, 0 rows, 0% relevant   <- FALSE NEGATIVE, see below
retry: search=engineer -> 200, 20 rows, 85% title-relevant  <- FILTERS
retry: search=data     -> 200, 20 rows, 30% title-relevant  (matches skills/body too)
title: filters on: ['search']
pages: last page returning rows: 7 (first failure at 8)
fresh: 75 dated rows across pages [1, 3, 5, 7]; p1 0.0d, p3 5.0d, p5 17.0d, p7 62d -> sorted
body:  no prose field over 200 chars in any sampled row
```

**The `search` correction.** The first pass recorded `title_search: false` because `search=nurse`
returned zero rows and `step_title`'s rule was `status == 200 and rows and relevance >= 0.3` — an
empty result fails the `rows` test before relevance is ever considered. Retried the same day with
terms a freelance marketplace could plausibly contain:

```
control        -> 20 rows, 5% already match 'engineer'
search=engineer -> 20 rows, 85% title-relevant
search=data     -> 20 rows, 30% title-relevant
```

85% against a 5% control settles it. `search` is a real keyword filter, this source can serve a
live per-request fetch, and it is the SECOND source caught by this bug after landing.jobs — which
is why the fix went into `_probe.py` rather than into either profile. `search=data` at 30%
suggests the filter reaches skills and body text as well as titles (the captured row's
`main_skills` is "Data Collection"), so treat it as a keyword filter rather than a title-only one.

**Not checked.** The rate limit is `unknown`; none is published and bursting was not run. The response envelope's own count was not recorded, so `volume.advertised` is `unknown`
even though the API almost certainly reports it. `us_share` is null: one row saying "United
States only" is not a distribution. And whether a `level` field appears on other rows — the one
`_SCHEMA.md` cites — was not established across the feed.
