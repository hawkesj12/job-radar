---
name: remotive
display_name: Remotive
status: wired
lane: breadth
verdict: >
  Wired, keyless, and the longest job bodies of any source measured — but on 2026-08-03 the
  public API returned its ENTIRE corpus as 31 rows and ignored every parameter sent to it,
  including the `search=` the adapter builds. Rights are the strictest of any keyless source
  here: attribution is mandatory, redistribution to aggregators is named and forbidden, and
  the real product is a $5k/mo private API.

auth:
  type: none
  env: []
  signup: null
  notes: keyless, no registration

license: # a CONTRACT — quoted, not paraphrased. Shipped INSIDE the response as `0-legal-notice`
  commercial_use: unclear
  commercial_terms: >
    Sharing jobs onward is the granted purpose, and attribution plus a link back is a
    condition of access rather than a courtesy. Using the feed to collect signups or email
    addresses is called out as a breach. Commercial use as such is never addressed, so this
    is `unclear` and not `allowed`.
  attribution_required: true
  attribution_text: "link back to the URL found on Remotive AND mention Remotive as a source"
  redistribution: prohibited # to third-party job platforms, named explicitly
  derivative_works: unclear
  cache_policy: 'advises max 4 GETs/day; "excessive requests will be blocked"'
  on_termination: "API access terminated for non-attribution; no data-deletion clause stated"
  personal_data: none # employer postings only, no candidate data in the response
  terms_url: https://remotive.com/api-documentation
  read_at: 2026-08-03
  read_depth: full # the notice is served in every response; captured verbatim below
  verbatim:
    - "API documentation and access is granted so that developers can share our jobs further."
    - "Please do not submit Remotive jobs to third Party websites, including but not limited to: Jooble, Neuvoo, Google Jobs, LinkedIn Jobs."
    - "Please link back to the URL found on Remotive AND mention Remotive as a source in order to Remotive to get traffic from your listing. If you don't do that, we'll terminate your API access, sorry!"
    - "Jobs displayed are delayed by 24 hours, the goal being that jobs are attributed to Remotive on various platforms."
    - "Displaying our jobs in order to collect signups/email addresses to show a listing constitutes a breach of our terms of services."
    - "We offer a private, paid-for API […] (starting budget is $5k/mo)."
    - "Typically, you only need to GET Remotive job data through this API a couple of times a day (we advise max. 4 times a day) […] excessive requests will be blocked."

endpoint:
  base: https://remotive.com/api/remote-jobs
  method: GET
  slug_pattern: null

query:
  title_search: false # MEASURED — see traps; the adapter sends `search=` and it does nothing
  location_search: false
  filters: [] # none that were shown to work
  param_validation: ignores_unknown

limits:
  page_size: 31 # not a page size — it is the whole corpus the API returned
  max_page: null # no paging parameter had any effect
  reachable_per_query: 31
  rate_limit: "no 429 observed; the notice advises max 4 requests/day and warns of blocking"
  quota: unknown
  concurrency_safe: unknown

volume:
  advertised: 31 # `total-job-count`, which agreed with the rows returned
  reachable: 31
  measured_at: 2026-08-03

freshness:
  median_age_days: 25
  pct_within_100d: 1.0
  date_sorted: unknown # one page exists, so there is nothing to sort across
  measured_at: 2026-08-03

coverage:
  countries: [multi] # stated as regions, not countries — see location
  us_share: null # 7 of 31 rows name the USA inside a region list; not a country field
  sector_skew: "remote-first tech and adjacent — design, engineering, marketing"

location:
  shape: free_text
  primary_path: candidate_required_location
  type: string
  state_available: false # no state anywhere in the record
  state_path: null
  city_path: null
  country_path: null
  multi_value: true # ONE string holds a whole region list
  multi_delimiter: "," # e.g. "Americas, Europe, Asia, Africa, Oceania"
  free_text_fallback: candidate_required_location
  gazetteer_needed: true
  notes: >
    This field is an eligibility region, not a job location — every row is remote, so it says
    where a candidate may live. 12 distinct values across 31 rows, from "Brazil" to
    "Europe, UK, Germany, France, European timezones". Nothing here resolves to a city or a
    state, and a consumer filtering by US state can never satisfy it from this source.

remote:
  signal: derived
  path: null
  rule: "every posting is remote by construction — Remotive lists nothing else"
  reliability: "31 of 31; the site is remote-only, so the signal is the source itself"

fields:
  title: { path: title, type: str }
  company: { path: company_name, type: str }
  body: { path: description, type: str, median_chars: 9646 }
  posted: { path: publication_date, type: str } # ISO 8601, no timezone marker
  url: { path: url, type: str }
  tags: { path: tags, type: list }
  salary: { path: salary, type: str } # free text, empty in the captured record
  employment_type: { path: job_type, type: str } # e.g. "full_time"
  function: { path: category, type: str } # a job family: "Design", "Software Development"
  org_unit: null
  employer_org: null
  seniority: null
  # unmapped keys in the record: company_logo, company_logo_url, id

traps:
  - "EVERY parameter is ignored. `?search=nurse`, `?search=engineer` and `?limit=5` each returned the identical 31 rows as the bare endpoint. `search_remotive` builds `?search={query}` and gets an unfiltered set back — it has no idea it is not filtering."
  - '31 rows is the WHOLE corpus, not a page. `total-job-count` says 31 too, so there is nothing behind it to page to. Any historical "Remotive has thousands of jobs" note is not what the public API serves today.'
  - "The licence rides inside the payload, at key `0-legal-notice`. It is easy to drop as noise during normalization and it is the actual contract."
  - 'Attribution is a CONDITION OF ACCESS, not a nicety — "we''ll terminate your API access". A consumer that displays these rows without linking back and naming Remotive is in breach.'
  - "Rows are deliberately delayed 24 hours. Freshness measured here is capped by that, not by how fast Remotive lists."
  - "`candidate_required_location` is where a candidate may LIVE, not where the job is. Treating it as a job location mis-files every row."
---

# remotive

## A real record

Captured 2026-08-03 from the bare endpoint. Description and tags truncated with a marker; rest
verbatim.

```json
{
  "id": 2091081,
  "url": "https://remotive.com/remote-jobs/design/senior-graphic-designer-2091081",
  "title": "Senior Graphic Designer",
  "company_name": "Lemon.io",
  "category": "Design",
  "tags": [
    ".Net",
    "android",
    "C",
    "C#",
    "C++",
    "data science",
    "golang",
    "illustrator",
    "ios",
    "java",
    "…[truncated]"
  ],
  "job_type": "full_time",
  "publication_date": "2026-07-28T14:23:05",
  "candidate_required_location": "Americas, Europe, Asia, Africa, Oceania",
  "salary": "",
  "description": "<p>Are you a talented Senior Designer looking for a remote job that lets you show your skills …[truncated]"
}
```

**What this record proves that the field table did not.** Two things. First, `category` is a job
**family** ("Design"), so it belongs in `function` — Remotive is one of the few sources whose
category field is not secretly an org unit or an employer name.

Second, and more consequential: `candidate_required_location` is not a location. "Americas,
Europe, Asia, Africa, Oceania" is an eligibility region — where a candidate may live — and there
is no city, state, or country field anywhere in the record. A consumer that maps this field to
"where the job is" produces rows no location filter can match, and 12 distinct values across 31
rows means normalizing it is a parsing project, not a mapping change.

The `tags` array is also unusually rich — 20+ skills on this one row — and that is boost material
the common dict currently drops.

## The measurement that matters

The bare endpoint, `?search=nurse`, `?search=engineer` and `?limit=5` all returned the **same 31
rows in the same order**, first row "Senior Graphic Designer" every time. `total-job-count` also
reads 31, so this is not a page-1 illusion in front of a larger corpus.

That makes `search_remotive`'s `?search={q}` a silent no-op: the adapter builds a filtered URL,
gets the full set back, and every row flows into scoring as though it had matched the query.
Nothing errors, nothing looks broken — the exact failure `param_validation: ignores_unknown`
predicts.

Whether this is a permanent change to Remotive's free tier or a bad day for it, one probe cannot
say. The legal notice advertising a $5k/mo private API is at least consistent with the free feed
having been cut back to a sample.

## How this was probed

`catalog/_probe.py` on 2026-08-03, one request per second, against the live API. Terms were read
the same day from the `0-legal-notice` key served in every response, and are quoted verbatim
above rather than summarized.

```
junk:  control 200 (31 rows) vs junk 200 (31 rows)
title: control: 31 rows, 0% already match 'nurse'
title: q=nurse -> 200, 31 rows, 0% relevant
title: query=nurse -> 200, 31 rows, 0% relevant
title: search=nurse -> 200, 31 rows, 0% relevant
title: keyword=nurse -> 200, 31 rows, 0% relevant
title: keywords=nurse -> 200, 31 rows, 0% relevant
title: title=nurse -> 200, 31 rows, 0% relevant
title: name=nurse -> 200, 31 rows, 0% relevant
title: limit=nurse -> 200, 31 rows, 0% relevant
title: category=nurse -> 200, 31 rows, 0% relevant
title: company_name=nurse -> 200, 31 rows, 0% relevant
title: filters on: nothing
pages: `page` appears IGNORED — page 999 returned the same 31 rows as the control
fresh: 31 dated rows; median 25d, 100% within 100d
body:  median 9646 chars over 31 rows
```

**Not checked:** the rate limit stays `unknown` — the notice asks for at most four requests a day
and says excessive ones are blocked, and deliberately tripping a block on a source that tells you
it blocks was not worth the number. Also unchecked: whether some documented parameter exists that
the eleven names above missed, and whether the 31-row corpus is permanent — that needs a probe on
a different day, not another request today.
