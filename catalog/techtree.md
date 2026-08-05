---
name: techtree
display_name: TechTree
status: rejected
lane: breadth
verdict: >
  REMOVED FROM THE PACKAGE 2026-08-04, on the measured facts rather than the contested
  licence. It is the only
  source in the catalog carrying PII — `delivery_owner` names a real TechTree staff member —
  and 60 of 76 postings hide the employer behind "TechTree's client", which collapses
  unrelated companies in any dedup keyed on company name. It is also the stalest wired
  breadth source (45-day median) and not a remote board (24 of 76). Separately its terms
  read as prohibiting this use, though that reading is disputed and both sides are recorded
  below. It is a London recruiting agency's candidate pipeline, not a job board.

auth:
  type: none
  env: []
  signup: null
  notes: >
    Keyless, but the endpoint is the site's own front-end API, not a published developer
    product. No API documentation was found.

license: # a CONTRACT — quoted, not paraphrased. This one is decisive.
  commercial_use: prohibited
  commercial_terms: >
    The terms address this use case directly rather than by implication. Section 27
    forbids using Platform data or outputs to enrich external databases, build competing
    products, or automate scraping. Section 24 separately forbids scraping and commercial
    exploitation. A job aggregator that harvests these postings into its own store and
    serves them is squarely inside both. The operator is C2C Talent Network LTD (London),
    and the Platform is a recruiting agency's system, so a competing job-search product is
    a foreseeable adverse use rather than a stretch reading.
  attribution_required: unknown # not addressed for third parties
  redistribution: prohibited
  derivative_works: prohibited
  cache_policy: none_stated
  on_termination: "TechTree may suspend or terminate access where misuse occurs or these Terms are breached"
  personal_data: contains_pii # `delivery_owner` names a real TechTree staff member — measured
  terms_url: https://techtree.dev/terms
  read_at: 2026-08-03
  read_depth: full
  verbatim:
    - "Users may not use Platform data or outputs to: train AI systems; enrich external databases; build competing products; automate scraping; conduct unauthorised extraction."
    - "Users may not: reverse engineer; replicate; scrape; commercially exploit; create competing systems."
    - "TechTree may suspend or terminate access where: misuse occurs; fraud is suspected; these Terms are breached; legal risk arises."
    - 'The Platform is operated by: C2C Talent Network LTD (trading as "TechTree") 124 City Road London, England EC1V 2NX'

endpoint:
  base: https://jobs.techtree.dev/api/public-job-posting?visibility=job_board_only
  method: GET
  slug_pattern: null

query:
  title_search: false # MEASURED — nine parameter names, none filtered
  location_search: false
  filters: [visibility]
  param_validation: ignores_unknown

limits:
  page_size: 76 # the whole feed in one response
  max_page: null # `page` is ignored — page 999 returns the same 76 rows
  reachable_per_query: 76
  rate_limit: unknown # not probed
  quota: unknown
  concurrency_safe: unknown

volume:
  advertised: null
  reachable: 76
  measured_at: 2026-08-03

freshness:
  median_age_days: 45
  pct_within_100d: 0.74
  date_sorted: unknown
  measured_at: 2026-08-03

coverage:
  countries: [unknown] # `country` is null on all 76 rows despite the key existing
  us_share: null
  sector_skew: >
    Tech only, and European — salary currencies seen were USD 12, EUR 4, PLN 1, GBP 1, with
    58 of 76 stating none. The operator is London-based.

location:
  shape: free_text
  primary_path: locations
  type: list
  state_available: false
  state_path: null
  city_path: null
  country_path: country # the key exists and is null on every one of 76 rows
  multi_value: true
  multi_delimiter: null
  free_text_fallback: locations
  gazetteer_needed: true
  notes: >
    A `country` field exists and is null across the entire feed, which is worse than absent —
    a consumer mapping it gets a column of nulls rather than an obvious gap. `workplace_type`
    is the only reliable geography-adjacent signal.

remote:
  signal: structured
  path: workplace_type
  rule: "workplace_type == 'Remote'"
  reliability: >
    Clean enum, present on every row — but the values are On-site 28, Hybrid 24, Remote 24.
    This is not a remote board; a remote-only consumer keeps under a third of it.

fields:
  title: { path: title, type: str }
  company: { path: company_name, type: str } # anonymised on 60 of 76 rows — see traps
  body: { path: description, type: str, median_chars: 2768 } # plain text
  posted: { path: created_at, type: str }
  url: { path: application_url, type: str }
  tags: { path: skills, type: list }
  salary: { path: [salary_min, salary_max], type: int } # with `salary_currency`, often null
  employment_type: { path: job_type, type: str } # "Full-time"
  function: null
  org_unit: null
  employer_org: null
  seniority: { path: level, type: str } # "Mid-Senior Level" — already normalized
  # unmapped keys in the record: benefits, body_doc, company, company_overview, equity,
  # funding, investors, is_anonymized, is_job_hot, requirements, short_description,
  # team_members, workplace_type

traps:
  - "**`delivery_owner` carries a named individual** — the only PII measured in any source here. It ships in every row of a feed a public harvester would store."
  - '**The terms read as prohibiting this use, and that reading is DISPUTED.** Section 27 names "enrich external databases" and "automate scraping"; the counter-argument is that §24 is boilerplate and the specific clauses are candidate-scoped. Both cases are set out below — do not treat `prohibited` here as settled the way We Work Remotely''s is.'
  - '**The optional LLM re-rank collides with the one undisputed clause.** "train AI systems" is prohibited by terms both readers accept; sending row text to a model must not happen for this source.'
  - '**60 of 76 postings are anonymised** — `is_anonymized: true` and `company_name` reads "TechTree''s client". Four out of five rows have no real employer, which defeats any product promising direct-to-company listings and pollutes company-level dedup with a single fake name.'
  - "`country` exists on every row and is null on every row. Mapping it yields a fully-null column that looks populated in a schema."
  - "It is not a remote board. On-site 28, Hybrid 24, Remote 24 — the majority is not remote."
  - "`body_doc` is a ProseMirror/TipTap document tree, not text. `description` is the plain-text twin and is the one to read; walking `body_doc` naively yields node type names."
  - "`equity`, `funding` and `investors` are objects whose fields are empty strings rather than null or absent — they look structured and carry nothing."
  - "The median posting is 45 days old and only 74% fall inside 100 days — the stalest wired breadth source measured."
---

# techtree

## A real record

Captured 2026-08-03. Description truncated with a marker; empty structural objects shown as
returned, because their emptiness is the point.

```json
{
  "title": "AI Engineer",
  "company_name": "TechTree's client",
  "is_anonymized": true,
  "short_description": "Build real-time speech-to-text for military environments — degraded audio, on-premise, …[truncated]",
  "description": "Who we are\n\nA European defence startup based between Paris and Warsaw, building moder…[truncated, 2768 chars median]",
  "job_type": "Full-time",
  "workplace_type": "On-site",
  "level": "Mid-Senior Level",
  "salary_min": 60000,
  "salary_max": 80000,
  "salary_currency": "EUR",
  "skills": [
    "Python",
    "Speech-to-Text",
    "Voice AI",
    "Audio Processing",
    "ML",
    "Fine-tuning",
    "NLP"
  ],
  "requirements": [],
  "benefits": [],
  "equity": {
    "type": "",
    "percentage": "",
    "vesting_period": "",
    "cliff_period": "",
    "additional_details": ""
  },
  "funding": {
    "total_funding_amount": "",
    "last_round_amount": "",
    "last_round_type": ""
  },
  "investors": { "all": [], "last_round": [] },
  "company_overview": "",
  "country": null,
  "application_url": "https://jobs.techtree.dev/job/0515f6f5-8f30-4cb6-abd2-16bf87137a4d/apply",
  "is_job_hot": true,
  "body_doc": {
    "type": "doc",
    "content": [{ "type": "heading", "…": "…[truncated ProseMirror tree]" }]
  }
}
```

**What this record proves that the field table did not.** `company_name` is `"TechTree's
client"`. Not a company — a placeholder. A field table listing `company: company_name` looks
perfectly healthy and would carry that string into 60 of 76 rows.

The record also shows the shape of the emptiness: `requirements`, `benefits`, `equity`,
`funding`, `investors`, `company_overview` all exist and all carry nothing, with the objects
filled by empty strings rather than nulls. That is a schema designed for a richer product than
the feed actually serves, and it makes a field-presence check useless — every key is present,
almost none is populated.

What is genuinely good here: `level` is a normalized seniority, `skills` is a real tag array,
and salary arrives as two integers plus a currency.

## Why this should come out of the package

The terms are not ambiguous and they were read in full. Section 27, "Restricted AI Usage and
Data Scraping": users may not use Platform data or outputs to "train AI systems; enrich
external databases; build competing products; automate scraping; conduct unauthorised
extraction." Section 24 adds that users may not "scrape; commercially exploit; create competing
systems."

Harvesting these postings into a store and serving them is enriching an external database, and
a job-search product built from a recruiting agency's pipeline is a competing product by the
plain meaning of the phrase. The operator is C2C Talent Network LTD of London — an agency, not
a job board — which is also why four of five listings hide the employer.

Even setting the rights aside, the fit is poor: 45-day median age, no country data, and a
majority of non-remote roles. There is no version of this source that is worth the exposure.

## A recorded disagreement about the licence

Two readers reached different answers here, so both are recorded rather than one being chosen
quietly.

**The case for `unclear`** (the catalog spar's, and it is not weak): the one specific clause is
candidate-scoped — "Companies may only use **Candidate information** for legitimate hiring
activity", surrounded by "build external talent pools; share candidate data externally". What
remains, §24's "reverse engineer; replicate; scrape; commercially exploit; create competing
systems", is boilerplate that appears almost verbatim in Ashby's, Braintrust's, Lever's and
Rippling's terms — all of which this catalog records as `unclear`. Rippling's is arguably more
on-point still: "may not extract data from Rippling… as part of any **data aggregation
service**." Grading TechTree `prohibited` while grading those `unclear` is an inconsistency.

**The case for `prohibited`, which is what is recorded**, rests on two things that argument does
not address. First, §27 is a separate section from §24 and is not candidate-scoped: "Users may
not use **Platform data or outputs** to: train AI systems; **enrich external databases**; build
competing products; **automate scraping**; conduct unauthorised extraction." Harvesting a feed
into a store is enriching an external database by the plain words. Second — and this is the
distinction that resolves the parity argument — **the clauses bind different people**.
Rippling's restrictions attach to "Authorized Representatives" and Lever's ToS governs its
employer customers; a third party reading a public board never became either. TechTree's bind by
access: "By registering for, accessing, or using the Platform, you agree to these Terms."

**What both readers agree on:** the licence is the weaker reason to drop this source. The
stronger ones are measured facts — `delivery_owner` carries a named individual, so this is the
only `contains_pii` source in the catalog, and 60 of 76 rows hide the employer behind
"TechTree's client", which collapses unrelated companies in any dedup keyed on company name.
Both readers also agree that job-radar's optional LLM re-rank collides with the one clause
nobody disputes — "train AI systems" — and that at minimum the re-rank must not run over rows
from this source.

## How this was probed

`catalog/_probe.py` on 2026-08-03, one request per second. Terms located at `techtree.dev/terms`
after `/terms` on the jobs subdomain returned a different document, read in full the same day,
and quoted verbatim above. Field distributions come from one further request over the feed.

```
junk:  control 200 (76 rows) vs junk 200 (76 rows) -> ignores_unknown
title: nine parameter names tried; filters on: nothing
pages: `page` appears IGNORED — page 999 returned the same 76 rows as the control
fresh: 76 dated rows, median 45.0d, 74% within 100d
body:  median 2768 chars over 74 rows
distribution (76 rows): is_anonymized 60 · country null 76 ·
        workplace_type On-site 28 / Hybrid 24 / Remote 24 ·
        salary_currency none 58, USD 12, EUR 4, PLN 1, GBP 1
```

**Not checked:** the rate limit (`unknown` — no burst was run). Whether the `visibility`
parameter accepts values other than `job_board_only`, which might expose a different or larger
set; given the terms, probing for a wider surface was not worth doing. No API documentation was
searched for beyond the terms, because the terms settle the question regardless of what a doc
might permit.
