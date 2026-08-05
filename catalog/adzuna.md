---
name: adzuna
display_name: Adzuna
status: wired
lane: breadth
verdict: >
  The only wired source that is title AND location searchable across all sectors — structurally
  irreplaceable for the live lane. But COMMERCIALLY CONSTRAINED: free for personal use, requires
  a licence conversation before any monetisation.

auth:
  type: free_key
  env: [ADZUNA_APP_ID, ADZUNA_APP_KEY]
  signup: https://developer.adzuna.com/
  notes: instant self-serve key

license:
  commercial_use: trial_only
  commercial_terms: >
    Commercial, government or academic use is permitted for a 14-day trial to validate coverage
    and quality; past that a licence agreement may be required. This is a business conversation,
    not a wall — Adzuna runs a commercial licensing programme.
  attribution_required: true
  attribution_text: >
    Each displayed advert labelled "Jobs by Adzuna", logo at least 116x23 px, with "Jobs"
    hyperlinked to the relevant local Adzuna domain. Research/salary use must cite "The Adzuna API".
  redistribution: license_required
  derivative_works: license_required
  cache_policy: >
    Partner terms require collecting a refreshed content file daily or more often, and refreshing
    displayed content within 4 hours of receiving it. jobfitr's 24h TTL is LOOSER than this.
  on_termination: "immediately remove all insertion codes and acquired data from all pages"
  personal_data: none
  terms_url: https://developer.adzuna.com/docs/terms_of_service
  read_at: 2026-08-03
  read_depth: full
  verbatim:
    - "is permitted subject to a 14 day trial period"
    - "a licence agreement may be required"
    - "may not be used in its original format or in aggregation … without written consent"
    - "shall immediately remove all insertion codes and data acquired from Adzuna"

endpoint:
  base: https://api.adzuna.com/v1/api/jobs/{country}/search/{page}
  method: GET
  slug_pattern: null

query:
  title_search: true
  filters:
    [
      what,
      what_and,
      what_phrase,
      what_or,
      what_exclude,
      title_only,
      where,
      distance,
      category,
      max_days_old,
      sort_by,
      full_time,
      part_time,
      contract,
      permanent,
      salary_min,
    ]
  param_validation: rejects_unknown_400
  notes: >
    REJECTS unknown params with HTTP 400 — so any param that returns a count is REAL, which makes
    probes of this API unusually trustworthy. `title_only` and `what_and` are real but undocumented.

limits:
  page_size: 50
  max_page: unknown
  reachable_per_query: unknown
  rate_limit: unknown
  quota: unknown
  concurrency_safe: unknown
  notes: "jobfitr applies its own ADZUNA_DAILY_CEILING=200 keyed fetches/day as a cost guard."

volume:
  advertised: null
  reachable: unknown
  measured_at: 2026-08-03
  notes: '"AI Engineer" US unfiltered = 55,052; "Occupational Therapist" = 73,014. All sectors.'

freshness:
  median_age_days: unknown
  pct_within_100d: unknown
  date_sorted: false
  notes: "sort_by=date is available but was not the default in any measurement."

coverage:
  countries: [US, GB, "and others via {country} path segment"]
  us_share: 1.0
  sector_skew: "all sectors — the least tech-skewed wired source"
  remote_signal: structured_but_discarded
  remote_how: "location.area == ['US'] exactly means nationwide/remote — 23 of 50 in a sample"

fields:
  title: { path: title, type: string }
  company: { path: company.display_name, type: string }
  body: { path: description, type: string, median_chars: 500 }
  location: { path: location, type: object, shape: "{display_name, area[]}" }
  posted: { path: created, type: iso8601 }
  url: { path: redirect_url, type: string }
  function:
    {
      path: category.label,
      type: string,
      note: 'e.g. "IT Jobs" — strip the " Jobs" suffix',
    }
  org_unit: null
  tags: null
  seniority: null
  salary:
    {
      path: [salary_min, salary_max],
      type: number,
      note: "salary_is_predicted flags estimates",
    }
  employment_type: { path: contract_time, type: string }
  remote: { path: location.area, type: derived, rule: "area == ['US']" }

traps:
  - "`where=remote` returns ZERO — `where` is a place hierarchy, not free text. Use `what_and=remote` (84% relevant vs 2% for blanking `where`)."
  - "`location.area` is DISCARDED by the current adapter, which keeps only display_name. For a nationwide row that is the bare string 'US', so remote_posting() cannot see it."
  - "Descriptions are capped at 500 chars BY ADZUNA — not by jobfitr's BODY_CAP. Do not 'fix' this."
  - "The 4-hour refresh obligation in the partner terms is stricter than jobfitr's 24h TTL."
---

## Why it is structurally important

It is the **only wired source that is both title- and location-searchable across all sectors**.
The ATS lane is deep but tech-shaped; USAJOBS is federal-only; The Muse cannot search by title at
all. When a user asks for "occupational therapist in Louisville," Adzuna is the only thing that can
answer on demand. That makes its licence posture the single most consequential rights question in
the whole system.

## The commercial position, stated plainly

Personal use and portfolio: fine. **Monetisation: needs a licence conversation before, not after.**
The terms are not hostile — Adzuna sells commercial licences and "I have a working product with
real traffic" is exactly the pitch they exist to receive. But the 14-day trial language means the
current usage is not a foundation to put ads on.

## A real record

Captured 2026-08-03, `what=registered nurse`, US. Description truncated; everything else verbatim.

```json
{
  "id": "5826627353",
  "title": "Registered Nurse",
  "company": { "display_name": "Shannon Health" },
  "location": {
    "display_name": "Big Spring, Howard County",
    "area": ["US", "Texas", "Howard County", "Big Spring"]
  },
  "category": {
    "label": "Healthcare & Nursing Jobs",
    "tag": "healthcare-nursing-jobs"
  },
  "created": "2026-08-03T09:55:06Z",
  "salary_min": 48323.49,
  "salary_max": 48323.49,
  "salary_is_predicted": "1",
  "latitude": 32.2035,
  "longitude": -101.46006,
  "redirect_url": "https://www.adzuna.com/land/ad/5826627353?…",
  "description": "Job Summary Under general supervision, performs a wide variety of professional level of nursing duties …[truncated]"
}
```

**What this record proves that the field table did not.** `display_name` is
`"Big Spring, Howard County"` — **no state token**, which is why 63% of jobfitr's corpus cannot be
parsed to a state. But `area` sitting right beside it is `["US","Texas","Howard County","Big
Spring"]`: country, **state**, county, city, already structured. The adapter keeps the string and
discards the array.

So the structured-location work is **not a parsing project for this source — it is a mapping
change**. `salary_is_predicted: "1"` also matters: that salary is Adzuna's estimate, not the
employer's, and nothing downstream distinguishes them.

### The `area` mapping — measured, 250 rows across 5 non-tech queries (2026-08-03)

```
depth histogram : 246/250 are exactly depth 4 · 4 are depth 1
area[0] == "US" : 250/250
area[1] is a real US state : 246/246 = 100.0%
display_name == "area[-1], area[-2]" : 246/250   (i.e. "City, County")
```

| slot             | meaning               | reliability                                       |
| ---------------- | --------------------- | ------------------------------------------------- |
| `area[0]`        | country               | 100% `"US"` on the US endpoint                    |
| `area[1]`        | **state**             | **100% a real US state** — this is the filter key |
| `area[2]`        | county (at depth 4)   | positional, see caveat                            |
| `area[-1]`       | finest known locality | **not always the city** — see caveat              |
| `len(area) == 1` | nationwide / remote   | the discarded remote signal                       |

**Caveat — depth 5 exists and shifts the meaning.** `['US','New York','New York City','Manhattan',
'Prince']`: there `area[2]` is the _city_ and `area[-1]` is a neighborhood. Metros with sub-localities
(NYC boroughs) push everything one slot deeper. **`area[1]` is unaffected**, so state-level filtering
is safe positionally; city/county extraction must branch on depth rather than index blindly.

**Proposed rule:**

```python
country = area[0] if area else None
state   = area[1] if len(area) >= 2 else None      # 100% reliable
remote  = (area == ["US"])                          # the signal currently discarded
county  = area[2] if len(area) == 4 else None       # depth-4 shape only
city    = area[3] if len(area) == 4 else (area[2] if len(area) >= 5 else None)
```

## How this was probed (2026-08-03)

Junk parameter → **HTTP 400**, which is why everything else measured here is trustworthy. Then
`where` across `remote` / `anywhere` / `""` / `Louisville, KY`; `what_and` and `title_only`
combinations; and a 50-row sample scored by eye for actual remoteness.
