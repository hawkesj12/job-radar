---
name: workingnomads
display_name: Working Nomads
status: evaluated
lane: breadth
verdict: >
  A clean, small JSON feed — 28 rows, eight keys, no nulls, no duplicate fields, real HTML
  bodies averaging 4,530 characters and a genuine job-family field. It is the tidiest record
  shape in the breadth lane. Its limits are reach and geography: 28 rows is everything, `page`
  is ignored, there is no query interface, and `location` is a continent-scale region string.

auth:
  type: none
  env: []
  signup: null
  notes: >
    Keyless. `exposed_jobs` is a self-describing endpoint name — the publicly exposed subset —
    rather than a documented developer API; no API documentation page was found.

license: # a CONTRACT — quoted, not paraphrased
  commercial_use: unclear
  commercial_terms: >
    The terms and conditions are written about "the Service" — the website and its accounts —
    and about competitors, not about a feed. The prohibition on commercially exploiting the
    Service is illustrated by its own example, "by sharing your premium account with others",
    which is about account resale rather than data redistribution. The competitive-product
    clause is broader and could reach a consumer that builds a rival remote-jobs board. No
    clause addresses reading the feed, so `unclear` — and `unclear` is not `allowed`.
  attribution_required: unknown # never addressed; §11.6 runs the other way, see verbatim_note
  redistribution: unknown # never addressed for listing data
  derivative_works: unknown
  cache_policy: none_stated
  on_termination: >
    §10.3 — on termination "we may delete your account and all related data (unless otherwise
    required by law or these Terms)"; this concerns an account holder's data, not a feed reader's
  personal_data: none # employer postings only, no candidate data in the response
  terms_url: https://www.workingnomads.com/terms-and-conditions
  read_at: 2026-08-03
  read_depth: full
  verbatim:
    - "Resell, sublicense, or otherwise commercially exploit the Service (for example, by sharing your premium account with others)."
    - "Access or use the Service to build a competitive product or service."
    - "Reverse engineer, decompile, disassemble, or hack any part of the Service."
    - "You agree that we may display your name, logo, and reference on our website or marketing materials to indicate that you use our Service."
  verbatim_note: >
    The last clause is quoted to prevent a misreading: it is Working Nomads' right to name its
    own CUSTOMERS, not an attribution requirement placed on a consumer of the feed.

endpoint:
  base: https://www.workingnomads.com/api/exposed_jobs/
  method: GET
  slug_pattern: null

query:
  title_search: false # MEASURED — nine parameter names tried, none filtered
  location_search: false
  filters: [] # none that were shown to work
  param_validation: ignores_unknown

limits:
  page_size: 28 # not a page size — it is the whole feed
  max_page: null # `page` had no effect — page 999 returned the identical 28 rows
  reachable_per_query: 28
  rate_limit: unknown # none published; not probed
  quota: unknown
  concurrency_safe: unknown

volume:
  advertised: null # the response is a bare array with no envelope and no total
  reachable: 28
  measured_at: 2026-08-03

freshness:
  median_age_days: 13.0
  pct_within_100d: 1.0 # █████
  date_sorted: unknown # one unpaged document; there is nothing to sort across
  measured_at: 2026-08-03

coverage:
  countries: [multi] # `location` is a region label — "Latin America" on the captured row
  us_share: null # the region strings do not resolve to countries; not countable as written
  sector_skew: >
    remote-first and broad — `category_name` values span Customer Success, engineering,
    marketing and management rather than tech alone

location:
  shape: free_text
  primary_path: location
  type: string
  state_available: false # no state, city or country field exists anywhere in the record
  state_path: null
  city_path: null
  country_path: null
  multi_value: true # MEASURED across 28 rows — see notes
  multi_delimiter: "," # "Europe, North America, Latin America, APAC" — and see the REMOTE trap
  free_text_fallback: location
  gazetteer_needed: true
  notes: >
    A continent-scale eligibility region — "Latin America" on the captured row — describing
    where a candidate may be based rather than where the job is. There is no finer granularity
    anywhere in the record: no city, no country, no code. US state filtering is structurally
    impossible, and even country filtering requires deciding what "Latin America" contains.
    Measured across all 28 rows the distinct values were "Europe, North America, Latin
    America, APAC" (5), "Global" (3), then singletons including "REMOTE, South America"
    and "REMOTE, Canada" — so the field carries a workplace MODE and a region at once,
    and splitting on the comma yields a place called REMOTE.

remote:
  signal: derived
  path: null
  rule: "every posting is remote by construction — Working Nomads lists nothing else"
  reliability: "the site is remote-only, so the signal is the source itself"

fields:
  title: { path: title, type: str }
  company: { path: company_name, type: str }
  body: { path: description, type: str, median_chars: 4530 } # HTML
  posted: { path: pub_date, type: str } # ISO 8601 with a real offset (-04:00)
  url: { path: url, type: str } # a redirect: /job/go/{id}/ — see traps
  tags: { path: tags, type: str } # A COMMA-DELIMITED STRING, not a list — see traps
  salary: null
  employment_type: null
  function: { path: category_name, type: str } # "Customer Success" — a real job family
  org_unit: null
  employer_org: { path: company_name, type: str } # the employing organisation
  seniority: null
  # unmapped keys in the record: none — all eight keys are mapped

traps:
  - '`tags` is a comma-delimited STRING, not an array: "crm,account manager,communication,english". Every other source in this catalog that has tags sends a list. Assigning this one straight through puts a single blob in the tags column, and note the entries contain spaces but the delimiter has no space after it — splitting on '', '' yields one token, splitting on '','' yields four.'
  - "`url` is a REDIRECT (`/job/go/1764739/`), not the posting's canonical page. It is stable enough to deduplicate on, but it resolves through Working Nomads' own tracking hop, so the stored URL is not where the job actually lives and following it is an attributed click."
  - '`category_name` is one of the few genuinely clean job-family fields in this catalog — "Customer Success", not an org unit and not an employer name. It maps to `function` correctly, which is rare enough here to be worth stating.'
  - "28 rows is the WHOLE feed. `page` is ignored — page 999 returned the identical 28 rows — so there is nothing behind it, and `exposed_jobs` is presumably a sample of a larger corpus that this endpoint does not reach."
  - "`pub_date` carries a -04:00 offset — Eastern, as it happens — while most sources here send UTC or naive. Parsing it as naive and then localizing double-shifts it."
  - "There is no salary, no employment type and no seniority field at all. The record is clean because it is small."
  - "The terms' commercial clause is illustrated by account sharing, so it reads as being about account resale rather than data reuse. Do not stretch it into a permission — the competitive-product clause beside it is the one that could bite."
---

# workingnomads

## A real record

Captured 2026-08-03 from `www.workingnomads.com/api/exposed_jobs/`, first row. Description
truncated with a marker; rest verbatim and complete.

```json
{
  "url": "https://www.workingnomads.com/job/go/1764739/",
  "title": "Customer Success Lead",
  "company_name": "Cloudasta",
  "category_name": "Customer Success",
  "description": "<p>Cloudasta is looking for a full-time seasoned Customer Success Lead to own, grow, and protect the commercial relationships within our high-touch customer base. This is a player-coach role: you will directly manage a Book of Business (BoB) of up to 50 accounts representing appr…[truncated, 5857 chars total]",
  "tags": "crm,account manager,communication,english",
  "location": "Latin America",
  "pub_date": "2026-07-31T15:21:46-04:00"
}
```

**What this record proves that the field table did not.** Mostly that this source is honest,
which is worth recording because so little else here is.

Eight keys, eight populated values, no nulls, no duplicate pairs, no empty-string-instead-of-null
sentinels, no fields holding each other's data. Every other source in this catalog needed a trap
entry for a key that lies about its contents — devitjobs' `stateCategory` holding a country,
jobspresso's `job_type` and `job_category` swapped, remoteok's `salary_min: 0` meaning unknown,
techtree's `equity` object full of empty strings. Working Nomads has none of that.

`category_name` is the concrete payoff. "Customer Success" is a job family: not an org unit
(Greenhouse), not an employer (USAJOBS), not a seniority (Braintrust), not a hyphenated job
title (Himalayas). It maps to `function` on the first reading, and `_SCHEMA.md` exists because
that is unusual.

The one real defect is `tags`. `"crm,account manager,communication,english"` is a string where
every comparable source sends an array, and the delimiter is a bare comma while the values
themselves contain spaces — so `split(", ")` returns the whole blob as one tag and looks like it
worked. Four useful skill tags are in there and the shape is one character away from being
unparseable by the obvious rule.

The second thing worth noting is `url`. `/job/go/1764739/` is a redirect through Working Nomads'
tracker, not the employer's posting. It deduplicates fine and it is not where the job lives.

## The size is the story

28 rows, and `page` does nothing — page 999 returns the same 28. The endpoint is called
`exposed_jobs`, which reads like exactly what it is: the publicly exposed slice of a corpus the
site holds more of.

So the whole source is one request and 28 rows, refreshed as postings roll through. Median age
13 days, everything inside 100 days. That is a real and current sample, and it is a sample —
there is no parameter, documented or guessed, that reached anything behind it, and the response
carries no total to say how much is behind it.

For a consumer that harvests everything and deduplicates, a 28-row source is cheap to include
and contributes little. It is recorded as `evaluated` on those merits: nothing about the data
is wrong, and there is not much of it.

## How this was probed

`catalog/_probe.py` on 2026-08-03, one request per second. Terms read the same day and quoted
verbatim above.

```
junk:  control 200 (28 rows) vs junk 200 (28 rows) -> ignores_unknown
title: control: 28 rows, 0% already match 'nurse'
title: q / query / search / keyword / keywords / title / name / term = nurse -> 200, 28 rows, 0%
title: filters on: nothing
pages: `page` appears IGNORED — page 999 returned the same 28 rows as the control
fresh: 28 dated rows across pages [0]; p0 median 13.0d, 100% within 100d
body:  median 4530 chars over 28 rows
```

**Not checked:** the rate limit stays `unknown` — none is published and bursting was not run.
No API documentation page was found, so whether `exposed_jobs` accepts any documented parameter
is unestablished, and the nine names tried were generic ones — the same gap that hid Jobicy's
`tag` and Arbeitnow's `visa_sponsorship`. Whether a larger, non-"exposed" endpoint exists was
not investigated. `us_share` is null rather than guessed: "Latin America" is not a country and
one row is not a distribution. Whether `location` can hold several regions at once WAS
measured after this section was first written — it can, and `multi_value` is `true`: five of
the 28 rows read "Europe, North America, Latin America, APAC", and two carry the literal token
REMOTE beside a region.
