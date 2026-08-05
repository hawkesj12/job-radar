---
name: teamtailor
display_name: Teamtailor
status: wired
lane: depth
verdict: >
  A confirmed new DEPTH platform: one keyless request to `{slug}.teamtailor.com/jobs.json`
  returns a whole board with full bodies and, nested one level down, a complete schema.org
  JobPosting carrying street, locality, region, postcode and country code — the most completely
  structured location in this catalog. Left `evaluated` rather than wired for two reasons worth
  stating: its terms of service could NOT be located, and the sampled board is Swedish, so
  neither its US share nor its English-language coverage has been established.

auth:
  type: none
  env: []
  signup: null
  notes: keyless; `{slug}.teamtailor.com/jobs.json` requires no key and no registration

license: # a CONTRACT — quoted, not paraphrased
  commercial_use: unclear
  commercial_terms: >
    Teamtailor's terms are a CUSTOMER agreement and they do not reach a third party reading
    a public career-site feed. The formation event is explicit — the Agreement is "between
    Teamtailor and the Customer" and consists of "the Order Form, the terms and conditions,
    the data processing agreement" — and the licence it grants runs to "The Customer and its
    representatives, such as employees ('Users')". Reading {slug}.teamtailor.com/jobs.json is
    not signing an Order Form. Section 14 confirms the same scoping for APIs: "You can choose
    to access the Service via external interfaces (e.g. APIs)", where "You" is the Customer
    throughout. So no clause addresses a third party consuming a customer's public feed —
    `unclear`, and `unclear` is not `allowed`.
  attribution_required: unknown # never addressed for third parties
  redistribution: unknown # never addressed for third parties
  derivative_works: unknown # never addressed for third parties
  cache_policy: none_stated
  on_termination: none_stated # the termination clauses concern a Customer's subscription
  personal_data: none # employer postings only, from what the response contains
  terms_url: https://www.teamtailor.com/en/terms-and-conditions/
  read_at: 2026-08-04
  read_depth: full
  verbatim:
    - "These terms and conditions, together with the following documents and appendices, constitute the entire agreement between Teamtailor and the Customer"
    - 'the Order Form the terms and conditions the data processing agreement ("DPA")'
    - 'The Customer and its representatives, such as employees ("Users") are granted a non-exclusive, non-transferable, non-sublicensable, revocable licence to use the Service in accordance with this Agreement.'
    - "You can choose to access the Service via external interfaces (e.g. APIs)."
    - "Our external interfaces are made available on an as-is basis, with no guarantee of fitness for any specific purpose, availability, completeness or particular content."

endpoint:
  base: https://{slug}.teamtailor.com/jobs.json
  method: GET
  slug_pattern: "{slug}.teamtailor.com/jobs.json"

query:
  title_search: false # whole-board fetch; the feed has no query interface
  location_search: false
  filters: []
  param_validation: ignores_unknown

limits:
  page_size: null # the entire board arrives in one response, unpaged
  max_page: null
  reachable_per_query: null # the whole board — 9 rows for apotea
  rate_limit: unknown # none documented that was found; not probed
  quota: unknown
  concurrency_safe: unknown
  requests_per_company: 1
  cheap_liveness: unknown # no cheap variant was looked for; unmeasured, not absent

volume:
  advertised: null
  reachable: null # a function of how many slugs you can name
  measured_at: 2026-08-03

freshness:
  median_age_days: 62
  pct_within_100d: 0.56
  date_sorted: unknown # one unpaged document; there is nothing to sort across
  measured_at: 2026-08-03
  note: >
    Nine rows from ONE small Swedish board. A median over nine rows is barely a statistic —
    treat 62 days as an observation about Apotea, not about Teamtailor.

coverage:
  countries: [SE] # the ONE board sampled; the platform is broadly European
  us_share: null # not established — the sampled board is Swedish and posts in Swedish
  sector_skew: "per-company by construction — the board is whatever that employer posts"

location:
  shape: structured
  primary_path: _jobposting.jobLocation[].address
  type: array_of_object
  state_available: true # `addressRegion` — "Stockholm" on the captured row
  state_path: _jobposting.jobLocation[].address.addressRegion
  city_path: _jobposting.jobLocation[].address.addressLocality
  country_path: _jobposting.jobLocation[].address.addressCountry # "SE" — an ISO code
  multi_value: true # `jobLocation` is an array
  multi_delimiter: null
  free_text_fallback: null # there is NO location field at the top level at all
  gazetteer_needed: false
  notes: >
    The most complete address in this catalog — streetAddress, addressLocality, postalCode,
    addressRegion and an ISO-2 addressCountry, as a schema.org PostalAddress. It is also the
    best-hidden: none of it exists at the top level of the record, only inside `_jobposting`.
    Unlike Ashby's `addressCountry`, this one is already a code ("SE"), so it joins without
    normalizing.

remote:
  signal: none
  path: null
  rule: null
  reliability: >
    No remote field at the top level and none inside `_jobposting` on the captured row.
    schema.org defines `jobLocationType: TELECOMMUTE` for remote postings and this row is an
    on-site job, so whether Teamtailor emits that key for a remote role is UNKNOWN and worth
    one look at a remote-first board before any rule is written.

fields:
  title: { path: title, type: str }
  company: { path: _jobposting.hiringOrganization.name, type: str } # "Apotea"
  body: { path: content_html, type: str, median_chars: 4635 }
  posted: { path: date_published, type: str } # ISO 8601 with a real offset
  url: { path: url, type: str }
  tags: null
  salary: null
  employment_type: null
  function: null
  org_unit: null
  employer_org: { path: _jobposting.hiringOrganization.name, type: str } # the employer
  seniority: null
  # unmapped keys in the record: id, _jobposting (whose own keys carry everything structured:
  # @context, @type, description, identifier, datePosted, hiringOrganization, jobLocation)

traps:
  - '**It reports its board owner**, which almost nothing else does: the JSON Feed''s `title` is the company name (`apotea` -> "Apotea"), arriving free in the response you already fetch. Only Greenhouse otherwise answers who owns a board, and Greenhouse charges a second request for it. That makes Teamtailor safe for aggressive slug guessing in `discover.from_names`, which is currently withheld from every ATS except Greenhouse.'
  - "Everything structured lives inside `_jobposting`, and NOTHING structured lives at the top level. A consumer reading top-level keys sees `id, title, url, date_published, content_html` and concludes this source sends no location and no company at all — while a full schema.org JobPosting with a five-field postal address sits one level down in the same object."
  - "`_jobposting.description` is a byte-for-byte duplicate of `content_html` (3,994 chars each on the captured row). Keeping both doubles storage for no information."
  - "`hiringOrganization.name` is the employer, so it belongs in `employer_org`. There is no org-unit or department field anywhere in the response — do not manufacture one."
  - "The sampled board posts in Swedish. Any relevance scoring, remote-keyword matching, or English-language title heuristic silently returns nothing on a board like this, and it will not look like an error."
  - "`date_published` carries a real local offset (`+02:00`). Parsing it as naive and then treating it as Eastern shifts every posting by six hours."
  - "The terms are NOT linked from the site footer — only a cookie policy and a privacy policy are. They live at `/en/terms-and-conditions/`, while `/en/terms/`, `/en/legal/` and `/en/terms-of-service/` all 404. An earlier pass recorded `unknown` after those 404s; per _SCHEMA.md rule 5 a 404 meant the path was wrong, not the document absent."
  - "A 404 means the SLUG is wrong, not that the platform is dead — Teamtailor looked dead here until the fourth slug answered."
---

# teamtailor

## A real record

Captured 2026-08-03 from `apotea.teamtailor.com/jobs.json`, first row. Both body fields
truncated with a marker; rest verbatim.

```json
{
  "id": "15a0e5d4-c4b0-4b29-b17c-b24aa68585b5",
  "title": "Kundservicemedarbetare till Apotea | Stockholm",
  "url": "https://apotea.teamtailor.com/jobs/8128762-kundservicemedarbetare-till-apotea-stockholm",
  "date_published": "2026-07-27T09:33:15+02:00",
  "content_html": "<p><strong>Vill du hjälpa människor på riktigt — varje dag?</strong><br>Som Kundservicemedarbetare till Apotea är du en viktig del av kundupplevelsen…[truncated, 3994 chars total]",
  "_jobposting": {
    "@context": "http://schema.org/",
    "@type": "JobPosting",
    "title": "Kundservicemedarbetare till Apotea | Stockholm",
    "description": "<p><strong>Vill du hjälpa människor på riktigt — varje dag?</strong>…[truncated, 3994 chars total — identical to content_html]",
    "identifier": {
      "@type": "PropertyValue",
      "name": "Apotea",
      "value": 8128762
    },
    "datePosted": "2026-07-27T09:33:15+02:00",
    "hiringOrganization": {
      "@type": "Organization",
      "name": "Apotea",
      "sameAs": "https://karriar.apotea.se"
    },
    "jobLocation": [
      {
        "@type": "Place",
        "address": {
          "@type": "PostalAddress",
          "streetAddress": "Sveavägen 168",
          "addressLocality": "Stockholm",
          "postalCode": "113 46",
          "addressCountry": "SE",
          "addressRegion": "Stockholm"
        }
      }
    ]
  }
}
```

**What this record proves that the field table did not.** That the shape of this response is a
trap, and a field table written from the top level would have called this source unusable.

Five keys are visible at the top: `id`, `title`, `url`, `date_published`, `content_html`. No
company. No location. No country. Read that far and Teamtailor is a bare feed you could not
join to anything — worse than Greenhouse, which at least sends a location string.

Then `_jobposting` opens and it is a complete schema.org JobPosting: the employer by name and
by URL, and a `PostalAddress` with street, locality, postcode, ISO country code and region as
five separate fields. That is more structure than any other source in this catalog sends,
including the keyed commercial APIs — and `addressCountry` is already `"SE"`, a code, where
Ashby's is the string "United States".

The lesson generalizes past this source: a leading underscore reads like an internal or debug
key, and dropping `_`-prefixed keys during normalization is a common and reasonable-looking
rule. Here it would discard everything worth having.

## What is missing, and what that means for wiring it

Two absences matter. There is no remote field — not at the top level, not inside
`_jobposting` — and the captured row is an on-site job, so it cannot even tell you whether the
schema.org `jobLocationType: TELECOMMUTE` key appears when the role IS remote. One request to a
remote-first Teamtailor board would settle that, and it has not been made.

And there is no salary, no employment type, no seniority, and no org unit. `_jobposting` has
slots for several of those in the schema.org vocabulary and this board populated none of them,
which is the usual pattern: the container is standard, the fill rate is per-employer.

## How this was probed

`catalog/_probe.py` on 2026-08-03, one request per second, with steps 2 and 3 skipped — a
whole-board endpoint has no query interface to test and no pages to walk.

```
junk:  control 200 (9 rows) vs junk 200 (9 rows) -> ignores_unknown
fresh: 9 dated rows across pages [0]; p0 median 62d, 56% within 100d
body:  median 4635 chars over 9 rows
```

**Not checked, and this is most of the profile.** The terms of service were NOT found: no
canonical legal page surfaced and the `/jobs.json` docs were not read, so every `license:` key
is `unknown` because it is unread — this is the strongest caveat on the page and the reason
the source stays `evaluated`. Everything measured comes from ONE small Swedish board of nine
rows, so freshness is barely a statistic, `us_share` is null rather than guessed, and English
coverage is unestablished. `cheap_liveness` is `unknown` because no cheap variant was looked
for, not because none exists. The rate limit was not probed and none was found documented. And
the remote question above is open on purpose: the row sampled is on-site, so a null remote
signal here proves nothing either way.
