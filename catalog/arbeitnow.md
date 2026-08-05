---
name: arbeitnow
display_name: Arbeitnow
status: wired
lane: breadth
verdict: >
  The deepest reachable keyless corpus measured — roughly 5,950 rows over 34 pages with long
  bodies — and it is a GERMAN board, so its value here is breadth of employer rather than US
  coverage. Two properties matter more than the size: it serves a rotating slice, which makes
  every difference-based measurement against it a lie, and it has a real `remote` boolean.
  There is no terms page of any kind.

auth:
  type: none
  env: []
  signup: null
  notes: >
    Keyless and publicly documented in the author's own blog post. A sibling UK API exists at
    `arbeitnow.co.uk/api/job-board-api` with the same shape and was NOT probed.

license: # a CONTRACT — quoted, not paraphrased
  commercial_use: unclear
  commercial_terms: >
    The terms of service carry a dedicated API paragraph, and it GRANTS revocable permission
    rather than staying silent: it disclaims warranties, requires a link back, and reserves the
    right to revoke. That makes attribution a stated condition and access an acknowledged
    permission. What it never addresses is commercial use, redistribution or derivative works,
    so `commercial_use` stays `unclear` — but this is a narrower `unclear` than a source with no
    terms at all, because a permission exists and has one named condition attached to it.
  attribution_required: true
  attribution_text: "a link back to Arbeitnow.com on your platform"
  redistribution: unknown # never addressed
  derivative_works: unknown # never addressed
  cache_policy: none_stated
  on_termination: >
    "We may revoke the permission to use API at any time" — a revocation right with no notice
    period and no data-deletion duty stated
  personal_data: none # employer postings only, no candidate data in the response
  terms_url: https://www.arbeitnow.com/terms
  docs_url: https://www.arbeitnow.com/blog/job-board-api
  read_at: 2026-08-03
  read_depth: full # the terms of service and the API announcement post
  verbatim:
    - 'The API is provided by us on an "as is" and "as available" basis without any warranties of any kind, and we expressly disclaim any and all warranties, whether express or implied, including the implied warranties of fitness for a particular purpose, and non-infringement.'
    - "You also agree to providing a link back to Arbeitnow.com on your platform."
    - "We may revoke the permission to use API at any time."
    - "These terms and conditions are governed by and construed in accordance with the laws of Berlin and you irrevocably submit to the exclusive jurisdiction of the courts in that State or location."
    - "You can find the Free Job Search API on the job board, requires no API key and API has been documented."
    - "To find jobs with visa sponsorship, you can use the parameter visa_sponsorship and set it to true or false."

# ── ADMISSION TEST — keyless is not the same as permitted. Both halves, separately.
admission:
  documented_public_api: true # announced and documented by the operator in his own blog post
  terms_permit_automated_access: true # a revocable permission with an attribution condition
  verdict: >
    PASSES both halves, and it is the only source in this catalog that passes the second half
    on an explicit grant rather than on silence. The API is publicly announced by Arbeitnow's
    own author, and the terms name it and permit it. The condition — a link back — is not
    currently satisfied by anything in this package.

endpoint:
  base: https://www.arbeitnow.com/api/job-board-api
  method: GET
  slug_pattern: null

query:
  title_search: false # MEASURED — seven parameter names tried, none filtered. See traps
  location_search: unknown # not probed
  filters: [page, visa_sponsorship] # visa_sponsorship is DOCUMENTED and was never tried
  param_validation: ignores_unknown

limits:
  page_size: 175 # measured from the unfiltered response, not documented
  max_page: 34 # walked; page 35 returned no rows, confirmed on retry
  reachable_per_query: 5950 # 175 x 34 — the deepest keyless corpus in this catalog
  rate_limit: unknown # none published on any Arbeitnow page; not probed
  quota: unknown
  concurrency_safe: unknown

volume:
  advertised: null # the response envelope carries links/meta that were not recorded
  reachable: 5950
  measured_at: 2026-08-03

freshness:
  median_age_days: 0
  pct_within_100d: 1.0 # █████
  date_sorted: false # MEASURED — p0 median 0d, p25 median 3d; a 3-day spread over 25 pages
  measured_at: 2026-08-03
  note: >
    A 0-day median with `date_sorted: false` is not a contradiction — the whole reachable
    corpus is days old, so paging deeper barely ages it. That also means a consumer cannot
    page to "the fresh part" because effectively all of it is fresh, and must cut at ingest if
    it wants anything narrower.

coverage:
  countries: [DE, EU] # the captured row is London; the board is German-language and German-market
  us_share: 0.0 # no US rows observed; this is a Europe-facing board
  sector_skew: >
    all sectors, and notably NOT tech-only — the sampled feed includes logistics, executive
    recruiting and industrial roles across Düsseldorf, Munich, Cologne and Ludwigsfelde

location:
  shape: free_text
  primary_path: location
  type: string
  state_available: false # no state, country or postal field exists anywhere in the record
  state_path: null
  city_path: null
  country_path: null
  multi_value: unknown
  multi_delimiter: null
  free_text_fallback: location
  gazetteer_needed: true
  notes: >
    A bare city name — "London" on the captured row, "Ludwigsfelde, Brandenburg" and
    "Kabelsketal, Saxony-Anhalt" elsewhere in the sampled feed. So the string sometimes carries
    a German federal state and sometimes does not, with no field to say which, and there is no
    country anywhere in the record at all. For a board serving several European markets that is
    the hardest shape to normalize: you cannot tell Cologne from Cologne without a gazetteer.

remote:
  signal: structured
  path: remote
  rule: "remote == true"
  reliability: >
    A real boolean, documented by the author as "a field remote which indicates if the job
    posting is a remote job or not", and `false` on the captured row. One of the few honest
    remote signals in the breadth lane; its accuracy across the feed was not measured.

fields:
  title: { path: title, type: str }
  company: { path: company_name, type: str } # lowercase and unbranded — "alixpartners"
  body: { path: description, type: str, median_chars: 6606 } # DOUBLE-escaped HTML — see traps
  posted: { path: created_at, type: int } # epoch SECONDS
  url: { path: url, type: str }
  tags: { path: tags, type: list } # counts baked into the strings — see traps
  salary: null
  employment_type: { path: job_types, type: list } # EMPTY on the captured row
  function: null
  org_unit: null
  employer_org: { path: company_name, type: str } # the employing organisation
  seniority: null
  # unmapped keys in the record: slug, remote, location

traps:
  - "IT SERVES A ROTATING SLICE. Every request returns a different-looking body, including requests carrying parameters the API has never heard of. This defeated the first version of `_probe.py`'s title test outright: a difference-based check called all seven parameter names real, when `search=nurse` simply returns 175 rows of unrelated titles. It is the reason that test now judges by RELEVANCE instead of by change, and it means any measurement of this source that compares two responses is measuring the rotation."
  - "`description` is DOUBLE-escaped HTML, exactly like Greenhouse: the captured row reads `&lt;div&gt;`, which decodes to the literal text `<div>`. Unescape, THEN strip tags; the other order silently leaves entities in the output."
  - '`tags` have COUNTS baked into the string: the captured row''s only tag is `"Organizational Development (515)"`. That is a tag name plus the number of jobs carrying it, in one value. Grouping on it produces a new tag every time the count moves.'
  - "`visa_sponsorship=true|false` is a DOCUMENTED parameter that this probe never tried. Because unknown parameters are silently ignored here, its absence from the measured filter list is not evidence it does not work — the seven names tested were the generic ones, and the vendor's own documented filter was not among them."
  - "`job_types` was an EMPTY array on the captured row despite the job being a full-time directorship. The field exists and is not reliably populated."
  - "There is no country field, and `location` sometimes carries a German state and sometimes just a city. Two different European cities can share a name and this record cannot disambiguate them."
  - '`company_name` is lowercase and unbranded ("alixpartners", not "AlixPartners"). Deduplication and display both want the proper form and the API does not send it.'
  - "A sibling UK API exists at `arbeitnow.co.uk/api/job-board-api`. It was NOT probed, so nothing here describes it."
  - 'ATTRIBUTION IS REQUIRED and it is easy to miss, because the clause is not on the API documentation page — it is a paragraph inside the general terms of service, between the payment terms and the affiliate disclosure. "You also agree to providing a link back to Arbeitnow.com on your platform." Nothing in this package currently does that.'
  - "Permission is REVOCABLE by the operator at any time, with no notice period stated. That is a different risk shape from a source with no terms: there is something to lose."
  - "Rows are re-syndicated from other ATSs — Greenhouse, SmartRecruiters, Join.com, Teamtailor, Recruitee and Comeet, per the operator. So an Arbeitnow row may be the SAME posting job-radar already harvested directly from a Greenhouse or Teamtailor board, arriving with a different URL and a different company spelling. That is a deduplication problem this catalog has not measured."
---

# arbeitnow

## A real record

Captured 2026-08-03 from `www.arbeitnow.com/api/job-board-api`, first row. Description truncated
with a marker; rest verbatim and complete.

```json
{
  "slug": "executive-recruiting-director-emea-london-201349",
  "company_name": "alixpartners",
  "title": "Executive Recruiting Director – EMEA",
  "description": "&lt;div&gt;At AlixPartners, we solve the most complex and critical challenges by moving quickly from analysis to action when it really matters; creating value that has a lasting impact on companies, their people, and the communities they serve…[truncated, 10399 chars total]",
  "remote": false,
  "url": "https://www.arbeitnow.com/jobs/companies/alixpartners/executive-recruiting-director-emea-london-201349",
  "tags": ["Organizational Development (515)"],
  "job_types": [],
  "location": "London",
  "created_at": 1785801338
}
```

**What this record proves that the field table did not.** Three things, all of them small
strings hiding work.

`tags` is `["Organizational Development (515)"]`. That trailing `(515)` is a job count — how
many postings carry the tag right now — concatenated into the tag's own name. It is a display
string from the website that leaked into the API. Anything that groups, filters or joins on
tags here creates a new distinct value every time the count changes, so the same tag fragments
into dozens of variants over a week.

`description` opens `&lt;div&gt;`. Those characters are not markup; they decode to the text
`<div>`. Greenhouse does the same thing and both need unescaping before tag-stripping, in that
order, or the output contains either raw entities or raw HTML depending on which single step
you ran.

And `company_name` is `"alixpartners"` while the description's own first sentence says
"AlixPartners". The API sends the slug-cased form, so the displayed employer name is wrong in
casing on every row and the only correctly-cased copy is inside the prose.

What the record gets right is worth stating plainly: `remote` is a real boolean. Most of the
breadth lane makes remoteness something you grep out of a location string, and this source
simply answers the question.

## The rotating slice, and why it broke a measurement

This is the source that forced `_probe.py`'s title test to be rewritten, so the finding belongs
here rather than in a code comment.

Arbeitnow serves a rotating subset of its corpus. Two identical requests a second apart return
different rows. The first version of the title test asked "did the response change when I added
this parameter?" — and against a rotating feed the answer is yes for `q`, yes for `query`, yes
for `search`, yes for a parameter that does not exist. All seven names scored as working
filters. `search=nurse` returns 175 rows of executive recruiters and logistics managers.

The test now asks whether the response is ABOUT the term, measured as the share of returned
titles containing it, and Arbeitnow scores 0% on all seven — which is the correct answer.

The generalizable point: on any source where the unfiltered response is non-deterministic, a
before/after comparison measures the source's churn, not the parameter. That is also why
`devitjobs`'s 1,581-versus-1,576 junk-parameter result is churn rather than validation, and why
`param_validation: ignores_unknown` should be read here as "a typo is a permanent, invisible
no-op" — including, quite possibly, on the documented `visa_sponsorship` filter nobody tested.

## The probe log

```
junk:  control 200 (175 rows) vs junk 200 (175 rows) -> ignores_unknown
title: control: 175 rows, 0% already match 'nurse'
title: q / query / search / keyword / keywords / title / name = nurse -> 200, 175 rows, 0% relevant
title: filters on: nothing
pages: last page returning rows: 34 (first failure at 35)
fresh: 275 dated rows across pages [0, 25]; p0 median 0d, p25 median 3.0d -> NOT date-sorted
body:  median 6606 chars over 274 rows
```

## Where the API terms actually were

This profile first recorded `terms_url: null` and "NOTHING WAS FOUND TO READ", on the strength
of the API announcement post containing no terms — which it does not. That was wrong, and the
way it was wrong generalizes.

Arbeitnow's API terms are one paragraph inside `arbeitnow.com/terms`, sitting between the job-
posting payment terms and an affiliate-link disclosure. Nothing links them from the API
documentation, and the paragraph is not headed "API" in any navigable way. Searching the
developer-facing surface finds nothing; only reading the general terms end to end surfaces it.

Across this catalog, API-specific terms have now turned up in four different places:

- **inside the response payload** — Remotive (`0-legal-notice`), Remote OK (row 0's `legal`)
- **on a dedicated API-terms page the footer does not link** — We Work Remotely
- **buried mid-document in the general terms** — Arbeitnow, this source
- **in the main terms of service, undivided** — TechTree, Jobspresso, landing.jobs

So "the terms of service say nothing about the API" is an unfinished search, not a finding, and
this profile is the evidence for that rule rather than an exception to it.

## How this was probed

`catalog/_probe.py` on 2026-08-03, one request per second. The API announcement post and the
full terms of service were both read the same day; the licence is quoted verbatim above.

**Not checked, and one item here is a real gap.** The DOCUMENTED `visa_sponsorship` parameter
was never sent — the probe tried seven generic names and not the vendor's own, which is the
same mistake that made Jobicy's `tag` look absent. The rate limit stays `unknown`: no figure is
published anywhere on an Arbeitnow page, and third-party claims of "generous rate limits" were
not treated as evidence. The UK sibling API was not probed at all. `location_search` was never
tested. The accuracy of the `remote` boolean across the feed is unmeasured — only its presence
is established. And the overlap with the ATS boards job-radar already harvests directly is
unquantified: Arbeitnow re-syndicates Greenhouse and Teamtailor rows, and how many of its 5,950
are duplicates of the depth lane was not measured.
