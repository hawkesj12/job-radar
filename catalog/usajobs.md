---
name: usajobs
display_name: USAJOBS (US OPM)
status: wired
lane: breadth
verdict: >
  Federal postings, all sectors, free key, and the ONLY wired source already handling "remote"
  correctly. Its breadth is entirely a function of the harvest keyword list — widening that list
  is the cheapest non-tech supply win available. COMMERCIALLY CONSTRAINED by its terms.

auth:
  type: free_key
  env: [USAJOBS_API_KEY, USAJOBS_EMAIL]
  signup: https://developer.usajobs.gov/apirequest/
  notes: >
    Key + the registering email are sent as headers (Authorization-Key, User-Agent). Registration
    names the requesting organisation, and the terms bind the data to THAT registered use.

license:
  commercial_use: prohibited
  commercial_terms: >
    Terms prohibit renting, leasing, loaning, selling, trading or creating derivative works of the
    API services and data. Data is for the explicit use of the organisation named on the API
    Registration Form; any other use needs prior WRITTEN approval from OPM USAJOBS.
    NOTE the distinction that matters: the underlying federal postings are US-government works and
    generally public domain, but the API TERMS bind separately from the data's copyright status.
    A different acquisition route (e.g. NLx) may carry different terms for similar content.
  attribution_required: unclear
  attribution_text: null
  redistribution: prohibited
  derivative_works: prohibited
  cache_policy: none_stated
  on_termination: none_stated
  personal_data: none
  terms_url: https://developer.usajobs.gov/
  read_at: 2026-08-03
  read_depth: summary_only
  verbatim:
    - "may not rent, lease, loan, sell, trade or create derivative works"
    - "for the explicit use of the requesting company identified on the … API Registration Form"
  todo: >
    Read the full ToS end to end before any monetisation, and ask OPM in writing what a
    fit-ranking consumer site requires. Public-domain status is an argument, not a permission.

endpoint:
  base: https://data.usajobs.gov/api/search
  method: GET
  slug_pattern: null

query:
  title_search: true
  filters: [Keyword, LocationName, Radius, RemoteIndicator, ResultsPerPage]
  param_validation: ignores_unknown
  notes: >
    DANGEROUS: a junk parameter returned the UNFILTERED result (354), identical to sending nothing.
    A misspelled parameter name therefore does nothing, forever, silently. Every param this adapter
    sends should be re-verified against a control query.

limits:
  page_size: 500
  max_page: unknown
  reachable_per_query: unknown
  rate_limit: unknown
  quota: unknown
  concurrency_safe: unknown

volume:
  advertised: null
  reachable: unknown
  measured_at: 2026-08-03
  notes: >
    Breadth is a pure function of the KEYWORD LIST, not of the source. 15 non-tech keywords
    measured 2026-08-03 returned 2,468 federal postings — medical assistant 736, registered nurse
    620, social worker 439, warehouse 213, physical therapist 122, accountant 98, occupational
    therapist 69, paramedic 39, dental hygienist 35, diesel mechanic 23, welder 23, electrician 21,
    truck driver 17, high school teacher 10. jobfitr's whole corpus holds 3,737 usajobs rows.

freshness:
  median_age_days: unknown
  pct_within_100d: unknown
  date_sorted: unknown

coverage:
  countries: [US]
  us_share: 1.0
  sector_skew: >
    All sectors, but FEDERAL ONLY — the VA is the largest health system in the country and DoDEA
    employs teachers, so healthcare/education/trades are genuinely present. Will never produce a
    private-sector employer.
  remote_signal: structured
  remote_how: "&RemoteIndicator=True — verified: 354 unfiltered vs 1 filtered"

fields:
  title: { path: PositionTitle, type: string }
  company: { path: OrganizationName, type: string }
  body: { path: QualificationSummary, type: string, median_chars: 2051 }
  location:
    {
      path: PositionLocationDisplay,
      type: string,
      note: "'Anywhere in the U.S. (remote job)' for remote",
    }
  posted: { path: PublicationStartDate, type: iso8601 }
  url: { path: PositionURI, type: string }
  function:
    {
      path: JobCategory,
      type: array_of_object,
      shape: "[{Name, Code}]",
      note: "OPM occupational series — a REAL controlled vocabulary",
    }
  org_unit: { path: SubAgency, type: string }
  employer_org:
    {
      path: DepartmentName,
      type: string,
      note: "THE EMPLOYER. Rode in the deprecated `department` until 0.9.0 removed that field; now UNMAPPED — the record has no employer_org-shaped field since `parent_company` went in the same release.",
    }
  tags: null
  seniority:
    {
      path: JobGrade,
      type: array_of_object,
      note: "GS grade — a real seniority signal, unused",
    }
  salary: { path: PositionRemuneration, type: array_of_object }
  employment_type: { path: PositionSchedule, type: array_of_object }
  remote: { path: PositionLocationDisplay, type: derived }

traps:
  - "SILENTLY IGNORES unknown params — a typo'd parameter name is a permanent no-op with no error. Verify every param against a control."
  - "`DepartmentName` is an EMPLOYER ('Department of Veterans Affairs'), not a category. It rode in the deprecated `department` until 0.9.0 removed that field, and it is now UNMAPPED — this is the single accepted information loss of that release. `team` holds `SubAgency` (a facility) and `category` the OPM series; neither is the department."
  - "`JobCategory[].Name` is the real job function (OPM occupational series, with codes) and is currently unused."
  - "Public-domain federal data does NOT mean the API terms permit commercial use. Two separate questions."
---

## The cheapest supply win in the system

`search_usajobs` builds `?Keyword={query}` (`sources.py:1077`), so its coverage is decided entirely
by the harvest's keyword list — not by anything about the source. jobfitr's list is tech-shaped, so
its 3,737 rows are tech-shaped. Fifteen non-tech keywords measured today return **2,468 postings**
of exactly the roles the corpus lacks.

**That is a `web-harvest.yaml` edit.** No adapter, no key, no schema change, no release.
Occupational Therapist goes 0 → 69.

Curation matters more than volume: `warehouse` returned a retail sales associate. Pick occupational
titles, not nouns.

## A real record

Captured 2026-08-03, `Keyword=occupational therapist`. `QualificationSummary` and `UserArea`
truncated; everything else verbatim.

```json
{
  "PositionID": "CBSW-13023874-26-LD",
  "PositionTitle": "Occupational Therapist",
  "PositionURI": "https://www.usajobs.gov:443/job/878624800",
  "PositionLocationDisplay": "Palo Alto, California",
  "PositionLocation": [
    {
      "LocationName": "Palo Alto, California",
      "CountryCode": "United States",
      "CountrySubDivisionCode": "California",
      "CityName": "Palo Alto, California",
      "Longitude": -122.1608,
      "Latitude": 37.44466
    }
  ],
  "OrganizationName": "Veterans Health Administration",
  "DepartmentName": "Department of Veterans Affairs",
  "SubAgency": "VA Palo Alto Healthcare System",
  "JobCategory": [{ "Name": "Occupational Therapist", "Code": "0631" }],
  "JobGrade": [{ "Code": "GS" }],
  "PositionRemuneration": [
    {
      "MinimumRange": "102818",
      "MaximumRange": "193838",
      "RateIntervalCode": "PA",
      "Description": "Per Year"
    }
  ],
  "PublicationStartDate": "2026-07-30T11:01:01.6770",
  "ApplicationCloseDate": "2026-08-10T23:59:59.9970",
  "QualificationSummary": "…[truncated]"
}
```

**What this record proves.** Four fields we need are already here, correctly typed, and all four are
discarded:

| we want        | it already sends                                                   | the adapter uses                            |
| -------------- | ------------------------------------------------------------------ | ------------------------------------------- |
| `state`        | `PositionLocation[].CountrySubDivisionCode` = `"California"`       | the free-text `PositionLocationDisplay`     |
| `city`         | `PositionLocation[].CityName`                                      | —                                           |
| `function`     | `JobCategory[]` = `{Name: "Occupational Therapist", Code: "0631"}` | —                                           |
| `employer_org` | `DepartmentName`                                                   | UNMAPPED since 0.9.0 — see traps            |

`0631` is the **OPM occupational series** — a federal standard code for the occupation, which is a
better controlled vocabulary than anything any other source provides, and it is thrown away while an
employer name is used as the category.

Also note `OrganizationName` (`"Veterans Health Administration"`) is the truer employer than
`DepartmentName`, and `SubAgency` (`"VA Palo Alto Healthcare System"`) is the org unit — three
distinct nouns that the single `department` field used to flatten into one. Two of the three now
have homes (`company` and `team`); `DepartmentName` has none.

**Consequence:** structured location for this source is a mapping change, not a parsing project.
The data is already in the payload.

## Two fidelity fixes it is owed

1. **`DepartmentName` needs an `employer_org`-shaped home, and there is not one.** It must never go
   back into a category column — that routing was the single largest contributor to the downstream
   employer-as-category bug. But the field it rode in, `department`, was removed at 0.9.0, and
   `parent_company` — the only employer_org-shaped field the record ever had — was removed in the
   same release. So this is not a mapping change any more: it needs a field that does not exist.
   Until one does, the employing department is dropped, and that is the one accepted information
   loss of the 0.9.0 removal.
2. **`JobCategory[]` → `function`.** OPM occupational series is a genuine controlled vocabulary with
   codes — better than anything else any source provides, and currently thrown away.

## The rights nuance worth understanding

Federal job postings are US-government works and generally public domain. **The API terms bind
separately.** Public-domain status is an argument for obtaining the same content another way — the
National Labor Exchange carries state job bank + private employer postings under its own licence —
not a permission slip for this endpoint. Two different questions; do not let the first answer the
second.
