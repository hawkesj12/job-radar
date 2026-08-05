---
name: jobspresso
display_name: Jobspresso
status: evaluated
lane: breadth
verdict: >
  A ten-item WordPress job-feed RSS with more fields than most feeds carry — a real company
  field, a location, an employment type — and two defects that will bite anyone who trusts the
  key names: the real body is in `content:encoded` while `description` holds a 372-character
  excerpt, and `job_type` and `job_category` hold each other's values. Its site terms prohibit
  commercial use and any public display, which is the plainest prohibition in this catalog.

auth:
  type: none
  env: []
  signup: null
  notes: >
    Keyless RSS, served by WP Job Manager at `?feed=job_feed` — a plugin-standard endpoint
    rather than a bespoke API.

license: # a CONTRACT — quoted, not paraphrased
  commercial_use: prohibited
  commercial_terms: >
    The site's Terms and Conditions grant a "Use License" that is explicitly personal,
    non-commercial and transitory, and then prohibits commercial use, public display of ANY
    kind, modification, copying and mirroring to another server. These are generic website
    terms rather than feed-specific ones — the page never mentions RSS or an API — but they
    are the only contract Jobspresso publishes, and their plain wording covers republishing
    listings. Recorded as `prohibited` because the clause says so, with the caveat that it was
    written for site content rather than for a feed the site itself publishes.
  attribution_required: unknown # not offered as a remedy; the licence does not turn on credit
  redistribution: prohibited
  derivative_works: prohibited
  cache_policy: none_stated
  on_termination: >
    "you must destroy any downloaded materials in your possession whether in electronic or
    printed format" — an explicit deletion duty, and one of only two in this catalog
  personal_data: none # employer postings only, no candidate data in the feed
  terms_url: https://jobspresso.co/terms/
  read_at: 2026-08-03
  read_depth: full
  verbatim:
    - "Permission is granted to temporarily download one copy of the materials (information or software) on Jobspresso's web site for personal, non-commercial transitory viewing only."
    - "use the materials for any commercial purpose, or for any public display (commercial or non-commercial)"
    - 'transfer the materials to another person or "mirror" the materials on any other server'
    - "modify or copy the materials"
    - "This license shall automatically terminate if you violate any of these restrictions and may be terminated by Jobspresso at any time."
    - "Upon terminating your viewing of these materials or upon the termination of this license, you must destroy any downloaded materials in your possession whether in electronic or printed format."

# ── ADMISSION TEST — keyless is not the same as permitted. Both halves, separately.
admission:
  documented_public_api: false # NOT documented by Jobspresso anywhere that could be found
  terms_permit_automated_access: false # the licence is "personal, non-commercial" viewing only
  verdict: >
    FAILS BOTH HALVES — the same shape as 4dayweek, and the only other source in this catalog
    to fail both. `?feed=job_feed` is the WP Job Manager plugin's stock endpoint, so it exists
    because the plugin ships it rather than because Jobspresso published an API; no developer
    documentation, no API terms page, and no mention of a feed anywhere in Jobspresso's own
    material was found. Meanwhile the only contract it does publish grants a licence for
    "personal, non-commercial transitory viewing only" and prohibits commercial use, public
    display of any kind, copying, modification and mirroring. Undocumented AND barred is a
    status-changing combination, not a footnote — flagging it rather than setting the status,
    since status is the maintainer's call.

endpoint:
  base: https://jobspresso.co/?feed=job_feed
  method: GET
  slug_pattern: null

query:
  title_search: false # by SHAPE — an RSS document has no query interface to test
  location_search: false
  filters: [] # WP Job Manager feeds accept `search_location` / `job_categories`; NOT probed
  param_validation: ignores_unknown

limits:
  page_size: 10 # not a page size — it is the entire feed
  max_page: null # RSS: a single document, no paging concept
  reachable_per_query: 10
  rate_limit: unknown # none published; not probed
  quota: unknown
  concurrency_safe: unknown

volume:
  advertised: null # the site advertises "1,000+ Job Openings"; the feed serves 10
  reachable: 10 # advertised and reachable disagree by two orders of magnitude here
  measured_at: 2026-08-03

freshness:
  median_age_days: 27.0
  pct_within_100d: 1.0 # █████
  date_sorted: unknown # a single document of ten items; there is nothing to sort across
  measured_at: 2026-08-03

coverage:
  countries: [multi] # the captured row lists ten countries in one string
  us_share: null # `location` is a free-text country list, not countable as written
  sector_skew: >
    remote-first and broad — the site's own categories span AI/data, developer, product,
    design, support, marketing, sales and writing

location:
  shape: free_text
  primary_path: location
  type: string
  state_available: false
  state_path: null
  city_path: null
  country_path: null
  multi_value: true # ONE string holds a whole country list
  multi_delimiter: ","
  free_text_fallback: location
  gazetteer_needed: true
  notes: >
    An eligibility region list, not a workplace: the captured row's `location` reads
    "US, Canada, Americas, Europe, UK, Mexico, Australia, New Zealand, Japan, Singapore" —
    ten comma-separated entries mixing countries ("Mexico") with continents ("Americas") and
    with a country that is inside one of them ("US" and "Americas" both present). Splitting on
    the comma yields tokens at three different geographic scales, so this cannot be normalized
    to a single place, and US state filtering is impossible.

remote:
  signal: derived
  path: null
  rule: "every posting is remote by construction — Jobspresso lists nothing else"
  reliability: "the site is remote-only, so the signal is the source itself"

fields:
  title: { path: title, type: str } # embeds the company and a marketing clause — see traps
  company: { path: company, type: str } # "Maverick Trading" — a real field, unusual for RSS
  body: { path: encoded, type: str, median_chars: 6733 } # content:encoded — NOT `description`
  posted: { path: pubDate, type: str } # RFC 822 with a +0000 offset
  url: { path: link, type: str } # `guid` is a different, non-canonical ?post_type= URL
  tags: null
  salary: null
  employment_type: { path: job_category, type: str } # "Contract" — YES, `job_category`. See traps
  function: { path: job_type, type: str } # "Others" — YES, `job_type`. See traps
  org_unit: null
  employer_org: { path: company, type: str } # the employing organisation
  seniority: null
  # unmapped keys in the record: content (empty string), creator, description, post-id

traps:
  - '`job_type` AND `job_category` HOLD EACH OTHER''S VALUES. On the captured row `job_type` is "Others" — a category — and `job_category` is "Contract" — an employment type. Mapping them by name puts an employment type in the category column and vice versa, on every row, with no error. This is the naming trap _SCHEMA.md documents for `department`, in its purest form.'
  - "`description` is NOT the body. It is a 372-character excerpt with HTML entities left in (`Lion&#8217;s Share`). The real body is `encoded` — RSS `content:encoded` — at 4,420 chars on the captured row and a 6,733-char median across the feed. A consumer reading the obviously-named field gets a tenth of the text."
  - "`content` is an EMPTY STRING sitting beside both of them. Three body-ish keys, one of which is right, one of which is a stub, and one of which is empty."
  - '`creator` is not a person — it is the company name with an HTML `<br>` and a location line glued on: "Maverick Trading<br>⚲&nbsp;US, Canada, Americas, …". The RSS `dc:creator` element is being used as a display blob. A real `company` field exists separately; use that.'
  - 'The title carries marketing copy: "Remote Stock & Options Trader — Trade Firm Capital, Keep Up to 90% of Profits". Title-based relevance scoring is scoring an advertisement.'
  - 'Ten items is the WHOLE feed while the site advertises "1,000+ Job Openings". Advertised and reachable differ by two orders of magnitude — the same gap The Muse shows at a larger scale.'
  - "`guid` and `link` are DIFFERENT URLs — `guid` is `?post_type=job_listing&p=163372`, a non-canonical form. Deduplicating on `guid` and displaying `link` means two identifiers for one job."
  - "The terms prohibit commercial use AND any public display, commercial or not, and require destroying downloaded materials on termination. That is broader than a caching restriction — it covers showing the rows at all."
  - "`?feed=job_feed` is WP Job Manager's STOCK endpoint, not a Jobspresso product. It answers because the plugin ships it, and no Jobspresso documentation, API page, or feed announcement was found. Combined with the licence above, this source is undocumented AND barred — see `admission:`."
  - "The feed payload carries NO rights element: its RSS `<channel>` has a `<generator>` naming WordPress 7.0.2 and no `<copyright>`. So unlike Remotive, Remote OK and NoDesk, there is nothing in-band, and the site terms are the whole contract."
---

# jobspresso

## A real record

Captured 2026-08-03 from `jobspresso.co/?feed=job_feed`, first item. Both body fields truncated
with markers; rest verbatim.

```json
{
  "title": "Remote Stock & Options Trader — Trade Firm Capital, Keep Up to 90% of Profits",
  "link": "https://jobspresso.co/job/maverick-trading-worldwide-various-remote-stock-options-trader-trade-firm-capital-keep-up-to-90-of-profits/",
  "guid": "https://jobspresso.co/?post_type=job_listing&p=163372",
  "post-id": "163372",
  "creator": "Maverick Trading<br>⚲&nbsp;US, Canada, Americas, Europe, UK, Mexico, Australia, New Zealand, Japan, Singapore",
  "company": "Maverick Trading",
  "location": "US, Canada, Americas, Europe, UK, Mexico, Australia, New Zealand, Japan, Singapore",
  "job_type": "Others",
  "job_category": "Contract",
  "pubDate": "Thu, 23 Jul 2026 16:56:41 +0000",
  "content": "",
  "description": "Trade with Real Capital. Keep the Lion&#8217;s Share of What You Earn. Maverick Trading is one of the oldest proprietary trading firms in the U.S., with over 25 years of experience funding traders worldwide…[truncated, 372 chars total]",
  "encoded": "<p>Trade with Real Capital. Keep the Lion&#8217;s Share of What You Earn.</p>\n<p>Maverick Trading is one of the oldest proprietary trading firms in the U.S., with over 25 years of experience funding traders worldwide. We&#8217;re looking for entrepreneurial, market-obsessed indiv…[truncated, 4420 chars total]"
}
```

**What this record proves that the field table did not.** Two mappings that a careful engineer
would get wrong by reading the key names, which is the only information a field table gives.

`job_type` is `"Others"`. `job_category` is `"Contract"`. Read those twice: the _type_ field
holds a category and the _category_ field holds a type. "Contract" is an employment type by any
reading; "Others" is a catch-all bucket. So the correct mapping is `function: job_type` and
`employment_type: job_category` — each key assigned to the opposite of what it is called. There
is no error, no null, nothing that fails; the columns just quietly hold each other's data on
every row.

The body is the second one. `description` is the field whose name says "the description", and
it is a 372-character excerpt with entities unresolved. The actual posting — 4,420 characters —
is under `encoded`, which is RSS's `content:encoded` element and reads like an implementation
detail. And `content` sits beside both of them holding the empty string. A consumer that maps
`body: description` gets under a tenth of the text and no indication anything is missing.

The rest of the record is better than most feeds manage: a real `company` field, a real
`location`, a canonical `link`. `creator` is worth one look — it is the company name with a
`<br>` and a location line concatenated on, which is a rendered HTML fragment inhabiting RSS's
author element. Ignore it; `company` has the clean value.

## The location is at three scales at once

`"US, Canada, Americas, Europe, UK, Mexico, Australia, New Zealand, Japan, Singapore"`

Ten comma-separated tokens: two continents-ish regions (Americas, Europe), seven countries, and
"US" listed alongside "Americas" which contains it. This is an eligibility list — where a
candidate may live — expressed at whatever granularity the employer felt like using.

There is no split that produces a consistent geographic level, no field that says which token
is a country and which is a region, and no way to answer "is this job open to someone in
Kentucky" beyond string-matching "US". Recorded as `free_text` with `multi_value: true` because
that is what it is, and `gazetteer_needed: true` because even a gazetteer only partly helps
when the entries nest.

## How this was probed

`catalog/_probe.py` on 2026-08-03, one request per second. Steps 2 and 3 are answered by shape
rather than by probe: an RSS document has no query interface and no pages. Terms read the same
day and quoted verbatim above.

```
junk:  control 200 (10 rows) vs junk 200 (10 rows) -> ignores_unknown
title: RSS feed — no query interface
pages: RSS feed — single document
fresh: 10 dated rows across pages [0]; p0 median 27.0d, 100% within 100d
body:  median 6733 chars over 10 rows
```

## The admission test, and why this one fails both halves

Two separate questions, because a keyless 200 answers neither.

**Is there published developer documentation for this endpoint?** No. `?feed=job_feed` is the
stock RSS route that WP Job Manager — a WordPress plugin — exposes on every site running it.
Jobspresso publishes no API page, no feed documentation, and no announcement that could be
found; the endpoint answers because the plugin ships it, not because anyone offered it. That is
a weaker position than NoDesk's, whose feed the site itself links, and the same position as
4dayweek's front-end endpoint.

**Do the terms permit automated access?** No, and this is the plainer half. The Use License is
granted for "personal, non-commercial transitory viewing only" and then prohibits commercial
use, "any public display (commercial or non-commercial)", copying, modification, and mirroring
to another server. A harvester that stores rows and a front end that displays them are both
named. There is no ambiguity to resolve here — unlike the sources whose terms are simply silent.

Searched, for completeness, on the principle that API terms live somewhere different on every
site: the site terms (found, quoted above), a `/terms-of-service/` path (404), and the feed's
own RSS `<channel>` header for an in-band notice on the Remotive precedent (none — only a
WordPress `<generator>`).

Undocumented and barred is the 4dayweek shape. Recorded as a finding; the status is the
maintainer's call.

**Not checked:** WP Job Manager feeds conventionally accept parameters like `search_location`
and `job_categories`, and NONE were tried — so `filters: []` here means untested, not proven
absent, and this source may well be more queryable than the RSS shape suggests. The rate limit
stays `unknown`; none is published and bursting was not run. Whether the `job_type`/`job_category`
swap holds across all ten items was confirmed on one row only. The relationship between the
site's advertised "1,000+" and the ten items served was not investigated — whether the feed is
capped, paginated by an untried parameter, or genuinely a sample is open. And the licence, while
read in full, is generic site terms rather than feed-specific ones, so `prohibited` reflects the
text as written against content the site itself syndicates.
