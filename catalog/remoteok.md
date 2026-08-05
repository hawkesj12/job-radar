---
name: remoteok
display_name: Remote OK
status: wired
lane: breadth
verdict: >
  Fresh (median 2 days), keyless, and one request gets the whole feed — but the data quality
  is the worst measured. Three quarters of `location` values are truncated to a city and a
  dangling comma, 98% of salaries are the integer 0 rather than null, and the feed is
  mojibake-corrupted in place. Usable as a freshness-led supplement, never as a location
  source. Attribution is a condition of API access.

auth:
  type: none
  env: []
  signup: null
  notes: keyless, no registration

license: # a CONTRACT — quoted, not paraphrased. Served INSIDE the response, at index 0
  commercial_use: unclear
  commercial_terms: >
    The notice is about attribution and trademark, not commerce: it never addresses whether
    a consumer may earn from the data. What it does make unambiguous is that a link back
    with a followable rel and a named credit is the PRICE of access, enforced by suspension.
    Absent a commercial clause this is `unclear`, and `unclear` is not `allowed`.
  attribution_required: true
  attribution_text: >
    "link back (with follow, and without nofollow!) to the URL on Remote OK and mention
    Remote OK as a source" — note the explicit requirement that the link NOT be nofollowed
  redistribution: unclear
  derivative_works: unclear
  cache_policy: none_stated
  on_termination: "API access suspended for non-attribution"
  personal_data: none # employer postings only
  terms_url: https://remoteok.com/api # the terms ARE the payload's index 0
  read_at: 2026-08-03
  read_depth: full
  verbatim:
    - "API Terms of Service: Please link back (with follow, and without nofollow!) to the URL on Remote OK and mention Remote OK as a source, so we get traffic back from your site. If you do not we'll have to suspend API access."
    - "Please don't use the Remote OK logo without written permission as it's a registered trademark, please DO use our name Remote OK though."

endpoint:
  base: https://remoteok.com/api
  method: GET
  slug_pattern: null

query:
  title_search: false # MEASURED — nine parameter names, none filtered
  location_search: false
  filters: []
  param_validation: ignores_unknown

limits:
  page_size: 100 # 101 elements returned; index 0 is metadata, not a job
  max_page: null # `page` is ignored — page 999 returns the same 101 elements
  reachable_per_query: 100
  rate_limit: unknown # not probed
  quota: unknown
  concurrency_safe: unknown

volume:
  advertised: null
  reachable: 100 # one flat feed; there is nothing behind it to page to
  measured_at: 2026-08-03

freshness:
  median_age_days: 2
  pct_within_100d: 1.0
  date_sorted: unknown # a single page, so there is nothing to sort across
  measured_at: 2026-08-03

coverage:
  countries: [multi]
  us_share: null # unresolvable — see location; the field rarely names a country
  sector_skew: "remote tech-led, but the sampled feed included warehouse and production roles"

location:
  shape: free_text
  primary_path: location
  type: string
  state_available: false
  state_path: null
  city_path: null
  country_path: null
  multi_value: unknown
  multi_delimiter: null
  free_text_fallback: location
  gazetteer_needed: true
  notes: >
    Broken, not merely unstructured. 75 of 100 sampled rows have a `location` that ends in a
    bare comma — "Cambridge, ", "Crossville, ", "Sonipat, " — a city with whatever followed
    it truncated away. The captured row shows where it went: `location` reads "Cambridge, "
    while the DESCRIPTION opens "Cambridge, ON, CA, N1R 5Y2". The province, country and
    postcode exist in the payload, just not in the location field. Recovering them means
    parsing the body, which is a project rather than a mapping.

remote:
  signal: derived
  path: null
  rule: "every posting is remote by construction — Remote OK lists nothing else"
  reliability: "100 of 100; the site is remote-only, so the signal is the source itself"

fields:
  title: { path: position, type: str } # NOT `title`
  company: { path: company, type: str }
  body: { path: description, type: str, median_chars: 2052 } # HTML
  posted: { path: date, type: str } # ISO 8601 with offset; `epoch` carries the same instant
  url: { path: url, type: str }
  tags: { path: tags, type: list }
  salary: { path: [salary_min, salary_max], type: int } # 0 means "not stated" — see traps
  employment_type: null
  function: null
  org_unit: null
  employer_org: null
  seniority: null
  # unmapped keys in the record: apply_url, company_logo, epoch, id, logo, slug

traps:
  - "**Index 0 is not a job.** The response is a flat array whose first element is `{last_updated, legal}`. A consumer that iterates without skipping it ingests the licence notice as a posting."
  - '`location` is TRUNCATED in 75 of 100 rows — it ends in a bare comma. The full location survives at the START of `description` ("Cambridge, ON, CA, N1R 5Y2"), so the data exists but not where you would look for it.'
  - "The title field is `position`, not `title`."
  - "`salary_min`/`salary_max` are the integer **0** in 98 of 100 rows, not null. Anything treating 0 as a real figure will report a feed of unpaid jobs, and any min/max filter silently matches them."
  - "`apply_url` is byte-identical to `url` in 100 of 100 rows — it is not a separate apply link."
  - "`company_logo` and `logo` are two keys holding the same value, both empty in the captured row."
  - 'The feed is **mojibake-corrupted at source**: a sampled location read "GonaÃ¯ves" — UTF-8 bytes decoded as Latin-1 before being served. Re-decoding downstream cannot reliably fix it and it will appear in user-facing text.'
  - "Attribution must be a FOLLOWED link. The notice explicitly forbids `nofollow`, which is a stricter requirement than any other source here and easy to violate by default in a template that nofollows outbound links."
---

# remoteok

## A real record

Captured 2026-08-03, the first job element (array index 1 — index 0 is the licence object).
Description truncated with a marker; rest verbatim.

```json
{
  "slug": "remote-general-production-dana-solutions-llc-1136069",
  "id": "1136069",
  "epoch": 1785719389,
  "date": "2026-08-03T01:09:49+00:00",
  "company": "Dana Solutions LLC",
  "company_logo": "",
  "position": "General Production",
  "tags": ["education", "content writing", "non tech"],
  "description": "Cambridge, ON, CA, N1R 5Y2<br><br>Requisition: 66687<br><br><strong>Job Purpose<br><br>…[truncated, 2052 chars median]",
  "location": "Cambridge, ",
  "apply_url": "https://remoteOK.com/remote-jobs/remote-general-production-dana-solutions-llc-1136069",
  "salary_min": 0,
  "salary_max": 0,
  "logo": "",
  "url": "https://remoteOK.com/remote-jobs/remote-general-production-dana-solutions-llc-1136069"
}
```

**What this record proves that the field table did not.** Put `location` and `description` side
by side and the defect is unmistakable: the field says `"Cambridge, "` and the description
begins `"Cambridge, ON, CA, N1R 5Y2"`. This is not a source that omits location data — it is a
source that has the data and truncates it on the way out, leaving the comma behind as evidence
of what was cut. Measured across the feed, 75 of 100 rows do this.

The record also shows two things a schema would call populated and a human would not:
`salary_min` and `salary_max` are `0`, which is a sentinel for "not stated" wearing the costume
of a number, and `apply_url` is character-for-character the same string as `url`.

Finally, note `tags`: `["education", "content writing", "non tech"]` on a warehouse production
job. The tags are approximate at best.

## How this was probed

`catalog/_probe.py` on 2026-08-03, one request per second. The licence was read the same day
from index 0 of the response itself and is quoted verbatim above. Field-quality percentages
come from one further request over the whole 100-row feed.

```
junk:  control 200 (101 rows) vs junk 200 (101 rows) -> ignores_unknown
title: nine parameter names tried; filters on: nothing
pages: `page` appears IGNORED — page 999 returned the same 101 elements as the control
fresh: 100 dated rows, median 2.0d, 100% within 100d
body:  median 2052 chars over 101 rows
quality (100 rows): location ends in a bare comma 75/100 · salary_min == 0 98/100 ·
        apply_url == url 100/100
```

**Not checked:** the rate limit (`unknown` — no burst was run against any source). Whether the
100-row feed is the entire corpus or a fixed window onto a larger one: `page` is ignored and no
`total` is published, so there is no way to tell from the API, and Remote OK's own site
advertises far more than 100 open roles. Also unchecked: whether the truncated `location` can
be recovered reliably from the first line of `description` across the whole feed rather than in
the rows sampled here.
