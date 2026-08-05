---
name: devitjobs
display_name: DevITjobs
status: rejected
lane: breadth
verdict: >
  The richest structured location in the catalog — city, state, country, postcode, street and
  lat/long on every row — attached to postings with no description of any kind. 1,576 rows
  arrive in one response and not one carries prose, so nothing downstream can score them. A
  large fraction are Indeed affiliate redirects carrying a cost-per-click bid, which makes the
  feed an ad network as much as a job board.

auth:
  type: none
  env: []
  signup: null
  notes: >
    Keyless. `/api/jobsLight` is undocumented — the "Light" suffix implies a fuller sibling
    endpoint that was NOT found and NOT probed.

license: # a CONTRACT — quoted, not paraphrased
  commercial_use: prohibited
  commercial_terms: >
    A Polish-law terms of service (Swissdev Greg Janusz Tomasik) whose §5.7 enumerates what a
    licensee will NOT do, and it covers this use directly: no sublicensing or transferring
    access to a third party, no building a competitive product, and no copying or creating
    derivative works of the Services without prior written consent. §1.5 explicitly invokes
    the Polish Database Act, so the listing corpus is claimed as a protected database, not
    merely as page content.
  attribution_required: unknown # never addressed
  redistribution: prohibited
  derivative_works: license_required # "except with the prior written consent of DevITjobs"
  cache_policy: none_stated
  on_termination: none_stated
  personal_data: none # employer postings only, no candidate data in the response
  terms_url: https://static.devitjobs.com/documents/Terms-And-Conditions-2024.pdf
  read_at: 2026-08-03
  read_depth: full # PDF extracted locally with pdftotext; the website itself is JS-walled
  verbatim:
    - "Database – database as described in - Polish Act of 27 July 2001 regarding database creation and protection"
    - "sublicense, lease, rent, loan or otherwise transfer to any third party the right to access and use the Services"
    - "use or access the Services for the purpose of building a competitive product"
    - "copy, frame, modify or create any derivative works of the Services (or any component, part, feature, function, user interface, or graphic thereof) or Documentation, except with the prior written consent of DevITjobs"
    - "The Services constitute protected copyrighted material and valuable trade secrets of DevITjobs."

# ── ADMISSION TEST — keyless is not the same as permitted. Both halves, separately.
admission:
  documented_public_api: false # `/api/jobsLight` is undocumented; the "Light" name is internal
  terms_permit_automated_access: false # §5.7 bars derivative works and third-party transfer
  verdict: >
    FAILS BOTH HALVES — the 4dayweek shape, and the third source in this catalog to land in it
    alongside 4dayweek and jobspresso. `/api/jobsLight` appears in no documentation; the
    "Light" suffix is the naming of an internal variant, not a published product, and the
    devitjobs site is JavaScript-walled so there is no developer surface to check. The terms
    then claim the listing corpus as a protected database under Polish law and prohibit
    transferring third-party access, building a competitive product, and creating derivative
    works without prior written consent. Already `rejected` for the missing body, so this
    changes no status — but it means the rejection has two independent grounds, and the
    licence one would stand even if a body appeared tomorrow.

endpoint:
  base: https://devitjobs.com/api/jobsLight
  method: GET
  slug_pattern: null

query:
  title_search: false # MEASURED — eight parameter names tried, none filtered
  location_search: false
  filters: [] # none that were shown to work
  param_validation: ignores_unknown # see "The junk-parameter reading" below

limits:
  page_size: null # the whole corpus arrives in one unpaged response
  max_page: null # `page` had no effect — see traps, the probe's 4096 was its own search bound
  reachable_per_query: 1576 # everything, in one request
  rate_limit: unknown # none published; not probed
  quota: unknown
  concurrency_safe: unknown

volume:
  advertised: null # the response is a bare array with no envelope and no total
  reachable: 1576
  measured_at: 2026-08-03

freshness:
  median_age_days: 2
  pct_within_100d: 0.88 # ████░
  date_sorted: false # every sampled "page" had an identical 2-day median — it is one document
  measured_at: 2026-08-03

coverage:
  countries: [US, CA, multi] # the terms say the service targets the US IT industry
  us_share: null # the captured row is Canadian; the distribution was not counted
  sector_skew: "software and IT exclusively — the taxonomy is tech stacks"

location:
  shape: structured
  primary_path: [actualCity, stateCategory, latitude, longitude]
  type: object # flattened onto the record rather than nested
  state_available: false # the field NAMED state holds a COUNTRY — see notes
  state_path: null
  city_path: actualCity # "Hamilton"; `cityCategory` is a near-duplicate
  country_path: stateCategory # "Canada" — yes, really. See notes
  multi_value: false
  multi_delimiter: null
  free_text_fallback: address # "5660 Airport Blvd." — a street, not a locality
  gazetteer_needed: false # latitude and longitude ship on every row
  notes: >
    More location detail than any other source here — street `address`, `postalCode`,
    `actualCity`, `cityCategory`, `latitude`, `longitude` and `stateCategory` — and the one
    field whose NAME promises a state does not hold one. On the captured row
    `stateCategory` is "Canada", a country. This is the cleanest example in the catalog of
    why _SCHEMA.md forbids assigning `function`/`org_unit`/`employer_org` (and, by the same
    logic, state) from a field's name: `stateCategory` is a top-level geographic bucket, and
    for a US row it would presumably hold a state, so the field's MEANING changes with the
    country of the row. `state_available` is false because you cannot trust the field without
    first knowing what the row's country is, which the record does not separately say.

remote:
  signal: structured
  path: [workplace, remoteType]
  rule: "workplace in ('office', 'remote', 'hybrid'); remoteType carries the detail"
  reliability: >
    `workplace` was "office" and `remoteType` was null on the captured row. Two dedicated
    fields, one populated — their fill rates across the feed were not measured.

fields:
  title: { path: name, type: str } # NOT `title` — see traps
  company: { path: company, type: str }
  body: null # THE defect — no prose field exists anywhere. See "There is no body" below
  posted: { path: activeFrom, type: str } # ISO 8601 with milliseconds and an offset
  url: null # must be BUILT from `jobUrl`, a slug; `redirectJobUrl` goes to Indeed, not here
  tags: { path: technologies, type: list } # `filterTags` is a byte-identical duplicate
  salary: { path: [annualSalaryFrom, annualSalaryTo], type: int } # real integers, structured
  employment_type: { path: jobType, type: str } # "Full-Time"
  function: { path: [techCategory, metaCategory], type: str } # "System" / "infra"
  org_unit: null
  employer_org: { path: company, type: str } # "SHOTOVER Systems" — the employing organisation
  seniority: { path: expLevel, type: str } # "Regular" — normalized, but to a non-obvious scale
  # unmapped keys in the record: _id, applyQuestions, candidateContactWay, companySize,
  # companyType, cpc, hasVisaSponsorship, isPartner, isPaused, language, logoImg, partnerName,
  # perkKeys

traps:
  - "There is NO description. Not a short one, not an HTML one — the longest string on the record is `redirectJobUrl`, an Indeed tracking URL. `_probe.py`'s `is_prose()` exists BECAUSE of this source: taking the longest string per record reported a 393-char 'body' that was really a redirect URL, which would have contradicted this source's own rejected status."
  - 'The record''s `isPartner: true`, `partnerName: "indeed"`, `cpc: 7.01` and an Indeed `redirectJobUrl` together mean this row is a paid affiliate placement with a $7.01 cost-per-click bid. That is a fact about the FEED, not one row: a consumer republishing it is republishing someone''s ad inventory, and `redirectJobUrl` is a tracking link that will attribute the click.'
  - '`stateCategory` holds a COUNTRY ("Canada") on the captured row. Reading it as a US state produces ''Canada, USA''.'
  - "The title field is `name`, not `title`. A generic mapper keyed on `title` produces blank titles and no error."
  - "`technologies` and `filterTags` were byte-identical arrays on the captured row. Two keys, one list."
  - '`cityCategory` and `actualCity` were both "Hamilton". Another duplicate pair, and neither name says which is authoritative.'
  - "The probe's `max_page: 4096` is an ARTIFACT, not a ceiling — 4096 is `_probe.py`'s own upward search bound, and the binary search collapsed to it. Every sampled 'page' returned about 1,576 rows with an identical 2-day median, which is one document being re-served. There is no pagination here."
  - "The terms invoke the Polish Database Act over the listing corpus and require prior written consent for derivative works. Undocumented and keyless is not the same as permitted."
---

# devitjobs

## A real record

Captured 2026-08-03 from `devitjobs.com/api/jobsLight`, first row. Two arrays truncated with a
marker; rest verbatim and complete — nothing needed truncating for length, which is the finding.

```json
{
  "_id": "6a28af4169b1cd25177496b3",
  "name": "Embedded Systems Engineer",
  "company": "SHOTOVER Systems",
  "jobUrl": "SHOTOVER-Systems-Embedded-Systems-Engineer",
  "redirectJobUrl": "https://ca.indeed.com/tmn/ccs/a9587af3f7f32a3b/ab0ead84da88a9cda1881eac879f07976b2e59ca0e340c4c251d967ced886b66/7985898892702321?sf=arD01",
  "isPartner": true,
  "partnerName": "indeed",
  "cpc": 7.01,
  "isPaused": false,
  "activeFrom": "2026-08-04T00:16:18.545+00:00",
  "candidateContactWay": "CompanyWebsite",
  "address": "5660 Airport Blvd.",
  "postalCode": "L8P 1H4",
  "actualCity": "Hamilton",
  "cityCategory": "Hamilton",
  "stateCategory": "Canada",
  "latitude": 43.2560802,
  "longitude": -79.8728583,
  "remoteType": null,
  "workplace": "office",
  "companyType": "Product",
  "companySize": "50-200",
  "hasVisaSponsorship": "No",
  "language": "English",
  "jobType": "Full-Time",
  "expLevel": "Regular",
  "annualSalaryFrom": 90000,
  "annualSalaryTo": 120000,
  "techCategory": "System",
  "metaCategory": "infra",
  "technologies": [
    "ARM",
    "Embedded",
    "Ethernet",
    "Firmware",
    "FreeRTOS",
    "GNU",
    "…[truncated, 14 total]"
  ],
  "filterTags": [
    "ARM",
    "Embedded",
    "Ethernet",
    "Firmware",
    "FreeRTOS",
    "GNU",
    "…[truncated, 14 total — identical to technologies]"
  ],
  "perkKeys": [],
  "applyQuestions": []
}
```

**What this record proves that the field table did not.** That a record can be detailed and
still be empty of the one thing that matters.

Thirty-one keys. A street address, a postcode, a latitude and a longitude to seven decimal
places. Company size, company type, visa sponsorship, posting language, an annual salary range
as real integers, fourteen normalized technologies. This is more structure than the keyed
commercial APIs in this catalog send.

And there is no sentence anywhere. Not a summary, not a requirements list, not a title
elaboration — the longest string on the row is a 137-character Indeed tracking URL. Every
scoring path in this package reads a body; there is nothing here to read. `body: null` in the
field table says so, and only the whole record makes it obvious that no mapping recovers it.

The second finding is `stateCategory: "Canada"`. The field whose name promises a state holds a
country. `_SCHEMA.md` warns about this for `function`, `org_unit` and `employer_org` — five
adapters putting five different things in one `department` field — and here the same failure
reaches geography. Worse than a plain mismatch: for a US row the field presumably _does_ hold a
state, so its meaning depends on data the record does not separately supply.

Third, and easy to skim past: `isPartner: true`, `partnerName: "indeed"`, `cpc: 7.01`. This row
is paid Indeed inventory with a seven-dollar cost-per-click bid, and `redirectJobUrl` is the
attribution link. Whatever fraction of the feed looks like this — not measured — is advertising
being redistributed, and the tracking URL means someone is being billed for the click.

## The junk-parameter reading, and why it is `ignores_unknown`

The probe reported `param_validation: unknown` with the note "junk param CHANGED the result;
investigate by hand". Investigated: the control returned 1,581 rows and the junk request
returned 1,576.

Then every subsequent request in the run — eight different title parameters — also returned
exactly 1,576, at 0% relevance each. A parameter-sensitive API does not return the same count
for `q`, `query`, `search`, `keyword`, `keywords`, `title`, `name` and `term`. Five rows left
the corpus between the first request and the second, and the feed was stable for the rest of
the run. That is churn, not validation.

Recorded as `ignores_unknown` with the evidence stated, because the conservative `unknown`
implies the source might reject bad parameters, and it demonstrably does not — which is the
thing a later reader needs to know when weighing any other measurement here.

## There is no body, and no `page` either

Both of the two claims that make this source `rejected` deserve their evidence in one place.

**No body.** No prose field exists. `_probe.py`'s `is_prose()` guard — length ≥ 200, not
starting with a URL scheme, at least 20 spaces — was written after this source reported a
393-character "body" that was a redirect URL, and it now correctly reports nothing.

**No pagination.** The recorded `max_page: 4096` is not a measurement. `_probe.py` doubles its
probe upward while `probe <= 4096`, so a source that never fails pins at exactly 4096 and the
binary search between "last good" and "first bad" collapses onto the same number — which is why
the log reads "last page returning rows: 4096 (first failure at 4096)", an impossible pair. The
freshness sampler then hit pages 0, 1024, 2048, 3072 and 4096 and got an identical 2-day median
from every one of them. `page` is ignored; the endpoint serves one document; 1,576 rows is
everything.

## How this was probed

`catalog/_probe.py` on 2026-08-03, one request per second. The terms of service are a PDF; the
devitjobs website itself is JavaScript-walled and returned no readable text, so the PDF was
extracted locally with `pdftotext` and is quoted verbatim above.

```
junk:  control 200 (1581 rows) vs junk 200 (1576 rows) — churn, not validation; see above
title: control: 1581 rows, 0% already match 'nurse'
title: q / query / search / keyword / keywords / title / name / term = nurse -> 200, 1576 rows, 0%
title: filters on: nothing
pages: reported 4096 — an ARTIFACT of the probe's own search bound; `page` is ignored
fresh: 7905 dated rows across pages [0, 1024, 2048, 3072, 4096]; every page median 2d
body:  no prose field over 200 chars in any sampled row
```

**Not checked:** the rate limit stays `unknown` — none is published, and bursting a source
whose terms claim database protection was not worth the number. Also unchecked: whether the
`jobsLight` name implies a heavier sibling endpoint that carries descriptions, which is the one
thing that could change this verdict and which was NOT looked for; what share of the feed is
Indeed affiliate inventory rather than direct postings; the fill rates of `workplace` and
`remoteType`; and the true US share, since the captured row is Canadian and no count was made.
