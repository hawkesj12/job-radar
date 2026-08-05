---
name: greenhouse
display_name: Greenhouse
status: wired
lane: depth
verdict: >
  The depth lane's workhorse: one unauthenticated request returns an employer's entire board
  with full bodies inlined, and it is the ONLY ATS here that names the board's owner, which is
  what makes slug identity verifiable at all. Two costs ride along — the body arrives as
  double-escaped HTML, and location is free text with no state field anywhere, so structured
  location is a parsing project here rather than a mapping change.

auth:
  type: none
  env: []
  signup: null
  notes: >
    Keyless for reads. "Job Board data is publicly available, so authentication is not
    required for any GET endpoints"; only the application-submission endpoint needs Basic Auth.

license: # a CONTRACT — quoted, not paraphrased
  commercial_use: unclear
  commercial_terms: >
    The Job Board API documentation states no terms of use, no licence, no attribution
    requirement and no rate limit — it addresses authentication and nothing else. Greenhouse's
    legal index lists a Master Subscription Agreement, but that agreement governs Greenhouse's
    relationship with its employer customers, and its body text could NOT be retrieved to
    check whether it speaks to third parties consuming public board data. Silence in a
    developer doc is not a grant, so this is `unclear`, and `unclear` is not `allowed`.
  attribution_required: unknown # never addressed in the API documentation
  redistribution: unknown
  derivative_works: unknown
  cache_policy: none_stated
  on_termination: none_stated
  personal_data: none # employer postings only; the GET endpoints cannot reach applications
  terms_url: https://www.greenhouse.com/legal
  docs_url: https://developers.greenhouse.io/job-board.html
  read_at: 2026-08-03
  read_depth: summary_only # API doc read in full; the MSA body text could NOT be retrieved
  verbatim:
    - "Job Board data is publicly available, so authentication is not required for any GET endpoints."
    - "Only the application submission endpoint requires Basic Auth."

endpoint:
  base: https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true
  method: GET
  slug_pattern: "boards-api.greenhouse.io/v1/boards/{slug}/jobs"

query:
  title_search: false # whole-board fetch; the boards endpoint has no query interface
  location_search: false
  filters: [content] # `content=true` inlines bodies; without it every row comes back bodyless
  param_validation: ignores_unknown

limits:
  page_size: null # the entire board arrives in one response, unpaged
  max_page: null
  reachable_per_query: null # the whole board, whatever its size — 806 rows for databricks
  rate_limit: unknown # none documented; bursting to find one was not run
  quota: unknown
  concurrency_safe: unknown
  requests_per_company: 1 # with `content=true`, bodies do NOT cost a detail call per role
  cheap_liveness: true # sources.liveness_for has a cheap greenhouse variant

volume:
  advertised: null
  reachable: null # a function of how many slugs you can name
  measured_at: 2026-08-03

freshness:
  median_age_days: 10.0 # measured on `updated_at`, which is NOT the posting date — see traps
  pct_within_100d: 1.0
  date_sorted: unknown # one unpaged document; there is nothing to sort across
  measured_at: 2026-08-03
  note: >
    Measured against ONE board (databricks, 806 rows). One employer's hiring cadence is not
    the platform's, and `updated_at` flatters the number — see traps.

coverage:
  countries: [multi] # the sampled board spans Tokyo and offices worldwide
  us_share: null # per-company by construction; one board is not a platform sample
  sector_skew: "per-company by construction — the board is whatever that employer posts"

location:
  shape: free_text
  primary_path: location.name
  type: string
  state_available: false # full state NAMES appear inside the string, never as a `, XX` field
  state_path: null
  city_path: null
  country_path: null
  multi_value: true
  multi_delimiter: ";" # one posting can read "Bengaluru, India; Mumbai, India"
  free_text_fallback: location.name
  gazetteer_needed: true
  notes: >
    `location.name` was "Tokyo, Japan" in the captured record — city plus country, no state, no
    separate fields. A parallel `offices[]` array looks like it should help and does not: its
    `location` is the same free-text string, so it adds a hierarchy without adding structure.
    Measured 2026-08-03 across the sampled board, 143 of 808 rows carried a semicolon-delimited
    multi-location string, so a consumer assuming one place per row mis-files about 18% of it.

remote:
  signal: none
  path: null
  rule: null
  reliability: >
    There is no remote field of any kind. Remoteness, where present at all, is a word inside
    `location.name` or the title, so any signal is derived by string matching and cannot be
    verified from the record.

fields:
  title: { path: title, type: str }
  company: { path: company_name, type: str } # the board owner
  body: { path: content, type: str, median_chars: 8519 } # DOUBLE-escaped HTML — see traps
  posted: { path: updated_at, type: str } # ISO 8601 with a real offset; see traps
  url: { path: absolute_url, type: str }
  tags: null
  salary: null
  employment_type: null
  function: null
  org_unit: { path: "departments[].name", type: list } # "Field Engineering - Other" — a team
  employer_org: { path: company_name, type: str } # "Databricks" — the employing organisation
  seniority: null
  # unmapped keys in the record: ai_disclaimer, ai_opt_out_request_url, application_deadline,
  # data_compliance, first_published, id, include_ai_disclaimer, internal_job_id, language,
  # metadata, offices, requisition_id

traps:
  - "`content` is DOUBLE-escaped HTML. The captured row reads `&lt;p data-renderer-start-pos=&quot;1648&quot;&gt;` — those entities decode to the literal text `<p …>`, so one unescape pass yields markup where you expected prose, and a tag-stripper run first leaves the entities untouched. Unescape, THEN strip; the other order is silently wrong."
  - "`updated_at` is not the posting date. The captured row carries `updated_at: 2026-08-03` and `first_published: 2026-06-29` — a 35-day-old posting that measures as one day fresh. The 10-day median above inherits this and overstates how new the board is. `first_published` is the honest field and the current adapter does not read it."
  - "`departments[].name` is the company's own org unit, not a job family. Routing it to `function` is the exact mistake _SCHEMA.md exists to prevent."
  - "One posting can list several locations in ONE semicolon-delimited string — 143 of 808 rows on the sampled board. Splitting on the comma instead produces garbage, because the comma already separates city from country."
  - "Without `?content=true` the rows come back with no body at all and the request still returns 200. A 200 is not a pass."
  - "`metadata[]` is employer-defined and its shape varies within a single row: the captured record has a `single_select` whose `value` is a string beside a `multi_select` whose `value` is an array. Anything reading `metadata[].value` must handle both types or it crashes on the second board it meets."
  - "A 404 means the SLUG is wrong, not that the platform is dead."
---

# greenhouse

## A real record

Captured 2026-08-03 from `boards-api.greenhouse.io/v1/boards/databricks/jobs?content=true`,
first row. Body truncated with a marker; rest verbatim.

```json
{
  "id": 8559344002,
  "title": "ソリューションアーキテクト (プリセールス)",
  "company_name": "Databricks",
  "absolute_url": "https://databricks.com/company/careers/open-positions/job?gh_jid=8559344002",
  "location": { "name": "Tokyo, Japan" },
  "offices": [
    {
      "id": 4029277002,
      "name": "Tokyo, Japan",
      "location": "Tokyo, Japan",
      "child_ids": [],
      "parent_id": 4033856002
    }
  ],
  "departments": [
    {
      "id": 4105217002,
      "name": "Field Engineering - Other",
      "child_ids": [],
      "parent_id": 4069106002
    }
  ],
  "metadata": [
    {
      "id": 9740521002,
      "name": "Company Assignment",
      "value": "Databricks Japan K.K.",
      "value_type": "single_select"
    },
    {
      "id": 24059030002,
      "name": "Career Page Posting Category",
      "value": ["Field Engineering"],
      "value_type": "multi_select"
    }
  ],
  "updated_at": "2026-08-03T07:12:33-04:00",
  "first_published": "2026-06-29T02:02:50-04:00",
  "requisition_id": "FEQ327R203",
  "language": "en",
  "application_deadline": null,
  "content": "&lt;p data-renderer-start-pos=&quot;1648&quot;&gt;FEQ327R203&lt;/p&gt;\n&lt;p data-renderer-start-pos=&quot;1648&quot;&gt;DatabricksはデータとAIの企業であり…[truncated, 6086 chars total]",
  "data_compliance": [{ "type": "gdpr", "requires_consent": false }]
}
```

**What this record proves that the field table did not.** Three things.

The body is escaped twice. `&lt;p …&gt;` is not markup — it is the _characters_ `<p …>`. A
consumer that runs a tag-stripper over `content` gets the entities back verbatim in its output,
and one that unescapes once gets HTML where it expected text. The order of the two operations
decides whether this field is usable, and a field table saying `body: content` cannot say so.

`updated_at` and `first_published` sit 35 days apart on this one row, and the adapter reads the
first. That is why the 10-day median freshness above carries a warning rather than a number you
can trust: it measures when Databricks last touched the requisition, not when the job appeared.

And the structure that did arrive is not the structure you want. `location.name` is
"Tokyo, Japan" — no state, no country code, no separate city. The parallel `offices[]` array
looks like the structured version and is not: its `location` is the identical string. Greenhouse
is the one source in this catalog where structured location genuinely is a parsing project.
`data_compliance` is a fourth thing worth noticing: some boards ship GDPR flags, and a consumer
storing rows inherits whatever they imply.

## The identity question, and why it is Greenhouse-only

`company_name` rides on every row. That single field is why `dedup.verify_identity` exists for
Greenhouse and for no other ATS: a probe can prove `boards-api.greenhouse.io/v1/boards/capital`
answers, but only the owner name proves the board is not Capital One's. Lever, Ashby, Rippling
and Teamtailor all return a board's contents without ever naming who owns it, so on those
platforms a slug that resolves is the whole of the available evidence.

## How this was probed

`catalog/_probe.py` on 2026-08-03, one request per second, with steps 2 and 3 skipped — a
whole-board endpoint has no query interface to test and no pages to walk. The API documentation
was read the same day; the legal index was reached but the MSA body text was not retrievable.

```
junk:  control 200 (806 rows) vs junk 200 (806 rows) -> ignores_unknown
fresh: 806 dated rows across pages [0]; p0 median 10.0d, 100% within 100d
body:  median 8519 chars over 806 rows
```

**Not checked, and this is the larger half.** The licence is `summary_only`: the developer
documentation states no terms at all, and the Master Subscription Agreement that might address
third-party consumption could not be read, so `commercial_use: unclear` records an _unread_
contract rather than an absent one — do not read it as "no restrictions". The rate limit stays
`unknown`; none is documented and bursting to find one was not run. Freshness, coverage and the
18% multi-location share all come from ONE board, so `us_share` is left null rather than
inferred from a single employer. The `first_published`-versus-`updated_at` gap was seen on the
captured row and has not been measured across the board, so the size of the freshness
overstatement is itself unquantified.
