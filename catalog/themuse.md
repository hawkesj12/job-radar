---
name: themuse
display_name: The Muse
status: wired
lane: breadth
verdict: >
  Worth adding as a bounded harvest lane. The only evaluated source with real NON-TECH supply
  (11% tech titles), but it cannot search by title and its advertised volume is off by 200x.

auth:
  type: none
  env: []
  signup: https://www.themuse.com/developers/api/v2
  notes: >
    Keyless works for testing. A registered api_key raises the ceiling to 3,600 req/hr and is
    required for any use beyond testing per their terms.

license:
  commercial_use: unclear
  commercial_terms: >
    Terms contemplate third-party sites/apps built on the API, and registration is required
    beyond testing. Whether an AD-SUPPORTED site qualifies is NOT clearly addressed — read the
    full terms before monetising. `unclear` is deliberate; it is not `allowed`.
  attribution_required: unclear
  attribution_text: null
  redistribution: unclear
  derivative_works: unclear
  cache_policy: none_stated
  on_termination: none_stated
  personal_data: none
  terms_url: https://www.themuse.com/developers/api/v2/terms
  read_at: 2026-08-03
  read_depth: summary_only
  verbatim: []
  todo: "Read the full v2 terms end to end and fill verbatim[] before this source ships."

endpoint:
  base: https://www.themuse.com/api/public/jobs
  method: GET
  slug_pattern: null

query:
  title_search: false
  filters: [category, location, level, page]
  param_validation: ignores_unknown
  notes: >
    q / query / search / keyword / title / name ALL return the identical unfiltered set —
    tested individually. Unknown params are silently dropped, so a passing probe here proves
    less than it would against Adzuna.

limits:
  page_size: 20
  max_page: 99
  reachable_per_query: 2000
  rate_limit: unknown
  quota: none
  concurrency_safe: unknown
  notes: 'page 100+ returns 400 {"code":400,"error":"Value `page` is too high"}. Retried 3x — a hard ceiling, not throttling. Applies to filtered slices too.'

volume:
  advertised: 404460
  reachable: 36060
  measured_at: 2026-08-03
  notes: >
    page_count reports 20223 (x20 = 404,460) and is NOT reachable pages. Real ceiling is
    2,000 per query. Fan-out over the 19 categories reaches ~36,060 and the slices are nearly
    disjoint — only 19 duplicate ids in 1,138 sampled rows.

freshness:
  median_age_days: 139
  pct_within_100d: 48
  date_sorted: false
  measured_at: 2026-08-03
  notes: >
    NOT sorted by date — page 0 median 420d, page 25 64d, page 90 45d, page 99 511d. Every page
    mixes 4-day and 530-day rows, so there is no way to page to the fresh part; the cut must
    happen at ingest. Freshness varies enormously by category: Science & Engineering 48/60
    fresh, Management 44/60, HR 43/60 vs Healthcare 2/60 and Food & Hospitality 8/60.
    Healthcare is 1,058 pages deep and almost entirely dead.

coverage:
  countries: [US, and others]
  us_share: unknown
  sector_skew: "11% tech titles — the least tech-skewed source evaluated"
  remote_signal: structured
  remote_how: 'location == "Flexible / Remote" (313 pages ~= 6,260 listings)'
  notes: >
    locations is an ARRAY, so a job can be ['Flexible / Remote', 'New York, NY'] — it models
    "local OR remote" natively, which Adzuna cannot. Real non-tech supply confirmed by eye:
    Semi-Truck Driver (Disneyland), Pediatric Home Health LVN/RN, Electrical Superintendent
    (Bechtel), Plant Superintendent (Crown Cork & Seal), Radiology, Retail Merchandiser.

fields:
  title: { path: name, type: string }
  company: { path: company.name, type: string }
  body:
    { path: contents, type: html_string, median_chars: 5065, max_chars: 10410 }
  location: { path: locations, type: array_of_object, shape: "[{name}]" }
  posted: { path: publication_date, type: iso8601 }
  url: { path: refs.landing_page, type: string }
  function:
    { path: categories, type: array_of_object, shape: "[{name}]", distinct: 20 }
  org_unit: null
  tags: { path: tags, type: array_of_object, shape: "[{name, short_name}]" }
  seniority:
    {
      path: levels,
      type: array_of_object,
      values: [Entry Level, Mid Level, Senior Level],
    }
  salary: null
  employment_type: null
  remote:
    { path: locations, type: derived, rule: 'any name == "Flexible / Remote"' }

traps:
  - "An unrecognized `location` SILENTLY returns the remote set — `zzzznotaplace` returned rows byte-identical to `Flexible / Remote`. Never trust a location query without verifying the value is real."
  - "`page_count` is not reachable pages. 20,223 advertised, 99 usable."
  - "Not date-sorted, so 'just take the newest pages' does not work."
  - "CORRECTED 2026-08-05. This entry previously read 'category filtering looks unreliable' and it was WRONG — it blocked the 19-category fan-out, and with it 36,000 of 36,060 reachable rows, for as long as it stood. Re-measured: `category=Healthcare` -> page_count 1056 with 20/20 rows carrying categories:[\"Healthcare\"] and ZERO overlap with the unfiltered page; `category=Software Engineering` -> page_count 5012, 20/20 correct, 1 overlap. The filter is exact and the slices are near-disjoint. Whatever produced the original observation, it was not this parameter."
  - "`level` filters exactly too and splits a category cleanly (Software Engineering 5012 -> Senior 2005 + Entry 1518, zero overlap), so each sub-slice gets its own 2,000-row ceiling. Verified on ONE category only — do not assume it generalises to all 19."
---

## Why it is interesting

It is the only evaluated source whose supply is genuinely **not** tech. jobfitr's corpus is ~78%
tech; The Muse's sampled titles were **11%** tech, with real healthcare, skilled trades, retail
and logistics roles. That is the gap the product actually has.

## The 20-value category taxonomy

Clean, general-purpose, and deliberately not tech-centric — good enough that jobfitr proposes
adopting it as its controlled `function` vocabulary:

```
Account Management · Accounting and Finance · Advertising and Marketing · Animal Care
Business Operations · Data and Analytics · Education · Food and Hospitality Services
Healthcare · Human Resources and Recruitment · Installation, Maintenance, and Repairs
Legal Services · Management · Product Management · Project Management · Retail
Sales · Science and Engineering · Software Engineering · Unknown
```

## A real record

```json
{
  "name": "Performance Marketing Manager, Paid Search",
  "company": { "name": "Uber" },
  "locations": [{ "name": "New York, NY" }, { "name": "San Francisco, CA" }],
  "categories": [{ "name": "Advertising and Marketing" }],
  "levels": [{ "name": "Mid Level" }],
  "tags": [{ "name": "Fortune 1000", "short_name": "fortune-1000-companies" }],
  "publication_date": "2026-07-16T00:00:00Z",
  "contents": "<p>…5,056 chars of HTML…</p>",
  "refs": { "landing_page": "https://www.themuse.com/jobs/uber/…" }
}
```

## How this was probed (2026-08-03)

1. Junk parameter → silently ignored, so treat every other result here as weaker evidence.
2. Every plausible keyword param (`q`, `query`, `search`, `keyword`, `title`, `name`) → all
   returned the identical unfiltered 20,223-page result. No title search exists.
3. Walked `page` upward until it broke → 99 OK, 100 → 400. Retried 3x to rule out throttling.
4. Sampled pages spread across the range for freshness; sampled 19 categories for reach,
   overlap and sector mix.
5. `location=zzzznotaplace` → byte-identical to `location=Flexible / Remote`.

## Integration shape

**Harvest lane only** — `title_search: false` makes a per-request live fetch impossible. Fan out
over the 19 categories (~1,800 requests, ~13 min measured), apply the freshness cut at ingest
since the feed is unsorted, and expect ~17,400 rows to survive a 100-day cut.
