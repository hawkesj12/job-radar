---
name: weworkremotely
display_name: We Work Remotely
status: rejected
lane: breadth
verdict: >
  The best cost-per-row measured — 603 unique postings for 9 requests, 97% fresher than 100
  days — and contractually unusable for this product. Its API terms name "a job advertising
  or job search service" in the list of things you may not build, prohibit "saving, or
  storing our data" outright, and require that applying be routed through weworkremotely.com
  rather than direct to the employer. Three separate clauses, each independently fatal.
  Rejected on rights, not on data.

auth:
  type: none
  env: []
  signup: null
  notes: keyless RSS; "RSS" is linked from the site's own footer

license: # a CONTRACT — quoted, not paraphrased. THE deciding block on this source.
  commercial_use: prohibited # for this use; see commercial_terms
  commercial_terms: >
    Commercial use is granted and then withdrawn by the same sentence: free "provided that
    you abide by these terms and do not build an application or business that attempts to
    harm, compete with or replace We Work Remotely". The DON'T list then names the exact
    product — "a job advertising or job search service" — so this is not a judgement call
    about what "compete" means. Separately, "API Only" prohibits saving or storing the data
    at all, which a harvester does by definition, and the attribution clause forbids
    bypassing the WWR interface when applying.
  attribution_required: true
  attribution_text: >
    "we require you to not bypass the We Work Remotely interface when applying for a job.
    You are welcome to pull in job details like company name, logo and description, but
    applying must be routed through the weworkremotely.com website."
  redistribution: prohibited
  derivative_works: prohibited
  cache_policy: "storing prohibited outright — stricter than any cache window"
  on_termination: "We Work Remotely can refuse API access at any time for any reason"
  personal_data: none # employer postings only
  terms_url: https://weworkremotely.com/api-terms-and-guidelines
  terms_url_secondary: https://weworkremotely.com/terms-and-conditions
  read_at: 2026-08-03
  read_depth: full
  verbatim:
    - "do not build an application, website, product, or business that attempts to harm, compete with, or replace We Work Remotely, our website, or our services."
    - "In particular, do not use the API, or any We Work Remotely data, to build the following: […] A job advertising or job search service"
    - "The only We Work Remotely data you may use in your product or application is that which is exposed via our API. Scraping, copying, saving, or storing our data is strictly prohibited and against our Terms of Service."
    - "By using the API, we require you to not bypass the We Work Remotely interface when applying for a job. You are welcome to pull in job details like company name, logo and description, but applying must be routed through the weworkremotely.com website. Any violations of this will be shut down."
    - "You are free to use the We Work Remotely API for commercial use, provided that you abide by these terms and do not build an application or business that attempts to harm, compete with or replace We Work Remotely, our website, or our services."
    - "the onus is on you to contact us and inquire whether your use of the API is permitted."

endpoint:
  base: https://weworkremotely.com/remote-jobs.rss
  method: GET
  slug_pattern: null
  fan_out: >
    8 further category feeds at weworkremotely.com/categories/remote-<category>-jobs.rss —
    the union of all 9 is what produces 603 unique rows for 9 requests.

query:
  title_search: false # RSS — there is no query interface at all
  location_search: false
  filters: []
  param_validation: ignores_unknown

limits:
  page_size: 100 # per feed document
  max_page: null # a feed is a single document; there is no paging
  reachable_per_query: 100
  rate_limit: unknown # WWR publishes specifics; not read, and not probed
  quota: unknown
  concurrency_safe: unknown

volume:
  advertised: 41474 # the site footer's own count of posted jobs
  reachable: 603 # unique across the union of 9 feeds — about 1.5% of the advertised figure
  measured_at: 2026-08-03

freshness:
  median_age_days: 7.5
  pct_within_100d: 0.97
  date_sorted: true # an RSS feed is newest-first by construction, and the sample agreed
  measured_at: 2026-08-03

coverage:
  countries: [multi]
  us_share: null
  sector_skew: >
    Broader than most remote boards — the categories run beyond engineering to design,
    sales, support and an "All Other Remote" catch-all.

location:
  shape: semi_structured
  primary_path: region
  type: string
  state_available: true # a `state` element exists and carried "California" in the capture
  state_path: state
  city_path: null
  country_path: country
  multi_value: true # `country` held a list of ten countries in one row
  multi_delimiter: ","
  free_text_fallback: region
  gazetteer_needed: false
  notes: >
    Three overlapping location elements — `region` ("Anywhere in the World"), `country`, and
    `state` ("California"). `country` is the awkward one: it carried ten countries in a
    single comma-separated string, each PREFIXED WITH AN EMOJI FLAG. Any consumer matching
    country names must strip those first, and a naive length check or exact match fails.

remote:
  signal: derived
  path: null
  rule: "every posting is remote by construction — WWR lists nothing else"
  reliability: "the site is remote-only, so the signal is the source itself"

fields:
  title: { path: title, type: str } # company is PREFIXED into this — see traps
  company: null # no company element; it is glued to the title
  body: { path: description, type: str, median_chars: 7247 } # HTML, opens with an <img>
  posted: { path: pubDate, type: str } # RFC 822
  url: { path: link, type: str } # `guid` holds the same URL
  tags: null # a `skills` element exists and was empty
  salary: null
  employment_type: { path: type, type: str } # "Contract"
  function: { path: category, type: str } # "All Other Remote"
  org_unit: null
  employer_org: null
  seniority: null
  # unmapped keys in the record: content (empty), expires_at, guid, skills (empty)

traps:
  - "**The API terms are on a DIFFERENT page from the terms of service, and the footer links only the latter.** `/terms-and-conditions` contains no clause about the feed and reads permissive by omission; `/api-terms-and-guidelines` contains the prohibitions. Reading only the linked one produces exactly the wrong answer — this profile originally recorded `unclear` for that reason."
  - '"A job advertising or job search service" is a NAMED item in the do-not-build list. This is not an inference about what competing means.'
  - '"Scraping, copying, saving, or storing our data is strictly prohibited" — a harvester stores rows by definition, so the storage model itself is the violation, independent of what is displayed.'
  - "Applying may NOT bypass the WWR interface — the apply link must route through weworkremotely.com. Any product promising direct-to-employer application links cannot honour that."
  - '**The company is not a field.** It is prefixed into the title: "Toptal: CAD Engineer — AI Model Training & Evaluation | Remote". Splitting on the first colon recovers it, but titles legitimately contain colons, and the trailing " | Remote" is decoration that also needs stripping.'
  - '`country` is a comma-separated list of country names EACH PREFIXED WITH AN EMOJI FLAG — "🇦🇷 Argentina, 🇧🇷 Brazil, 🇨🇦 Canada, …". Ten of them in the captured row. Exact-matching this against a country name fails on the flag.'
  - "`content` and `skills` elements exist and were EMPTY in the capture, while `description` carries the real body. Reading `content` because RSS conventionally uses `content:encoded` yields nothing."
  - "The site advertises 41,474 jobs and the feeds expose ~603. Advertised is not reachable, and the gap here is roughly 70x."
---

# weworkremotely

## A real record

Captured 2026-08-03 from the main feed, first item, parsed from RSS into a dict. Description
truncated with a marker; rest verbatim.

```json
{
  "title": "Toptal: CAD Engineer — AI Model Training & Evaluation | Remote",
  "region": "Anywhere in the World",
  "country": "🇦🇷 Argentina, 🇧🇷 Brazil, 🇨🇦 Canada, 🇨🇱 Chile, 🇨🇴 Colombia, 🇨🇷 Costa Rica, 🇲🇽 Mexico, 🇺🇸 …[truncated]",
  "state": "California",
  "category": "All Other Remote",
  "type": "Contract",
  "skills": "",
  "content": "",
  "description": "<img src=\"https://we-work-remotely.imgix.net/logos/0171/6077/logo.gif?ixlib=rails-4.0.0…[truncated, 7247 chars median]",
  "pubDate": "Mon, 03 Aug 2026 20:57:57 +0000",
  "expires_at": "Wed, 02 Sep 2026 20:57:57 +0000",
  "guid": "https://weworkremotely.com/remote-jobs/toptal-cad-engineer-ai-model-training-evaluation…",
  "link": "https://weworkremotely.com/remote-jobs/toptal-cad-engineer-ai-model-training-evaluation…"
}
```

**What this record proves that the field table did not.** There is no company element. The
employer is the word before the first colon in the title — "Toptal" — and the title also
carries a decorative `| Remote` suffix. A field table with `company: null` states the absence
correctly but makes it look like the data is missing; it is not missing, it is embedded, and
recovering it is string surgery on a field that legitimately contains colons.

The `country` value is the other thing worth seeing rather than describing: ten countries in one
string, each carrying an emoji flag. It is a display string that happens to live in a data
field.

## Why it is rejected

The data is excellent and none of that matters. Three clauses from the API terms, each
sufficient on its own:

**It names the product.** The DON'T list is explicit: "do not use the API, or any We Work
Remotely data, to build the following: … **A job advertising or job search service**." There is
no interpretation to do.

**It prohibits storage.** "The only We Work Remotely data you may use in your product or
application is that which is exposed via our API. Scraping, copying, saving, or storing our data
is strictly prohibited." A harvester stores rows; that is what it is. This clause bites the
architecture, not the presentation.

**It forbids direct apply links.** "applying must be routed through the weworkremotely.com
website. Any violations of this will be shut down." A product whose promise is direct-to-company
listings cannot satisfy that.

The terms also put the burden the right way round — "the onus is on you to contact us and
inquire whether your use of the API is permitted" — so if this source is ever wanted, the route
is an email to WWR, not a re-reading of the clauses.

## The trap that produced the wrong answer first

This profile initially recorded `commercial_use: unclear` on the strength of
`/terms-and-conditions`, which is what the site footer links, and which contains nothing about
the feed at all. The API terms live at a separate URL that the footer does not link. Reading
only the linked document yields a permissive-by-omission reading that is the opposite of the
truth.

The general lesson, worth applying to every remaining source: **look for API-specific terms
separately from the terms of service, and treat "the ToS says nothing about the API" as an
unfinished search rather than a finding.** Remotive and Remote OK both ship their API terms
inside the payload; We Work Remotely puts them on an unlinked page; TechTree puts them in the
main ToS. There is no convention.

## How this was probed

`catalog/_probe.py` on 2026-08-03, one request per second, parsing RSS through `parse_rss`.
Terms read the same day: first `/terms-and-conditions` (found via the footer, after `/terms` and
`/terms-of-service` both 404'd), then `/api-terms-and-guidelines`, which is the operative
document and was surfaced by the catalog spar rather than by that footer trail.

```
junk:  control 200 (100 rows) vs junk 200 (100 rows) -> ignores_unknown
title: RSS feed — no query interface
pages: RSS feed — single document
fresh: 100 dated rows, median 7.5d, 97% within 100d
body:  median 7247 chars over 100 rows
```

**Not checked:** the rate limit — WWR says it has "published some specifics with regard to rate
limiting" on another page, and that page was not read, because the source is rejected on rights
and the number would not change anything. The 603-unique figure across 9 feeds is carried
forward from the earlier survey rather than re-measured. Whether `state` is populated as
reliably as the captured row suggests — one row carrying "California" is not a distribution.
