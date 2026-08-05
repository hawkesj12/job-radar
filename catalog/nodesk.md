---
name: nodesk
display_name: NoDesk
status: evaluated
lane: breadth
verdict: >
  A ten-item RSS feed with five keys per item — title, description, date, link, guid — and
  nothing else. No company field, no location, no category, no salary. The company is
  recoverable only by splitting the title on " at ", and there is no location data anywhere at
  all. Its terms of use could NOT be read: nodesk.co returns 403 to every automated fetch, so
  the licence here is unread rather than absent.

auth:
  type: none
  env: []
  signup: null
  notes: >
    Keyless RSS. Note the asymmetry — the FEED serves an automated client fine, while the
    site's own terms pages return 403 to one.

license: # a CONTRACT — quoted, not paraphrased
  commercial_use: unknown
  commercial_terms: >
    COULD NOT VERIFY. `nodesk.co/terms/` is an index page linking to a Terms of Use, a Privacy
    Policy and a Cookie Policy; the index rendered, and both it and the linked
    `nodesk.co/legal/terms/` returned HTTP 403 to automated fetches. So the terms exist, are
    linked, and were not read. This is an UNREAD contract, and reading `unknown` here as
    permission would be exactly the mistake the schema's rights section warns about.
  attribution_required: unknown
  redistribution: unknown
  derivative_works: unknown
  cache_policy: unknown
  on_termination: unknown
  personal_data: none # employer postings only, from what the feed contains
  terms_url: https://nodesk.co/terms/ # index page; the document behind it 403s
  read_at: 2026-08-03 # stamps ONLY the in-feed copyright below; the terms remain unread
  read_depth: summary_only
  verbatim:
    # The ONLY rights statement that could be obtained — served inside the feed itself,
    # in the RSS <channel> header. It asserts copyright and grants nothing.
    - "© 2026 NoDesk"

# ── ADMISSION TEST — keyless is not the same as permitted. Both halves, separately.
admission:
  documented_public_api: true # an RSS feed is a published syndication format, linked by the site
  terms_permit_automated_access: unknown # the terms could NOT be read — 403
  verdict: >
    PASSES the first half and CANNOT ANSWER the second. RSS exists to be consumed by machines
    and the site publishes it, so unlike 4dayweek's front-end endpoint this is a real
    syndication surface. But the terms of use return 403 to an automated fetch, so whether
    they permit automated access is genuinely unknown — and an unread contract is a blocker
    for wiring, not a formality to note and move past.

endpoint:
  base: https://nodesk.co/remote-jobs/index.xml
  method: GET
  slug_pattern: null

query:
  title_search: false # by SHAPE — an RSS document has no query interface to test
  location_search: false
  filters: []
  param_validation: ignores_unknown

limits:
  page_size: 10 # not a page size — it is the entire feed
  max_page: null # RSS: a single document, no paging concept
  reachable_per_query: 10 # the smallest reachable corpus of any source in this catalog
  rate_limit: unknown # none published that could be read; not probed
  quota: unknown
  concurrency_safe: unknown

volume:
  advertised: null
  reachable: 10
  measured_at: 2026-08-03

freshness:
  median_age_days: 14.5
  pct_within_100d: 1.0 # █████
  date_sorted: unknown # a single document of ten items; there is nothing to sort across
  measured_at: 2026-08-03

coverage:
  countries: [unknown] # THERE IS NO LOCATION FIELD — see location
  us_share: null # unresolvable from the feed; the captured row names no place at all
  sector_skew: >
    remote-first and broad — the captured row is an administrative assistant role, not a
    developer one, so this is not a tech-only board

location:
  shape: free_text
  primary_path: null
  type: null
  state_available: false
  state_path: null
  city_path: null
  country_path: null
  multi_value: false
  multi_delimiter: null
  free_text_fallback: null # there is NOTHING to fall back to
  gazetteer_needed: true
  notes: >
    There is no location field of any kind. Not a string, not an object, not a region label —
    the item has five keys and none of them is a place. Whatever location information exists
    lives inside the description prose or on the linked page, so location for this source is
    not a mapping change and not a parsing project but a fetch-and-extract project against a
    second URL. Every other source in this catalog answers "where" somehow; this one does not.

remote:
  signal: derived
  path: null
  rule: "every posting is remote by construction — NoDesk lists nothing else"
  reliability: "the site is remote-only, so the signal is the source itself"

fields:
  title: { path: title, type: str } # embeds the company after " at " — see traps
  company: null # NO company field. Recoverable only by splitting the title
  body: { path: description, type: str, median_chars: 689 } # the shortest bodies measured
  posted: { path: pubDate, type: str } # RFC 822 with a real offset (+0200)
  url: { path: link, type: str } # `guid` was identical on the captured row
  tags: null
  salary: null
  employment_type: null
  function: null
  org_unit: null
  employer_org: null
  seniority: null
  # unmapped keys in the record: guid

traps:
  - "THE FEED IS UNPARSEABLE BY A STRICT XML PARSER. Its descriptions contain HTML named entities like `&rsquo;`, which XML does not define — XML defines five named entities and HTML about two thousand. `ET.fromstring` raises `undefined entity: line 17, column 37`, `_probe.py`'s `records()` caught that and returned an empty list, and nodesk reported 0 rows, `title_search: false` and NO captured record at all from a 200 carrying 11 KB of real items. A parse failure and an empty feed must not look alike. `_probe.py` now rewrites non-XML named entities to numeric refs before parsing; anything else consuming this feed needs the same guard."
  - 'There is NO company field. The company is glued to the end of the title after '' at '' — "Virtual Administrative Assistant – Office Management at Personify Health". Splitting on '' at '' is the only route, and it breaks on any title legitimately containing the word ("Analyst at Scale", "Engineer at Rest").'
  - "There is NO location field. Nothing in the item names a place, so a consumer's location filter can never match a NoDesk row, and the source will look empty rather than broken."
  - "Ten items is the WHOLE feed, not a page. There is no `page`, no `offset`, and no larger endpoint that was found."
  - "Bodies are 689 median chars — by far the shortest measured here, against Remotive's 9,646. Relevance scoring on this source is working with roughly a paragraph."
  - "`pubDate` carries a +0200 offset, so parsing it as naive and treating it as Eastern shifts every posting by six hours."
  - "The terms of use return 403 to automated fetches while the feed itself does not. The licence is unread, not absent, and the 403 is not evidence of permissiveness."
  - "The ONLY rights statement obtainable is inside the feed: the RSS `<channel>` carries `<copyright>© 2026 NoDesk</copyright>`. That is an assertion of ownership, not a grant — the opposite of Remotive's and Remote OK's in-payload notices, which at least say what you may do. It is worth knowing the header is there, because it is the one place a rights signal ships for this source."
---

# nodesk

## A real record

Captured 2026-08-03 from `nodesk.co/remote-jobs/index.xml`, first item. Description truncated
with a marker; rest verbatim and complete — the item really is five keys.

```json
{
  "title": "Virtual Administrative Assistant – Office Management at Personify Health",
  "description": "Personify Health is seeking a Virtual Administrative Assistant – Office Management to support our growing remote operations. In this role, you will serve as the organizational backbone of our office management function b…[truncated, 1273 chars total]",
  "pubDate": "Thu, 30 Jul 2026 16:27:53 +0200",
  "guid": "https://nodesk.co/remote-jobs/personify-health-virtual-administrative-assistant-office-management/",
  "link": "https://nodesk.co/remote-jobs/personify-health-virtual-administrative-assistant-office-management/"
}
```

**What this record proves that the field table did not.** How much is genuinely not here.

Five keys, and two of them (`guid`, `link`) hold the same URL. So the usable content is a
title, a paragraph and a date. There is no company, no location, no salary, no employment
type, no category, no tags.

The company is in the title, after " at ": _Personify Health_. That is recoverable, and the
recovery rule is fragile in a specific way — job titles contain the word "at" for reasons
unrelated to employers, and every false split produces a plausible-looking wrong company rather
than an error. It is the difference between a missing field and a quietly wrong one.

The location is not recoverable at all. The description's opening sentence says "remote
operations" and never names a country, a state or a city. A consumer's location filter cannot
match this source, and the symptom is zero NoDesk rows surviving the filter rather than an
error anyone would investigate.

## Why this profile did not exist until today

The first probe run reported nodesk as `0 rows`, `title_search: false`, no dates, no body, and
saved no captured record — so `_scaffold.py` skipped it entirely and there was no draft to
finish. That looked like a dead or empty feed.

It was a parser bug. NoDesk's descriptions contain `&rsquo;` — a right single quotation mark,
written as an HTML named entity. XML defines exactly five named entities (`&amp;`, `&lt;`,
`&gt;`, `&quot;`, `&apos;`) and `&rsquo;` is not among them, so Python's `ElementTree` raises
`undefined entity: line 17, column 37`. `records()` catches `ParseError` and returns `[]`,
which is the correct behaviour for genuinely malformed input and the wrong summary for this
one: the feed is valid RSS 2.0 carrying 11 KB of real items behind one unresolvable entity.

`_probe.py` now rewrites every non-XML named entity to its numeric form before parsing, which
every parser accepts. The fix is commented there with this measurement, because the failure
mode — a 200, a well-formed-looking feed, and a silent zero — is the shape that makes a live
source look dead.

The wider lesson for this catalog: `_SCHEMA.md` rule 4 says a 200 is not a pass. This is the
inverse and it needs saying too — a zero-row result is not proof of an empty source.

## How this was probed

`catalog/_probe.py` on 2026-08-03, one request per second, re-run the same day after the
entity fix above. Steps 2 and 3 are answered by shape rather than by probe: an RSS document has
no query interface and no pages.

```
junk:  control 200 (10 rows) vs junk 200 (10 rows) -> ignores_unknown
title: RSS feed — no query interface
pages: RSS feed — single document
fresh: 10 dated rows across pages [0]; p0 median 14.5d, 100% within 100d
body:  median 689 chars over 10 rows
```

## Where the terms were looked for

Because API-specific terms sit in a different place on every site in this catalog, the search
here was not one page:

- `nodesk.co/terms/` — the index page rendered and links a Terms of Use, a Privacy Policy and
  a Cookie Policy. The linked documents 403.
- `nodesk.co/terms-of-use/` and `nodesk.co/legal/terms/` — both 403.
- **The feed payload itself** — checked, on the Remotive/Remote OK precedent that a source may
  ship its licence inside the response. It carries one rights element:
  `<copyright>© 2026 NoDesk</copyright>` in the RSS `<channel>` header. That asserts ownership
  and grants nothing, so it is quoted above as evidence of what WAS found rather than as a
  licence.

So the terms exist, are linked from a page that renders, and are unreadable by an automated
client — which is a distinct state from Teamtailor (no terms page found at all) and from
Arbeitnow (terms found, buried mid-document). All three read as `unknown`-ish in a summary
table and none of them means the same thing.

**Not checked, and the licence is the important one.** The terms of use were NOT read:
`nodesk.co/terms/` links to them and both that index and the document behind it return 403 to
automated fetches, so every `license:` key is `unknown` because it is unread. That is the
single biggest gap on this page and it is not a formality — a ten-row feed with no location and
no company is a marginal source on the data alone, and an unread contract is the reason not to
wire it regardless. Also unchecked: whether a larger or paginated NoDesk feed exists (only the
one index.xml was tried), whether category-specific feeds exist as they do for We Work
Remotely, the rate limit, and whether the ' at ' title convention holds across all ten items —
it was confirmed on one.
