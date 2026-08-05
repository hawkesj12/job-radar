---
name: jobicy
display_name: Jobicy
status: wired
lane: breadth
verdict: >
  The only source in this catalog that REJECTS an unknown parameter, which makes every number
  below trustworthy in a way no other keyless source's are. It has a documented, working
  keyword filter (`tag`) and structured salary, seniority and region. Its hard limit is
  reach, not quality: `count` caps at 100, NO pagination parameter exists at all, so one
  query can only ever see the latest 100 postings.

auth:
  type: none
  env: []
  signup: null
  notes: keyless, no registration; the same data is also served over RSS and MCP

license: # a CONTRACT — quoted, not paraphrased
  commercial_use: unclear
  commercial_terms: >
    "Fair Use and Restrictions" states the purpose of access — distributing Jobicy listings —
    and enumerates what breaks it, but never addresses commercial use as such. The named
    prohibition is redistribution to COMPETING aggregation platforms, with Google Jobs,
    LinkedIn and Jooble called out by name, which is the same clause Remotive carries almost
    word for word. Absent any statement on commercial use, this is `unclear` and not `allowed`.
  attribution_required: false # not stated as a requirement anywhere in the fair-use section
  redistribution: prohibited # to competing job aggregation platforms, named explicitly
  derivative_works: prohibited # "You modify or misrepresent the original content" ends access
  cache_policy: "polling more than once per hour is discouraged; listings are delayed 3 hours"
  on_termination: "access may be restricted; no data-deletion clause is stated"
  personal_data: none # employer postings only, no candidate data in the response
  terms_url: https://github.com/Jobicy/remote-jobs-api
  read_at: 2026-08-03
  read_depth: full
  verbatim:
    - "API, RSS, and MCP access are provided to help distribute Jobicy job listings."
    - "Do not redistribute listings to competing job aggregation platforms such as Google Jobs, LinkedIn, Jooble, and similar services."
    - "Job listings are intentionally published with a 3-hour delay."
    - "Polling feeds more than once per hour is discouraged."
    - "Excessive requests may result in restricted access."
    - "Access may be restricted if: You intentionally overload the service. You modify or misrepresent the original content. Your activity negatively impacts platform stability."

endpoint:
  base: https://jobicy.com/api/v2/remote-jobs
  method: GET
  slug_pattern: null

query:
  title_search: true # via `tag` — documented, and measured. See "The `tag` question" below
  location_search: true # `geo`, from a fixed slug taxonomy, not free text
  filters: [count, geo, industry, tag]
  param_validation: rejects_unknown_400 # the ONLY source here that does; see traps

limits:
  page_size: 100 # documented: "Default: 100; range: 1-100"
  max_page: 1 # there is NO pagination parameter. `page` is not a documented key and 400s
  reachable_per_query: 100 # the hard ceiling for any one filter combination
  rate_limit: unknown # no figure published; "polling more than once per hour is discouraged"
  quota: unknown
  concurrency_safe: unknown

volume:
  advertised: null # the response carries no total
  reachable: 100 # per query. Different geo/industry/tag combinations reach different 100s
  measured_at: 2026-08-03

freshness:
  median_age_days: 1.0
  pct_within_100d: 1.0 # █████
  date_sorted: unknown # a single unpaged page of 100; there is nothing to sort across
  measured_at: 2026-08-03

coverage:
  countries: [multi] # `jobGeo` is a region label — "USA", "Anywhere", "Europe"
  us_share: null # the captured row says "USA"; the share across the feed was not counted
  sector_skew: >
    all sectors — 22 documented industry slugs spanning healthcare, legal, accounting and
    education as well as engineering. One of the least tech-skewed keyless sources here.

location:
  shape: free_text
  primary_path: jobGeo
  type: string
  state_available: false # no state, city or country field exists anywhere in the record
  state_path: null
  city_path: null
  country_path: null
  multi_value: false
  multi_delimiter: null
  free_text_fallback: jobGeo
  gazetteer_needed: true
  notes: >
    `jobGeo` is an employment RESTRICTION, not a workplace: it says where a candidate may be
    based, and reads "Anywhere" when unrestricted. It draws from a fixed taxonomy of about 50
    region slugs (`?get=locations`), which makes it clean to filter on and useless to map to a
    city or a state. A consumer needing US state filtering can never satisfy it from here.

remote:
  signal: derived
  path: null
  rule: "every posting is remote by construction — Jobicy lists nothing else"
  reliability: "the site is remote-only, so the signal is the source itself"

fields:
  title: { path: jobTitle, type: str }
  company: { path: companyName, type: str }
  body: { path: jobDescription, type: str, median_chars: 6259 } # HTML
  posted: { path: pubDate, type: str } # ISO 8601 with a real offset
  url: { path: url, type: str }
  tags: null # `tag` is a QUERY parameter; there is no tags field on the record
  salary: {
      path: [salaryMin, salaryMax, salaryCurrency, salaryPeriod],
      type: number,
    } # fully structured
  employment_type: { path: jobType, type: list } # ["Full-Time"] — an ARRAY, not a string
  function: { path: jobIndustry, type: list } # ["Project & Program Management"] — a job family
  org_unit: null
  employer_org: null
  seniority: { path: jobLevel, type: str } # "Senior" — already normalized, "Any" when unset
  # unmapped keys in the record: id, jobSlug, companyLogo, jobExcerpt

traps:
  - "`count` maxes at 100 and there is NO pagination parameter — not `page`, not `offset`, not `skip`. One query sees the latest 100 postings and there is nothing behind them. The only way to reach more is to vary `geo`/`industry`/`tag` and accept the overlap; total corpus size is not knowable from the API at all."
  - "Unknown parameters return 400, which is unusual here and GOOD: a typo fails loudly instead of silently returning an unfiltered set the way Remotive and The Muse do. It also means the probe's generic name list (`q`, `query`, `search`…) all 400'd, and a source that 400s on everything can still have a working keyword filter under a name of its own — Jobicy's is `tag`."
  - "`tag` searches job titles AND DESCRIPTIONS, per Jobicy's own documentation. Measured 2026-08-03, `tag=nurse` returned 20 rows of which 15% had 'nurse' in the title — the other 85% mention it in the body. It is a real keyword filter, not a title filter, so a consumer that displays these as title matches will look broken to a user."
  - "`jobType` and `jobIndustry` are ARRAYS, not strings, even when they hold one value. Reading either as a string yields a Python list repr in the output column."
  - "`jobGeo` is where a candidate may LIVE, not where the job is. Treating it as a job location mis-files every row."
  - "Listings are held back 3 hours ON PURPOSE. The 1-day median freshness above is capped by that, not by how fast Jobicy lists."
  - "`jobExcerpt` looks like a summary and is a mangled prefix of the body with the whitespace removed — the captured row reads 'CompanyCox Automotive - USAJob Family GroupBusiness Operations'. Use `jobDescription`."
  - "Redistribution to competing aggregators is named and forbidden, listing Google Jobs, LinkedIn and Jooble. Any consumer that syndicates onward is in breach."
---

# jobicy

## A real record

Captured 2026-08-03 from `jobicy.com/api/v2/remote-jobs?count=100`, first row. Body and excerpt
truncated with a marker; rest verbatim.

```json
{
  "id": 150172,
  "url": "https://jobicy.com/jobs/150172-senior-manager-change-enablement-cox-automotive-fleet",
  "jobSlug": "150172-senior-manager-change-enablement-cox-automotive-fleet",
  "jobTitle": "Senior Manager, Change Enablement - Cox Automotive Fleet",
  "companyName": "Cox Enterprises",
  "companyLogo": "https://jobicy.com/data/server-nyc0409/galaxy/mercury/2026/07/613a036d6c31-221.webp",
  "jobIndustry": ["Project &amp; Program Management"],
  "jobType": ["Full-Time"],
  "jobGeo": "USA",
  "jobLevel": "Senior",
  "jobExcerpt": "CompanyCox Automotive - USAJob Family GroupBusiness OperationsJob ProfileChange Enablement Sr ManagerManagement LevelSr Manager - Non People Leader…[truncated, 421 chars total]",
  "jobDescription": "<p><b>Company</b></p><p>Cox Automotive - USA</p><p></p><div><div><div><div>…[truncated, 9345 chars total]",
  "pubDate": "2026-08-03T18:45:05+00:00",
  "salaryMin": 122600,
  "salaryMax": 204400,
  "salaryCurrency": "USD",
  "salaryPeriod": "yearly"
}
```

**What this record proves that the field table did not.** Three things.

Salary is genuinely structured, and almost nothing else keyless here is. Four separate keys —
min, max, ISO currency, period — instead of a free-text string someone has to regex. Combined
with `jobLevel: "Senior"`, which is an already-normalized seniority rather than a word buried
in a title, this row carries two of the most expensive-to-derive fields for free.

`jobIndustry` and `jobType` are arrays holding one element each. Nothing in the field names
says so, and a mapper that assigns them as strings writes `['Full-Time']` into the output
column — visible in the shortlist, invisible in code review.

And `jobIndustry` is a real job FAMILY ("Project & Program Management"), so it maps to
`function`. Jobicy is one of the few sources here whose category field is not secretly an org
unit or an employer name. Note the `&amp;` — the value ships HTML-escaped inside a JSON string,
so it needs unescaping even though nothing about the field suggests markup.

## The `tag` question, and why `title_search` is `true`

The probe's generic parameter list — `q`, `query`, `search`, `keyword`, `keywords`, `title`,
`name` — returned 400 for all seven. That is the correct answer for those names and the wrong
conclusion about the source. Jobicy documents its keyword filter as `tag`, and `tag=nurse`
returned 200 with 20 rows.

The honest measurement is that 15% of those rows had "nurse" in the title. Jobicy's own
documentation explains why: `tag` is specified as "Search job titles and descriptions", so the
other 85% are body matches, and they are correct behaviour rather than noise.

That leaves a judgement call the schema is explicit about — `title_search` is "DECISIVE:
false => harvest-lane only, never live-fetch". Jobicy CAN answer a per-request keyword query,
so recording `false` would route a queryable source to the harvest lane on the strength of a
number (15%) produced by a title-only relevance test against a title-and-body filter. It is
recorded as `true`, with the 15% figure kept in `traps:` so nobody mistakes it for a title
match. **If the intended meaning of `title_search` is strictly "matches on the title", this is
the one field in this profile to flip** — the measurement supports either reading and the
routing consequence is the reason to prefer `true`.

## Why the 100-row ceiling is the real limit

Every other reach question about this API is pleasant. Filters are documented, they work,
unknown names fail loudly, and the taxonomy is discoverable at runtime via `?get=locations` and
`?get=industries`.

Then `count` stops at 100 and there is no second page. Not a page parameter that is ignored —
which is what Remotive, RemoteOK, techtree, workingnomads and landing.jobs all do — but no
pagination concept in the documentation at all. So the reachable corpus per query is exactly
100 rows, and the only lever for more is to fan out across the ~50 region slugs and 22 industry
slugs and deduplicate what comes back. The API never reports a total, so there is no way to
know what fraction of Jobicy you have seen.

## How this was probed

`catalog/_probe.py` on 2026-08-03, one request per second. Freshness was re-run the same day
with the page step skipped, because the first pass appended `page=0` to the URL and Jobicy —
correctly, per its own validation — answered 400, so the freshness sampler saw zero rows and
reported `unknown`. That was a probe artifact, not a property of the source. Terms read the
same day from the official repository and quoted verbatim above.

```
junk:  control 200 (100 rows) vs junk 400 (0 rows) -> rejects_unknown_400
title: control: 100 rows, 2% already match 'nurse'
title: q / query / search / keyword / keywords / title / name = nurse -> 400, 0 rows
title: tag=nurse -> 200, 20 rows, 15% title-relevant
title: geo=nurse -> 400 ; industry=nurse -> 400
pages: last page returning rows: 1 (first failure at 1) — `page` is not a valid parameter
fresh: 100 dated rows across pages [0]; p0 median 1.0d, 100% within 100d
body:  median 6259 chars over 100 rows
```

**Not checked:** the rate limit stays `unknown` — no figure is published, the terms discourage
polling more than hourly and warn that excessive requests restrict access, and deliberately
tripping that on a source that says it will was not worth the number. Also unchecked: the true
US share (the captured row says "USA" but the distribution across the feed was not counted);
whether varying `geo`/`industry`/`tag` reaches materially different rows or the same 100 with a
filter applied; and `date_sorted`, which is unanswerable while only one page exists.
