"""Job sources.

DEPTH  -- per-company ATS feeds (Greenhouse/Lever/Ashby/SmartRecruiters/Workable/
          Workday), polled for every company on the watchlist. All official public
          no-auth JSON endpoints.
BREADTH -- keyword aggregators + whole-board feeds searched across the whole
          market (Remotive/USAJOBS/Jobicy/Arbeitnow/RemoteOK/Himalayas/Adzuna/
          Google for Jobs/HN/Braintrust). All official public APIs.

Every source is a documented public API -- no scraping. (Scraper sources are an
opt-in extra, off by default; see the README.)
"""

from __future__ import annotations

import atexit
import html
import re
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from . import config
from . import iso3166
from . import vocab
from .vocab import (
    country_code,
    remote_type,
    salary,
    salary_period,
    split_place,
    us_state_code,
)
from .util import (
    NET_ERRORS,
    age_int,
    posted_from,
    clean_with_sections,
    get_json,
    post_json,
    q,
    salary_from_text,
    salary_range,
    to_date,
)


# ── DEPTH: per-company ATS feeds -- fetch_<ats>(slug) -> [posting] ───────────
# One definition, two users: the full fetch below (with bodies) and live_greenhouse
# (without them). Greenhouse also backs the board-ownership check in discover.
GREENHOUSE_API = "https://boards-api.greenhouse.io/v1/boards"


# An address inside a metadata VALUE, not a whole-value match: of 59 such values,
# 25 are embedded in a longer string. Deliberately narrow -- the dot-TLD requirement
# is what leaves 'Back@Work Physical Therapy' and '43000 Production@Pure' alone,
# verified against every string value in a 102,799-row harvest: 0 false positives.
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[A-Za-z]{2,}")


def _gh_metadata(md) -> dict:
    """Greenhouse `metadata[]` -> a flat name->value dict for `source_extra`.

    source_extra and NOT a core column, because the field names are chosen per board
    and share no vocabulary. Measured 2026-08-05: databricks sends 'Company
    Assignment' and 'Career Page Posting Category' on all 808 rows, anthropic sends
    'Location Type' on all 395, and stripe sends none at all on 547. Mapping
    'Company Assignment' to parent_company would work on exactly one board and put a
    different meaning in that column on the next one, which is the mistake
    `department` already made and 0.7.0 exists to undo.

    Multi-select values arrive as a list and are kept as one -- flattening to the
    first would silently drop the rest.
    """
    out = {}
    for m in md or []:
        if isinstance(m, dict) and m.get("name") and m.get("value") not in (None, ""):
            v = m["value"]
            # A PERSON IS NOT A JOB ATTRIBUTE. Greenhouse's user-type metadata carries
            # a named employee's work email and internal staff number: 1,280 rows of a
            # 102,799-row harvest, under 17 key names (Hiring Manager 365, Recruiter
            # 280, Job Approver 156), at veeam.com, datavant.com, nice.com, celonis.com,
            # x.ai, hasbro.com. The board publishes it, so nothing is breached -- but
            # re-publishing a third party's contact details under a consumer's name is
            # a decision, not a default. The name-vocabulary reasoning above is about
            # KEYS; this is about VALUES, which it never asked.
            #
            # TWO ENCODINGS, and the dict test alone misses the second. Clustering
            # every dict value in that harvest by key signature gives exactly four
            # shapes: salary {max_value,min_value,unit} 5,112 rows, referral
            # {amount,unit} 2,017, PEOPLE {email,employee_id,name,user_id} 1,739, and
            # a bare pay range {max_value,min_value} 36. So the person-key test drops
            # zero legitimate dicts -- but 57 MORE rows carry the email as a plain
            # STRING ('Hiring Manager' -> 'vinit.jadhav@careem.com'), same harm,
            # invisible to an isinstance check. 59 such values over those rows: 34 are
            # the whole value, 25 are embedded in a longer one -- so `search`, never
            # `fullmatch`. The embedded form is 42% of the string cases, not a corner.
            #
            # SCOPED CLAIM, deliberately: this removes work emails and internal staff
            # numbers. Bare personal NAMES still pass through ('Hiring Manager':
            # 'Pete Kern'). Filtering those needs a key list that does not also drop
            # 'Not Applicable' and other legitimate values under the same keys, and
            # a name with no contact detail is a different risk tier.
            if isinstance(v, dict) and {"email", "employee_id", "user_id"} & set(v):
                continue
            if isinstance(v, str) and _EMAIL_RE.search(v):
                continue
            out[str(m["name"])] = v
    return out


def fetch_greenhouse(slug: str):
    data = get_json(f"{GREENHOUSE_API}/{slug}/jobs?content=true")
    out = []
    for j in data.get("jobs", []):
        text, secs = clean_with_sections(j.get("content", ""))
        depts = j.get("departments") or []
        out.append(
            {
                "title": j.get("title", ""),
                # The board owner, as Greenhouse itself reports it, on a field that
                # was already in the response and was being thrown away. Measured
                # live 2026-08-24 over 104 boards: present and non-empty on 100% of
                # rows, and byte-identical to what `discover.board_owner`'s separate
                # `/v1/boards/{slug}` request returns on 103 of 103 boards that had a
                # row to compare -- so reading it here costs no extra request, and it
                # does not need `content=true` either.
                #
                # `or None` because "" must stay distinct from a real name: the
                # engine falls back to the watchlist entry on None, and this is a
                # nullable field precisely so an absent one is not a plausible guess.
                "company": j.get("company_name") or None,
                "location": (j.get("location") or {}).get("name", ""),
                "url": j.get("absolute_url", ""),
                # `first_published`, NOT `updated_at`. This is a correctness fix
                # wearing a mapping choice: `updated_at` is a last-EDIT stamp, and on
                # a real board it is not per-job information at all -- every one of
                # Anthropic's 395 postings reported an updated_at inside 30 days,
                # because the board gets touched in bulk. It carries no signal and
                # defeats every freshness gate downstream. Measured 2026-08-05:
                #
                #   anthropic   median age  1d (updated_at) vs  76d (first_published)
                #   databricks  median age 11d              vs 104d
                #
                # At the default max_age_days=60, databricks reported ALL 808 rows as
                # fresh when only 303 were -- 505 stale postings passing the filter,
                # and the stale_after_days score penalty never firing either.
                #
                # updated_at is kept in source_extra: "recently touched" is real
                # information, it is just not the post date.
                **posted_from(j.get("first_published") or j.get("updated_at")),
                "source_extra": {
                    "updated_at": to_date(j.get("updated_at")),
                    **_gh_metadata(j.get("metadata")),
                },
                # Present on every Greenhouse posting and NULL on all 397 of the
                # board measured 2026-08-05 -- the key exists, the data usually does
                # not. Mapped anyway: it costs nothing, other boards may fill it, and
                # to_date returns "" for a null so an absent deadline stays absent.
                "expires": to_date(j.get("application_deadline")),
                "team": (depts[0].get("name") if depts else None) or None,
                "employment_type": "",
                "salary": salary_from_text(text),
                "text": text,
                "sections": secs,
            }
        )
    return out


def _rt(is_remote, is_hybrid=None) -> str | None:
    """A source's remote/hybrid BOOLEANS -> the remote_type enum.

    Two booleans, three states: SmartRecruiters sends `remote` and `hybrid` side by
    side, and `remote=False, hybrid=True` is a real, common posting that a single
    bool reports as "not remote" -- indistinguishable from on-site.
    """
    if is_hybrid:
        return "hybrid"
    if is_remote is None:
        return None
    return "remote" if is_remote else "onsite"


def _lever_remote(workplace_type) -> dict:
    """Lever `workplaceType` -> {remote_type, remote_basis}.

    Lever states the work arrangement outright ("remote" / "hybrid" / "on-site"), so
    there is no need to infer it from prose. An unrecognised or absent value stays
    None -- unknown, NOT onsite -- and falls through to the text rule in the gate.
    """
    rt = remote_type(workplace_type)
    return {"remote_type": rt, "remote_basis": "stated" if rt else None}


def _lever_text(j: dict) -> tuple[str, list[dict] | None]:
    """The WHOLE posting body, which Lever splits across three fields, plus its sections.

    `descriptionPlain` is only the intro -- 1,118 chars on average (binance, n=295).
    The requirements and responsibilities live in `lists[]`, each a heading plus HTML
    (2,279 chars), and the closing sits in `additionalPlain` (712). Reading only the
    first field fed the scorer a third of each posting, so a role whose skills were
    all in the Requirements list scored as though it had none.

    HTML FIRST, PLAIN AS THE FALLBACK -- the same correction Ashby got in 0.9.0, one
    adapter over, and missed here. A header exists only in markup, so reading the
    `*Plain` variants meant this adapter could never produce a section: `sections: []`
    on 122 of 135 rows `[local 94-board harvest, 0.9.0]`. Worse, `lists[].text` IS the
    vendor's own section heading -- Lever hands us the structure in a labelled field --
    and appending it as bare prose threw that structure away and then failed to find it
    again. Wrapping it in a heading tag is not a heuristic; it is transcribing what the
    source said. Measured `[live api.lever.co, 4 boards, 489 postings, 2026-08-20]`:
    rows with no sections 471 -> 1, typed sections 1 -> 1,336, 0 unresolved spans.

    THE COST, because it is real: `text` changes on every Lever row. Median length
    7,286 -> 7,304 on palantir (HTML drops inline URLs and bullet markers the plain
    field spells out, the same trade Ashby took). Fit scores move on 7 of 489 postings,
    range -2..+3, mean -0.29 -- so 98.6% are byte-identical in score.

    `or`, not a bare swap: a body of "" is a legal value and nothing raises on it, so
    an employer who fills only the plain field would silently lose its whole posting.
    """
    parts = [j.get("description") or j.get("descriptionPlain") or ""]
    for sec in j.get("lists") or []:
        if isinstance(sec, dict):
            head = str(sec.get("text") or "").strip()
            if head:
                parts.append(f"<h3>{head}</h3>")
            parts.append(sec.get("content") or "")
    parts.append(j.get("additional") or j.get("additionalPlain") or "")
    body = "\n".join(str(x) for x in parts if x)
    # `sections: []` MEANS "we read a body and it had no headers", and that is a claim
    # about a body. With every Lever field empty there is no body to make it about:
    # `clean_with_sections("")` returns `("", [])`, `""` normalizes to `None` at the
    # engine boundary, and the row then asserted BOTH "no body" and "a body with no
    # headers" at once. 21 rows [102,799-row harvest, 2026-08-20], all Lever, because
    # it is the only adapter that BUILDS a body out of parts that can all be absent.
    #
    # Fixed here rather than in `clean_with_sections`, which must keep returning `[]`
    # for the 1,137 rows that genuinely have a body carrying no headers -- the two
    # cases are indistinguishable from inside that function, and only the caller knows
    # whether a body existed.
    if not body:
        return "", None
    return clean_with_sections(body)


def fetch_lever(slug: str):
    data = get_json(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    out = []
    for j in data:
        cats = j.get("categories") or {}
        text, secs = _lever_text(j)
        sr = j.get("salaryRange") or {}
        # PROBED 2026-08-05 on leverdemo: {min, max, currency, interval} where
        # interval is a vendor-specific string ("per-year-salary", "per-hour-wage")
        # that no generic period map would have guessed. Thin coverage -- 9 of 388
        # postings there, 0 of 295 on binance.
        pay = vocab.salary(
            sr.get("min"), sr.get("max"), sr.get("currency"), sr.get("interval")
        )
        if sr.get("min") and sr.get("max"):
            salary = (
                f"${int(sr['min']):,}–${int(sr['max']):,} {sr.get('currency', 'USD')}"
            )
        else:
            salary = j.get("salaryDescription") or salary_from_text(text)
        out.append(
            {
                "title": j.get("text", ""),
                "location": cats.get("location", ""),
                # TOP-LEVEL, not under `categories` -- `categories.country` does not
                # exist (0 of 295 on binance) while `country` is present on 295 of
                # 295, already alpha-2. Looking for it in the obvious place left the
                # column empty on nearly every Lever row.
                "country": country_code(j.get("country")),
                "url": j.get("hostedUrl", ""),
                **posted_from(j.get("createdAt")),
                "team": cats.get("team") or cats.get("department") or None,
                # `workplaceType` is a real Lever field ("remote"/"hybrid"/"onsite")
                # that this adapter never read -- remoteness was being re-derived from
                # the description while the source stated it outright.
                **_lever_remote(j.get("workplaceType")),
                "employment_type": cats.get("commitment", ""),
                **pay,
                "salary": salary,
                "text": text,
                "sections": secs,
            }
        )
    return out


def _ashby_place(address) -> dict:
    """Ashby `address.postalAddress` -> {city, state, country}. schema.org shape:
    addressLocality / addressRegion / addressCountry."""
    pa = (address or {}).get("postalAddress") if isinstance(address, dict) else None
    if not isinstance(pa, dict):
        return {"city": None, "state": None, "country": None}
    # TRIM AT THE BOUNDARY. The whitespace is the VENDOR's -- `addressRegion` arrives as
    # "California " on 11 of 1,730 live postings, from one board -- and it goes straight
    # into a column that gets GROUPED on, where " California" never meets "California".
    # US rows survive by luck because `us_state_code` strips internally; non-US rows
    # carry it all the way through.
    region = (pa.get("addressRegion") or "").strip() or None
    country_raw = (pa.get("addressCountry") or "").strip() or None

    # A REGION THAT IS THE COUNTRY REPEATED IS NOT A SUBDIVISION. Ashby lets a board
    # put anything in `addressRegion`, and 36 of 1,730 live postings put the country
    # there: ("UK","UK") 16, ("Australia","Australia") 7, ("Singapore","Singapore") 5.
    #
    # COMPARED AS RAW STRINGS, DELIBERATELY, and this is the whole design. The obvious
    # rule -- "drop a region that RESOLVES to the row's own country" -- destroys real
    # data: `England` resolves to GB through `_COUNTRY_CODES`, but England is a genuine
    # ISO 3166-2:GB subdivision (GB-ENG), and 27 of the 34 values that rule would have
    # deleted were exactly that. 39% collateral, measured. The alias exists in that map
    # for PROSE MATCHING ("we hire in England"), and borrowing it for data validation is
    # what makes the rule wrong. String equality catches 36 of the 43 bogus values and
    # touches none of the good ones; the 7 it misses are ("UK","United Kingdom"), the
    # same country spelled two ways, and they are left rather than reached for with a
    # normalizer that would re-catch England.
    if region is not None and country_raw is not None and region == country_raw:
        region = None
    return {
        "city": (pa.get("addressLocality") or "").strip() or None,
        "state": region,
        # Ashby sends a display name ("Singapore"); Lever sends alpha-2. One
        # normalizer so the column holds one vocabulary.
        "country": country_code(country_raw),
    }


def _ashby_salary(comp: dict) -> dict:
    """Ashby's structured compensation -> the five salary fields.

    `summaryComponents` is the pre-flattened list; the same figures also sit nested
    under compensationTiers[].components[], and reading the flat one avoids caring
    how many tiers a posting has.

    ONLY `compensationType == "Salary"`. Measured on openai (n=734 postings): the
    components are Salary 594, EquityCashValue 576, Commission 15 -- so taking the
    first component would have written an equity grant into salary_min on hundreds of
    rows, and equity carries minValue/maxValue of None anyway, which would have
    produced a salary that is neither stated nor honestly unknown.

    Every posting that has salary at all has exactly one Salary component with BOTH
    bounds present (594/594), so there is no partial-range case to invent a rule for.
    """
    for c in (comp or {}).get("summaryComponents") or []:
        if c.get("compensationType") != "Salary":
            continue
        got = salary(
            c.get("minValue"),
            c.get("maxValue"),
            c.get("currencyCode"),
            salary_period(c.get("interval")),
        )
        if got.get("salary_min") is not None or got.get("salary_max") is not None:
            return got
    # 140/734 postings carry no Salary component at all. All-None, not zero and not
    # a figure scraped from prose -- the `salary` display string above still carries
    # whatever the text says, and a consumer can tell the two apart by the basis.
    return salary()


def _ashby_locations(j: dict) -> list[dict] | None:
    """Ashby `secondaryLocations[]` + the primary address -> the `locations` list.

    ASHBY SHIPS A STRUCTURED PER-PLACE ARRAY AND THIS ADAPTER NEVER READ IT. Measured
    on a live probe of 8 boards (1,730 postings): 422 -- 24.4% -- carry a populated
    `secondaryLocations`, each entry an `{location, address.postalAddress}` pair with
    its own addressLocality / addressRegion / addressCountry. It arrives in a response
    already fetched, so reading it costs nothing.

    Without this, Ashby emitted no `locations` key at all and `engine._coerce` fell
    back to splitting the DISPLAY string -- which on this source is a single place, so
    a posting open in three offices reported one. Every other fix in this series parses
    a display string harder; this one stops parsing and reads the array.

    The primary address leads, because `location` is the posting's own headline place
    and the secondaries are explicitly secondary.

    NO PER-PLACE URL, and that is the vendor's doing rather than an omission here:
    the entries carry an address and nothing else. Entries used to carry the posting's
    own url on every one of them, which asserted a per-place apply link that no source
    publishes; the key was removed in 0.9.0 and the parameter with it.
    """

    def entry(raw: str, address) -> dict:
        place = _ashby_place(address)
        # CANONICALIZE HERE, because nothing downstream will. `engine._coerce` applies
        # the US-state-is-a-code rule to the SCALAR `state` only, and it builds
        # `locations[]` only when an adapter left it None -- so a list this adapter
        # supplies is never normalized. Without this, one row would carry state="CA" at
        # the top and state="California" in its own locations[0]: the same field, two
        # vocabularies, which is the exact defect this series just removed elsewhere.
        if place["country"] == "US" and place["state"]:
            place["state"] = us_state_code(place["state"]) or place["state"]
        return {"raw": raw, **place}

    entries: list[dict] = []
    seen: set[str] = set()
    primary = (j.get("location") or "").strip()
    if primary:
        entries.append(entry(primary, j.get("address")))
        seen.add(primary.lower())
    for sec in j.get("secondaryLocations") or []:
        if not isinstance(sec, dict):
            continue
        raw = (sec.get("location") or "").strip()
        if not raw or raw.lower() in seen:
            continue
        seen.add(raw.lower())
        entries.append(entry(raw, sec.get("address")))
    # ONE entry is not a list worth emitting -- `_coerce` builds the single-place shape
    # itself from the scalars, and returning None here keeps that one path.
    return entries if len(entries) > 1 else None


def fetch_ashby(slug: str):
    data = get_json(
        f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"
    )
    out = []
    for j in data.get("jobs", []):
        loc = j.get("location", "")
        # `workplaceType`, NOT `isRemote` -- the same reason `remote_type` reads it,
        # recorded in full at that assignment below: isRemote is TRUE ON EVERY HYBRID
        # ROW (measured on openai, n=733: isRemote=True with workplaceType='Hybrid'
        # on 442). So appending "(Remote)" from it labelled 7,435 hybrid postings as
        # remote in the one field a listing page renders -- `location: 'San Francisco
        # (Remote)'` beside `remote_type: 'hybrid'`. The classifier was corrected in
        # 0.9.0 and this string was not, which is two zones each individually right
        # and never checked against each other.
        #
        # This is NOT display-only. `remote_scope_raw` is a byte-copy of `location`,
        # and 775 of the affected rows currently parse a US boundary out of the
        # suffix, so `remote_areas` goes ['US'] -> None on them -- correctly: a hybrid
        # role in Menlo Park never stated a remote boundary. 6,225 rows also drop
        # 1-5 score points because `score_and_signals` scans `location` and "remote"
        # is a scored keyword. `dedup_key` does NOT move: `normalize_location` already
        # eats the word, verified identical on all 7,435.
        #
        # Nothing unique is discarded: across 7 live Ashby boards (1,392 postings),
        # isRemote=True with workplaceType ABSENT occurs 0 times.
        if str(j.get("workplaceType", "")).strip().lower() == "remote":
            loc = (loc + " (Remote)").strip()
        # HTML FIRST, and the fallback is the safety net -- not the other way round.
        # This read `descriptionPlain` until 0.9.0 and that produced `sections: []` on
        # 1,198 of 1,198 Ashby rows, the second-largest source, 15.8% of a harvest.
        # Headers live in markup and nowhere else, so plain text cannot yield one: the
        # feature was dead on this adapter the day it shipped. Measured [live probe
        # api.ashbyhq.com, 5 boards, 457 postings, 2026-08-20]: BOTH fields present on
        # 457/457, `descriptionHtml` -> 3,981 sections, `descriptionPlain` -> 0.
        #
        # README called this "a property of those employers, not of those adapters."
        # That was exactly backwards and is corrected there too.
        #
        # THE COST, because it is real and it is not free: Ashby renders a link as
        # `text https://url` in the plain field and as an `<a href>` in the HTML one,
        # and `clean` drops attributes -- so the body loses its inline URLs and its
        # bullet markers, 4,822 -> 4,663 mean characters (-3.3%) on the same 75
        # postings. That is the same body every other HTML source already produces
        # (Greenhouse loses its hrefs identically), so this makes Ashby consistent
        # rather than uniquely lossy -- but a consumer reading URLs out of `text`
        # loses them here.
        raw_desc = j.get("descriptionHtml") or j.get("descriptionPlain") or ""
        text, secs = clean_with_sections(raw_desc)
        comp = j.get("compensation") or {}
        salary = (comp.get("compensationTierSummary") or "").split("•")[0].strip()
        if not salary:
            tiers = comp.get("compensationTiers") or []
            if tiers:
                salary = tiers[0].get("title", "")
        out.append(
            {
                "title": j.get("title", ""),
                "location": loc,
                "url": j.get("jobUrl") or j.get("applyUrl", ""),
                **posted_from(
                    j.get("publishedAt") or j.get("updatedAt") or j.get("publishedDate")
                ),
                "team": j.get("department") or j.get("team") or None,
                # `workplaceType`, NOT `isRemote`. Measured on openai (n=733):
                #
                #   isRemote=True,  workplaceType='Hybrid'   442
                #   isRemote=None,  workplaceType=None       235
                #   isRemote=True,  workplaceType='Remote'    31
                #   isRemote=False, workplaceType='OnSite'    25
                #
                # `isRemote` is TRUE ON EVERY HYBRID ROW, so reading it reported 442
                # of 733 postings -- 60% of the board -- as fully remote. A boolean
                # cannot express hybrid, which is the entire reason remote_type is an
                # enum; wiring it to the one field that collapses the distinction
                # threw that away. `workplaceType` states it outright and
                # vocab.remote_type already maps all three values.
                #
                # The basis keys on the VALUE, not the key: `isRemote` is present on
                # 733/733 while null on 235, so keying on presence made 235 rows
                # claim a stated basis with remote_type=None.
                "remote_type": remote_type(j.get("workplaceType")),
                "remote_basis": "stated" if j.get("workplaceType") else None,
                **_ashby_place(j.get("address")),
                "locations": _ashby_locations(j),
                "employment_type": j.get("employmentType", ""),
                "salary": salary or salary_from_text(text),
                **_ashby_salary(comp),
                "text": text,
                "sections": secs,
            }
        )
    return out


# SmartRecruiters pages at 100 and CLAMPS SILENTLY. Measured 2026-08-04 on
# `boschgroup`: `?limit=200` returns 100 rows AND echoes `limit: 100` in the response
# -- no error, no warning. So the single `?limit=100` call this adapter used to make
# was taking 100 of 4,716 rows and reporting success: **97.9% of that board, gone.**
#
# The sharpest part is that the module already knew. `live_smartrecruiters` below
# returns `totalFound` (4,716) and that number feeds discovery's `-roles` sort, while
# this fetch returned 100. Two functions in one file, disagreeing by 46x. The LIVENESS
# comment even warns that "a capped or estimated number would silently reorder the
# review queue" -- the concern was applied to liveness and never to the fetch.
#
# `offset` paging works and has no depth ceiling (verified to offset=4700 -> 16 rows).
# Capped rather than exhaustive: Bosch alone would be 48 requests, and this runs
# per-company across a watchlist. 10 pages = 1,000 roles/company, a 10x improvement
# that stays bounded. Raise SMARTRECRUITERS_MAX_PAGES to widen it.
SMARTRECRUITERS_PAGE = 100  # the API's max; larger values are silently clamped


def fetch_smartrecruiters(slug: str):
    out: list[dict] = []
    for page in range(_depth("smartrecruiters_max_pages")):
        offset = page * SMARTRECRUITERS_PAGE
        try:
            data = get_json(
                f"https://api.smartrecruiters.com/v1/companies/{slug}/postings"
                f"?limit={SMARTRECRUITERS_PAGE}&offset={offset}"
            )
        except Exception:  # noqa: BLE001
            # Page 1 is different: with nothing collected there is no partial result
            # to salvage, and swallowing it would report a live board as empty. Same
            # discipline as fetch_workday's mid-walk guard -- and, like it, catching
            # bare Exception rather than only NET_ERRORS, so a parse fault on page 5
            # does not discard the 400 rows already in hand.
            if not out:
                raise
            break
        content = data.get("content", [])
        _smartrecruiters_rows(slug, content, out)
        if len(content) < SMARTRECRUITERS_PAGE:
            break  # short page -> the tail of the board
        total = data.get("totalFound")
        if isinstance(total, int) and offset + SMARTRECRUITERS_PAGE >= total:
            break  # the API told us where the end is; believe it
        time.sleep(0.2)  # be polite between pages of one board
    return out


def _smartrecruiters_rows(slug: str, content, out) -> None:
    """Map one page of SmartRecruiters postings into `out` (split out so the paging
    loop above reads as paging rather than as parsing)."""
    for j in content:
        loc = j.get("location") or {}
        parts = [loc.get("city", ""), loc.get("region", ""), loc.get("country", "")]
        loctext = ", ".join(p for p in parts if p)
        if loc.get("remote"):
            loctext = (loctext + " (Remote)").strip()
        out.append(
            {
                "title": j.get("name", ""),
                "location": loctext,
                "url": f"https://jobs.smartrecruiters.com/{slug}/{j.get('id', '')}",
                **posted_from(j.get("releasedDate") or j.get("createdOn")),
                # The most structured source in the set, and the adapter used almost
                # none of it: a real job family, a real org unit, a real seniority
                # string, fully structured geography, and an actual remote BOOLEAN --
                # all present on every posting, all previously collapsed into one
                # location string and one `department`.
                "category": (j.get("function") or {}).get("label") or None,
                "team": (j.get("department") or {}).get("label") or None,
                "seniority": (j.get("experienceLevel") or {}).get("label") or None,
                "city": loc.get("city") or None,
                "state": loc.get("region") or None,
                # country_code, not passthrough: SmartRecruiters sends LOWERCASE
                # alpha-2 ('de', 'us' -- 79 of 100 rows on boschgroup), so the column
                # held 'de' and 'DE' as different countries.
                "country": country_code(loc.get("country")),
                "remote_type": _rt(loc.get("remote"), loc.get("hybrid")),
                "remote_basis": "stated" if "remote" in loc else None,
                "employment_type": (j.get("typeOfEmployment") or {}).get("label", ""),
                # One-source fields: nobody else in the corpus sends an industry
                # taxonomy, a SEPARATE hybrid flag, or coordinates. Kept verbatim
                # rather than dropped, and out of the core rather than bloating it.
                "source_extra": {
                    k: v
                    for k, v in (
                        ("industry", (j.get("industry") or {}).get("label")),
                        ("hybrid", loc.get("hybrid")),
                        ("latitude", loc.get("latitude")),
                        ("longitude", loc.get("longitude")),
                        ("ref_number", j.get("refNumber")),
                    )
                    if v not in (None, "")
                },
                "salary": "",
                "text": "",
            }
        )


def fetch_workable(slug: str):
    data = get_json(
        f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true"
    )
    out = []
    for j in data.get("jobs", []):
        # THERE IS NO `location` KEY. This read `j.get("location") or {}` and built
        # the string from `.city/.region/.country` inside it, so `loctext` was ""
        # on EVERY row of every Workable board -- the only postings with any location
        # at all were remote ones, reading literally "(Remote)". Measured 28/28.
        # The real data is at the TOP LEVEL: city 26/28, state 26/28, country 28/28,
        # plus a `locations[]` list carrying an ISO-2 countryCode.
        places = [x for x in (j.get("locations") or []) if isinstance(x, dict)]
        first = places[0] if places else {}
        city = j.get("city") or first.get("city")
        state = j.get("state") or first.get("region")
        # `countryCode` FIRST. Workable's top-level `country` is a DISPLAY NAME
        # ('United States', 28/28 rows) while its own locations[] carries alpha-2 --
        # so one record asserted country='United States' and locations[0].country='US'
        # at the same time. country_code maps either onto the one vocabulary.
        country = country_code(
            first.get("countryCode") or j.get("country") or first.get("country")
        )
        loctext = ", ".join(p for p in (city, state, country) if p)
        if j.get("telecommuting"):
            loctext = (loctext + " (Remote)").strip()
        text, secs = clean_with_sections(j.get("description", ""))
        out.append(
            {
                "title": j.get("title", "") or j.get("full_title", ""),
                "location": loctext,
                "url": j.get("application_url")
                or j.get("url")
                or f"https://apply.workable.com/{slug}/j/{j.get('shortcode', '')}/",
                **posted_from(j.get("created_at") or j.get("published_on")),
                "team": j.get("department") or None,
                "city": city,
                "state": state,
                "country": country,
                "locations": [
                    {
                        "raw": ", ".join(
                            p
                            for p in (x.get("city"), x.get("region"), x.get("country"))
                            if p
                        ),
                        "city": x.get("city"),
                        "state": x.get("region"),
                        "country": x.get("countryCode") or x.get("country"),
                    }
                    for x in places
                ]
                or None,
                # `telecommuting` was already being read for the location STRING and
                # never for the enum -- the one-liner. False is a real answer here:
                # the key is on 28/28 rows, so an absent value is genuinely absent
                # rather than unknown.
                "remote_type": ("remote" if j.get("telecommuting") else "onsite")
                if "telecommuting" in j
                else None,
                "remote_basis": "stated" if "telecommuting" in j else None,
                "seniority": j.get("experience") or None,
                "category": j.get("function") or None,
                "source_extra": {
                    k: v
                    for k, v in (
                        ("industry", j.get("industry")),
                        ("education", j.get("education")),
                        ("shortcode", j.get("shortcode")),
                    )
                    if v
                }
                or None,
                "employment_type": j.get("employment_type", ""),
                "salary": salary_from_text(text),
                "text": text,
                "sections": secs,
            }
        )
    return out


# Workday's list endpoint pages 20 at a time (limit>20 is a hard HTTP 400), so a
# 400-role employer costs 20 calls. Cap it: 10 pages = 200 roles/employer, which
# bounds the LIST traffic across ~100 enterprise tenants at ~1k requests.
#
# That ~1k is the list lane only, and it is NOT the run's total: with details on
# (the default, below) each returned role costs one more request, so the same 100
# tenants cost ~1k list + up to ~20k detail calls. Read the two together before
# sizing a harvest window -- the detail pass dominates by an order of magnitude.
#
# The cap is silent by design and lossy: an employer with more than 200 open roles
# is TRUNCATED, not flagged. NVIDIA reports total=2000 and returns 200 here.
# Ordering is Workday's own (not newest-first), so the 200 you keep are not
# necessarily the 200 you want. Raise WORKDAY_MAX_PAGES to widen it.
# Raised from 10 to 25 (200 -> 500 roles/employer) ONLY because the detail pass is
# now gated (see fetch_workday's `keep`). The cap was never really a coverage
# decision -- it was standing in for a request budget, because every listed role cost
# a body whether or not it was wanted. With the gate in front, list pages are cheap
# and the budget is the gate, so the cap can move toward what the employer actually
# has. Accenture reports total=2000; at 25 pages we see 500 of them and pay for
# bodies only on the handful that pass the title filter.
WORKDAY_PAGE = 20
# Workday's LIST endpoint returns no description at all — those live on a per-job
# detail call, so bodies cost one request per role instead of one per twenty. Fetch
# them anyway: a body-less job is unrankable (jobfitr matches a user's boosts against
# title+body) and unreadable (the UI renders its snippet from the body), so 13k
# description-less jobs would be noise diluting the good results rather than coverage.
# This is the expensive half of a Workday harvest by an order of magnitude, so it is
# the first thing to turn off for a tight harvest window — but off is an escape
# hatch, not the normal state. Discovery does NOT pay this cost: sources.LIVENESS
# answers "is this board real" without touching the detail endpoint at all.
_ET = ZoneInfo("America/New_York")  # every date in job-radar is Eastern
_WD_POSTED = re.compile(r"Posting Date:\s*(\d{1,2})/(\d{1,2})/(\d{4})")
# A Workday requisition id as it appears in bulletFields: 'R00333425', 'R327553',
# 'R01169027'. Used to tell an id apart from a location in that heterogeneous list --
# matched on 59 of 60 rows across three tenants.
_WD_REQID = re.compile(r"[A-Za-z]{0,4}[-_]?\d{3,}")
_WD_RELATIVE = re.compile(r"Posted\s+(\d+)\+?\s+(day|week|month)s?\s+ago", re.I)
_WD_TODAY = re.compile(r"Posted\s+(today|yesterday)", re.I)


def posted_from_relative(text: str) -> dict:
    """A RELATIVE recency phrase -> `{posted, posted_basis}`, produced together.

    The sibling of util.posted_from, and the reason that one cannot simply be a
    boundary default: these two adapters compute a date from "Posted 26 Days Ago" or
    "3 days ago", and the result is indistinguishable from a real timestamp once it
    is a string. "30+ Days Ago" could be 30 days or 300.
    """
    d = _relative_posted(text)
    return {"posted": d, "posted_basis": "relative" if d else None}


def _relative_posted(text: str) -> str:
    """'Posted 26 Days Ago' -> an absolute YYYY-MM-DD (Eastern, like every date here)."""
    t = str(text or "")
    m = _WD_TODAY.search(t)
    if m:
        days = 0 if m.group(1).lower() == "today" else 1
        return (datetime.now(_ET) - timedelta(days=days)).strftime("%Y-%m-%d")
    m = _WD_RELATIVE.search(t)
    if not m:
        return ""
    n, unit = int(m.group(1)), m.group(2).lower()
    days = n * {"day": 1, "week": 7, "month": 30}[unit]
    return (datetime.now(_ET) - timedelta(days=days)).strftime("%Y-%m-%d")


# ── "is this a remote search, or a place search?" ───────────────────────────
# ONE predicate, three callers (adzuna x2, google_jobs). Every keyed search API
# here distinguishes a PLACE from the WORK ARRANGEMENT, and every one of them
# fails silently when you confuse the two: Adzuna resolves `where` against a place
# hierarchy and returns 0 rows for "remote"; Google treats it as a filter and
# ignores it as a location. Both look like "no such jobs" from the caller's side.
#
# This lived as a literal tuple inside search_google_jobs while search_adzuna had
# no equivalent at all, which is exactly how the two drifted.
_NON_PLACE = ("", "remote", "anywhere", "any")


# The seven codes that are BOTH an ISO country and a US state abbreviation. They are the
# reason a bare code is only half-accepted below, and the reason `_bare_code` exists at all
# rather than a one-line `in CODES` check.
_STATE_COLLIDING_CODES = frozenset({"AR", "CA", "CO", "DE", "ID", "IL", "IN"})
_ALPHA2 = frozenset(iso3166.NAME_TO_ALPHA2.values())
_SUBDIVISION_RE = re.compile(r"^([A-Z]{2})-([A-Z0-9]{1,3})$")


def _bare_code(name: str) -> str | None:
    """A vendor's own ISO CODE -> that code, where reading it cannot be wrong.

    `iso3166.alpha2` maps NAMES, so "United States" resolved while the literal "US" -- the
    very string this package stores -- did not. That asymmetry is the gap this closes.

    IT CLOSES ONLY THE HALF THAT IS SAFE. Seven codes are both a country and a US state
    abbreviation (AR CA CO DE ID IL IN), and the ambiguity is not hypothetical: on the PROSE
    path `vocab.remote_scope("Remote - CA")` returns `US-CA`, California. Accepting "CA" as
    Canada here would make two characters mean two different places on two paths of one
    record contract, and a wrong country in `remote_areas` admits a posting into a filter
    that excludes it -- the one direction this contract must never be wrong in. So those
    seven stay unresolved, with the vendor's string preserved in `remote_scope_raw`, and the
    canary's unmapped-token gate is what would surface them if a vendor ever sent one.

    SUBDIVISIONS ARE DIFFERENT AND DO RESOLVE. "US-TX" is unambiguous, `remote_scope` already
    EMITS that form, `REMOTE_AREA_RE` already blesses it and `_region_allowed` already
    resolves it -- so accepting it makes the two paths agree rather than diverge. The suffix
    is deliberately not validated against a subdivision list: under the `startswith` rule in
    `_region_allowed` a bogus "US-XX" can only ever narrow a match, never broaden one, which
    is the safe direction to be wrong in.

    MEASURED, AND WORTH SAYING PLAINLY: across 136 live rows from all three vendors that
    populate this field, ZERO sent a bare code. This is a contract-consistency fix, not a
    response to observed data.
    """
    v = name.strip().upper()
    if m := _SUBDIVISION_RE.match(v):
        return v if m.group(1) in _ALPHA2 else None
    if v in _ALPHA2 and v not in _STATE_COLLIDING_CODES:
        return v
    return None


def stated_scope(values, raw: str | None = None) -> dict:
    """A vendor's STATED eligibility list -> the three boundary fields.

    Several sources send this as a real array (himalayas `locationRestrictions`) or as a
    comma-joined string (jobicy `jobGeo`, remotive `candidate_required_location`). It is
    where a candidate may LIVE, not where the job is -- reading it as a workplace mis-files
    every row, which the catalog profiles say in as many words.

    AN EMPTY LIST IS NOT A MISSING ONE. himalayas' own API doc: "An empty array [] means the
    job is open worldwide with no geographic restrictions." The adapter used to write
    `", ".join(...) or None`, which turned that into "we don't know" on 29 measured rows and
    threw away the most permissive rows in the feed -- the inverse of the error this
    package's contract exists to prevent. `[]` survives as `[]`.

    THE JOIN IS KEPT, but only for `remote_scope_raw`, which is display. Using a joined
    string as DATA is what broke: ISO country names contain commas -- "Congo, The Democratic
    Republic of the", "Micronesia, Federated States of" are both real -- so re-splitting on
    ", " produced fragments like "Federated States of" as if they were countries. Nothing
    re-splits it now; the codes come from the list.

    Names resolve through `iso3166`, the generated ISO table, NOT through vocab's 62-name
    prose map -- these are structured vendor fields where the string is known to be a
    country, which is exactly the case that table exists for.
    """
    if values is None:
        return {"remote_areas": None, "remote_regions": None, "remote_scope_raw": raw}
    # ANYTHING THE VENDOR ACTUALLY SENT, not just the shapes we hoped for. This ran
    # `isinstance(values, str)` and then iterated, so a JSON number or boolean in this field
    # raised `TypeError: 'int' object is not iterable` and killed the whole harvest on the
    # first row carrying one -- the exact failure `engine._coerce` exists to prevent, except
    # this function runs INSIDE the adapter, upstream of it. Errors are values in this
    # package. A number is not a place, so it coerces to text, resolves to nothing, and the
    # record says the boundary is unknown while keeping the vendor's own value in
    # `remote_scope_raw`. Wrong-looking data survives as evidence; it does not stop a run.
    # Wrapped as a ONE-ITEM LIST, not handed to the string branch: that branch runs a
    # whole-string ISO lookup and a comma split, both meaningless for a number, and `0`
    # is falsy in a way the emptiness checks below would misread.
    if not isinstance(values, (str, list, tuple, set)):
        values = [str(values)]
    if isinstance(values, str):
        # A BLANK string is not an empty array. himalayas' `[]` means "open worldwide";
        # remotive and jobicy send a plain string, and "" from them means the vendor said
        # nothing. Collapsing the two would assert a posting is open to the world because a
        # field happened to be blank.
        if not values.strip():
            return {
                "remote_areas": None,
                "remote_regions": None,
                "remote_scope_raw": raw,
            }
        # WHOLE-STRING FIRST, then split. 15 ISO names contain a comma, and one of them
        # re-splits into a different real country: "Congo, The Democratic Republic of the"
        # (CD) becomes "Congo" (CG, Republic of the Congo) plus a fragment. That is a
        # well-formed code naming the wrong country, which is worse than no code.
        # VERBATIM MEANS VERBATIM. `remote_scope_raw` is the vendor's own words, kept so a
        # consumer that disagrees with our parse can re-read the original -- so it is the
        # string as sent, not a rejoin of the normalized parts. Without this, whitespace
        # normalization would quietly rewrite the evidence the field exists to preserve.
        raw = values if raw is None else raw
        whole = iso3166.alpha2(values)
        values = [values] if whole else [v.strip() for v in values.split(",")]
    # NORMALIZED ONCE, HERE, so every use below reads the clean value. Internal whitespace
    # is collapsed because the country lookup already does it (`iso3166.alpha2` splits and
    # rejoins) while the region lookup did not, so `"North  America"` with a double space
    # resolved to nothing while `" Germany "` resolved fine -- two vocabularies disagreeing
    # about whitespace in one function. Stripping here also stops `remote_scope_raw` from
    # echoing back `" Germany , France"`, which is a reconstruction artifact, not what any
    # vendor sent.
    names = [" ".join(v.split()) for v in values if isinstance(v, str) and v.strip()]
    # A list that HAD members but none usable is malformed input, not a declaration. Only a
    # genuinely empty list carries himalayas' "open worldwide" meaning; `["", None]` is a
    # vendor sending junk, and reading it as unbounded would assert the most permissive
    # possible value from the least information.
    if values and not names:
        return {"remote_areas": None, "remote_regions": None, "remote_scope_raw": raw}

    # "WORLDWIDE" IS A STATEMENT, NOT A SILENCE. himalayas says it with an empty array and
    # the branch above already honours that; remotive and jobicy say it with the WORD, and
    # this function did not know it -- 6 of 18 live remotive rows came back unstated with a
    # raw of "Worldwide". That is the same bug the empty array had: a vendor declaring no
    # geographic restriction, recorded as "we don't know", which then gets admitted or
    # dropped for the wrong reason. `vocab._REMOTE_ANYWHERE` already recognised the word on
    # the LOCATION path; the adapter path is where it was missing. Found only by probing a
    # live endpoint -- no fixture had it, because I wrote the fixtures.
    # WHOLE NAME, not a substring. `_REMOTE_ANYWHERE` is a word-boundary SEARCH, so the
    # first version of this guard read "Anywhere in the US", "Anywhere in Europe" and
    # "Worldwide except China" as unbounded -- a BOUNDED posting asserting it is open to
    # the world. Through scoring._region_allowed an empty list satisfies every policy, so
    # "Anywhere in the US" would have been admitted into a Germany-only filter. That is the
    # one direction this contract exists to never be wrong in, and the fix for one bug
    # introduced it.
    if names and all(vocab._REMOTE_ANYWHERE.fullmatch(n) for n in names):
        return {
            "remote_areas": [],
            "remote_regions": None,
            "remote_scope_raw": raw if raw is not None else ", ".join(names),
        }

    areas, regions, unmapped = set(), set(), False
    for n in names:
        code = iso3166.alpha2(n) or _bare_code(n)
        if code:
            areas.add(code)
        elif n.upper() in vocab.REMOTE_REGION_TOKENS:
            regions.add(n.upper())
        else:
            unmapped = True
    # `[]` only when the vendor genuinely sent an empty list. A non-empty list whose members
    # we could not map is UNSTATED, never "worldwide" -- that distinction is the whole point
    # of the field, and asserting unbounded because a lookup failed would be the worst
    # possible direction to be wrong in.
    if names and not areas:
        areas_out = None
    else:
        areas_out = sorted(areas)
    if unmapped and not areas and not regions:
        areas_out = None
    return {
        "remote_areas": areas_out,
        "remote_regions": sorted(regions) or None,
        "remote_scope_raw": raw if raw is not None else (", ".join(names) or None),
    }


def _is_remote_query(cfg) -> bool:
    """True when the configured location names a work ARRANGEMENT, not a place."""
    return cfg.location.strip().lower() in _NON_PLACE


# Himalayas paging. The adapter sent `limit=20` and no page parameter at all, so it
# took the first 20 rows per query out of a measured 8,020 reachable (401 pages x 20)
# -- an under-fetch of roughly 60x on the largest keyless breadth source in the set.
#
# The trap that made this easy to miss, from catalog/himalayas.md: this source has TWO
# endpoints with DIFFERENT pagination models. `/jobs/api` (browse) takes `offset`;
# `/jobs/api/search` takes `page`. Sending `offset` to the search endpoint is silently
# ignored and you get page 1 forever -- which is what the catalog's own first probe
# did, so `max_page` was briefly recorded as unknown for that reason rather than a
# measured ceiling.
#
# Capped rather than exhaustive: 401 pages x N title queries is a lot of requests for
# a board whose rows the relevance gate will mostly drop. 10 pages = 200 rows/query,
# a 10x improvement that stays polite. Raise HIMALAYAS_MAX_PAGES to widen it.
HIMALAYAS_PAGE = 20  # the API's own page size on this endpoint
# The browse lane's budget. THIS is what bounds it -- 50 pages x 20 = 1,000 of the
# ~97,000 rows in the corpus. An earlier version of this comment said freshness was
# the budget and the cap was only a backstop; that was wrong, and the arithmetic says
# so: a 60-day row (the default max_age_days) sits near offset 130,000, which this cap
# cannot reach, so the age stop in _himalayas_browse is a secondary guard that only
# fires on a short window. Worth having anyway because browse is date-ordered: these
# are the NEWEST 1,000, not an arbitrary slice.


# ── GOOGLE FOR JOBS (SerpApi) helpers ───────────────────────────────────────
# Google reports recency as a relative string ('16 hours ago', '3 days ago',
# '30+ days ago', 'today') — the same rot-in-the-cache trap as Workday's postedOn,
# so resolve it to an absolute Eastern date at fetch time.
_G_POSTED = re.compile(r"(\d+)\+?\s*(second|minute|hour|day|week|month)s?\s+ago", re.I)
_G_UNIT_DAYS = {"second": 0, "minute": 0, "hour": 0, "day": 1, "week": 7, "month": 30}

# Apply-route providers that are AGGREGATORS, not the employer. Google returns an
# ordered `apply_options`; we prefer the direct-to-company / ATS link (Workday,
# Greenhouse, a careers page) over these, because a direct link is jobfitr's whole
# product promise. Matched as a substring of the apply link's host.
_G_AGGREGATORS = (
    "linkedin.",
    "indeed.",
    "ziprecruiter.",
    "glassdoor.",
    "monster.",
    "bebee.",
    "jobleads.",
    "theladders.",
    "lensa.",
    "careerbuilder.",
    "talent.com",
    "jobrapido.",
    "bandana.",
    "snagajob.",
    "simplyhired.",
    "adzuna.",
    "jooble.",
    "trabajo.",
)


# THE ALLOWLIST, and the direction matters more than the contents.
#
# `direct_apply` used to be `not _is_aggregator(host)` against the hand-maintained
# blocklist above, which means an UNKNOWN host counted as direct-to-employer.
# Measured on 20 live Google results: 17 came back True, of which exactly ONE
# actually was. The other sixteen were dice.com, jobgether.com, jobilize.com,
# jobright.ai, recruit.net, kyjobs.usnlx.com, trabajos.univision.com, jobs.fox8.com,
# talents.vaia.com, vacancyglobalpro.up.railway.app -- and "trabajo." misses
# "trabajos.univision.com" by one character.
#
# A blocklist can never be complete: job-board hosts are effectively unbounded and
# new ones appear constantly, so every gap defaults to a WRONG "yes" on the field
# this product is built on. An allowlist inverts the failure: an unrecognised host
# is reported as not-direct, which understates rather than overstates. Recognising a
# new ATS is a one-line addition; recognising every aggregator on earth is not a
# finishable task.
def _depth(name: str):
    """One harvest-depth ceiling, read from config AT CALL TIME.

    Call time, not import time, and that is the whole point of the change. These were
    nine module-level constants each reading its own environment variable as the
    module loaded -- so a YAML config, which is parsed later, could never set any of
    them. Tuning a harvest meant knowing nine undocumented variable names. They now
    live in one named block (`config.HarvestDepth`, YAML `sources.harvest_depth.*`)
    that still takes those same env vars as its defaults, so nothing that worked
    before stopped working.
    """
    return getattr(config.active().harvest_depth, name)


_ATS_HOSTS = (
    "greenhouse.io",
    "lever.co",
    "ashbyhq.com",
    "myworkdayjobs.com",
    "smartrecruiters.com",
    "workable.com",
    "rippling.com",
    "teamtailor.com",
    "icims.com",
    "taleo.net",
    "successfactors.com",
    "bamboohr.com",
    "jobvite.com",
    "breezy.hr",
    "recruitee.com",
    "personio.de",
    "usajobs.gov",
    "paylocity.com",
    "ultipro.com",
    "adp.com",
    "oraclecloud.com",
    # ADD THESE BEFORE TIGHTENING THE EMPLOYER-DOMAIN BRANCH BELOW, NOT AFTER. All five
    # rows they cover were already being reported direct -- by ACCIDENT, through the old
    # substring test matching the company name in an ATS SUBDOMAIN
    # (`elucid.applytojob.com` contains `elucid`). Naming the platforms moves them onto
    # the intentional branch. Reversed, the tightening lands first, silently drops
    # 6 measured rows, and every gate stays green while it happens.
    "applytojob.com",  # JazzHR
    "welcomekit.co",
    "careers-page.com",
    # `personio.de` is listed above and does NOT match `friendlycaptcha.jobs.personio.com`.
    # The same vendor under a second TLD -- the gap that survives an audit precisely
    # because a reader scanning this tuple sees "Personio" and ticks it off.
    "personio.com",
)


def _google_posted(text: str) -> str:
    """'16 hours ago' / '3 days ago' / '30+ days ago' / 'today' -> YYYY-MM-DD (ET).

    Sub-day units ('hours'/'minutes' ago) resolve to today; 'today'/'just now' to
    today, 'yesterday' to yesterday. An unparseable string returns '' (a blank date
    sinks the role in the freshness filter, which is the safe default for unknown)."""
    t = str(text or "").strip().lower()
    if not t:
        return ""
    if "today" in t or "just now" in t or "just posted" in t:
        return datetime.now(_ET).strftime("%Y-%m-%d")
    if "yesterday" in t:
        return (datetime.now(_ET) - timedelta(days=1)).strftime("%Y-%m-%d")
    m = _G_POSTED.search(t)
    if not m:
        return ""
    n, unit = int(m.group(1)), m.group(2).lower()
    days = n * _G_UNIT_DAYS[unit]
    return (datetime.now(_ET) - timedelta(days=days)).strftime("%Y-%m-%d")


def _is_direct_apply(url: str, company: str = "") -> bool:
    """Can an application actually be completed at this URL, at the employer?

    POSITIVE evidence only, and that is the whole point -- see the _ATS_HOSTS
    comment. Two things count: a known applicant-tracking system, or the employer's
    own domain (a careers page). Anything else is reported as not-direct, including
    hosts nobody has classified yet.
    """
    host = urlparse(url or "").netloc.lower()
    if not host:
        return False
    if any(ats in host for ats in _ATS_HOSTS):
        return True
    # The employer's own domain: careers.acmehealth.com IS Acme Health Inc applying
    # to itself. `_norm_name` is reused rather than re-rolled because it already
    # drops the legal suffix -- without that, "Acme Health Inc" -> "acmehealthinc"
    # fails to match "acmehealth" and a real careers page reads as an aggregator.
    # Its docstring names keeping one definition as the point.
    from .discover import _norm_name

    token = _norm_name(company).replace(" ", "")
    # THE REGISTRABLE DOMAIN, not any substring of the host. `bitpay.applytojob.com`
    # matched on `bitpay` and read as BitPay's own careers page -- the company name was
    # the ATS's SUBDOMAIN, and a substring test cannot tell those apart. 6 rows measured;
    # every one is a real ATS platform, so they were right BY ACCIDENT rather than by
    # principle: the identical rule promotes `nike.some-aggregator.com`. No such row
    # exists in the corpus, so the hole was latent, not demonstrated -- and it is closed
    # here rather than after something lands in it.
    #
    # The four platforms are on _ATS_HOSTS above BECAUSE of this line. Adding them there
    # is not a separate improvement; it is the half of this change that keeps it free.
    #
    # KNOWN RESIDUAL, and do not "fix" it by allowlisting: shared hosting where the
    # SUBDOMAIN is the owner -- github.io, netlify.app, vercel.app, pages.dev -- is
    # invisible to this. `joulent.github.io` is one row in 7,545 and stays unrecognised.
    # Allowlisting github.io would certify every project page on GitHub as a direct
    # apply, which is a far worse trade than one missed employer.
    labels = host.split(".")
    registrable = ".".join(labels[-2:]) if len(labels) >= 2 else host
    # 5 chars, because short tokens produce nonsense matches -- a company called
    # "Ace" would claim every host containing "ace", which is most of them.
    return len(token) >= 5 and token in re.sub(r"[^a-z0-9]", "", registrable)


def _is_aggregator(url: str) -> bool:
    """A KNOWN aggregator. Narrower than "not direct" -- used only to demote a link
    when choosing between several, never to certify one as direct."""
    host = urlparse(url or "").netloc.lower()
    return bool(host) and any(agg in host for agg in _G_AGGREGATORS)


def _best_apply_link(apply_options: list, fallback: str = "", company: str = "") -> str:
    """Pick the best apply link from Google's `apply_options`.

    Preference order, strongest first: a link we can POSITIVELY identify as reaching
    the employer, then anything not on the known-aggregator list, then whatever
    Google ranked first. The middle tier exists because an unclassified host is
    genuinely better than a known aggregator even though we cannot certify it.
    """
    links = [o.get("link", "") for o in (apply_options or []) if o.get("link")]
    for link in links:
        if _is_direct_apply(link, company):
            return link
    for link in links:
        if not _is_aggregator(link):
            return link
    return links[0] if links else fallback


def _serpapi_searches_left(key: str) -> int | None:
    """Searches remaining on the SerpApi plan, or None if it cannot be determined.

    `/account.json` is FREE -- it does not consume a search (verified: usage stayed
    put across calls) -- which is what makes checking before every run affordable.

    None, not 0, when the check fails: an unreachable account endpoint says nothing
    about the quota, and treating it as empty would disable the adapter on a network
    blip. The caller falls back to the per-run cap, which bounds the damage either way.
    """
    try:
        d = get_json(f"https://serpapi.com/account.json?api_key={q(key)}")
    except Exception:  # noqa: BLE001 -- a quota check must never sink the harvest
        return None
    left = d.get("plan_searches_left")
    if left is None:
        left = d.get("total_searches_left")
    return int(left) if isinstance(left, (int, float)) else None


def _serpapi_budget(cfg, key: str, planned: int) -> int:
    """How many SerpApi searches this run may actually spend.

    Never silently caps: when the budget is below what was planned, it says what was
    dropped and why. A quiet trim here would read as "the market was quiet" -- the
    exact ambiguity the run manifest exists to remove.
    """
    budget = min(planned, max(0, cfg.serpapi_max_searches_per_run))
    left = _serpapi_searches_left(key)
    if left is not None:
        spendable = max(0, left - max(0, cfg.serpapi_reserve))
        if spendable < budget:
            print(
                f"  google_jobs: {left} searches left on the plan, holding "
                f"{cfg.serpapi_reserve} in reserve -- spending {spendable} of "
                f"{planned} planned"
            )
            return spendable
    if budget < planned:
        print(
            f"  google_jobs: capped at {budget} of {planned} planned searches "
            "(serpapi_max_searches_per_run)"
        )
    return budget


def search_google_jobs(queries):
    """Google for Jobs via SerpApi — keyed, queryable by title + location, exactly
    like search_adzuna/search_usajobs. Reaches the company-careers + enterprise-ATS
    roles (Workday, iCIMS) Google indexes that the ATS-specific adapters never see,
    WITHOUT any per-tenant polling.

    Metered: each page is one SerpApi search (free tier 250/mo), and Google returns
    ~10 roles/page, so google_jobs_pages defaults to 1. The fit score stays
    source-agnostic (engine scores by content); Google's edge is realized as the
    preferred canonical apply link on dedup, not as a score bonus."""
    cfg = config.active()
    key = cfg.env(cfg.serpapi_key_env)
    if not key:
        print(
            "  google_jobs: no SERPAPI_KEY set -- skipped (the free sources still run)"
        )
        return []
    # Google for Jobs treats 'remote' as a FILTER, not a place. This dropped the word
    # and then set no filter at all, so a remote search silently became an unfiltered
    # nationwide one -- and because it still returned rows, nothing looked broken.
    # `ltype=1` is SerpApi's documented work-from-home filter; it is the other half of
    # the sentence the comment here has always started.
    remote_query = _is_remote_query(cfg)
    where = "" if remote_query else cfg.location.strip()
    pages = max(1, getattr(cfg, "google_jobs_pages", 1))
    # EVERY page is one metered search, so the spend is decided before the first
    # request rather than discovered when SerpApi starts refusing.
    budget = _serpapi_budget(cfg, key, len(queries) * pages)
    if budget <= 0:
        print("  google_jobs: no SerpApi quota available this run -- skipped")
        return []
    spent = 0
    out = []
    for qy in queries:
        if spent >= budget:
            print(
                f"  google_jobs: budget spent after {spent} searches -- "
                f"{len(queries) - queries.index(qy)} queries not run this time"
            )
            break
        token = ""
        for _ in range(pages):
            if spent >= budget:
                break
            spent += 1
            url = (
                f"https://serpapi.com/search.json?engine=google_jobs"
                f"&q={q(qy)}&api_key={key}&hl=en"
            )
            if where:
                url += f"&location={q(where)}"
            else:
                url += "&ltype=1"  # work-from-home filter (SerpApi, documented)
            if token:
                url += f"&next_page_token={q(token)}"
            try:
                data = get_json(url)
            except NET_ERRORS:
                break  # a dead page ends this query; other queries still run
            if data.get("error"):
                # SerpApi reports quota-exhausted / bad-key as a JSON `error`, not an
                # HTTP error. Surface it once and stop — retrying burns nothing useful.
                print(f"  google_jobs: {data['error']}")
                break
            for j in data.get("jobs_results", []) or []:
                ext = j.get("detected_extensions") or {}
                text, secs = clean_with_sections(j.get("description", ""))
                out.append(
                    {
                        "title": j.get("title", ""),
                        "company": j.get("company_name", ""),
                        "location": j.get("location", ""),
                        # GOOGLE'S WORD, KEPT AS EVIDENCE AND NOT AS A BOUNDARY. Setting
                        # this is what tells `engine.derive_remote` the raw is already
                        # accounted for, so it does not parse the string into
                        # `remote_areas` -- see the "whoever supplied the raw owns the
                        # parse" comment there. Under `&ltype=1` this field is the SEARCH
                        # MODE, not the posting's scope: it is the constant `Anywhere` on
                        # every work-from-home result (43 of 43 locally) and a real city on
                        # the rest, so it varies with the QUERY and not with the row. 11 of
                        # those 43 state a US-only bound in their own title, body or URL,
                        # and `[]` satisfies every `allowed_scopes` policy unconditionally.
                        # Google exposes no eligibility field, so the honest boundary is
                        # unstated.
                        "remote_scope_raw": j.get("location") or None,
                        "url": (
                            _url := _best_apply_link(
                                j.get("apply_options"),
                                j.get("share_link", ""),
                                j.get("company_name", ""),
                            )
                        ),
                        # POSITIVE identification, not "absence from a blocklist".
                        # The old form was `not _is_aggregator(url)`, which reported
                        # 17 of 20 live rows as direct when exactly ONE was -- every
                        # unclassified host counted as a yes.
                        "direct_apply": _is_direct_apply(
                            _url, j.get("company_name", "")
                        ),
                        # Google's own name for whoever hosts the apply link
                        # ("LinkedIn", "Dice", "BeBee") -- the same judgement stated
                        # by the vendor, kept so a consumer can audit ours.
                        "source_extra": {
                            k: v
                            for k, v in (
                                ("via", j.get("via")),
                                ("job_id", j.get("job_id")),
                                ("source_link", j.get("source_link")),
                            )
                            if v
                        }
                        or None,
                        # ALWAYS relative -- Google states recency as "2 days ago",
                        # never a date, so the value is arithmetic done at fetch time
                        # and says so.
                        "posted": (_gp := _google_posted(ext.get("posted_at", ""))),
                        "posted_basis": "relative" if _gp else None,
                        # `work_from_home` is a real boolean on the extension, and it
                        # is the ONLY structured remote signal Google gives. Verified
                        # 2026-08-05: with &ltype=1 it is true on 10 of 10 results,
                        # without it 0 of 10.
                        **(
                            {"remote_type": "remote", "remote_basis": "stated"}
                            if ext.get("work_from_home")
                            else {}
                        ),
                        "employment_type": ext.get("schedule_type", ""),
                        # Google's salary carries its PERIOD in the string --
                        # "47-55 an hour", "2,140 a week" -- and is frequently NOT
                        # annual. Parsed rather than assumed; see vocab.google_salary.
                        "salary": ext.get("salary") or salary_from_text(text),
                        **vocab.google_salary(ext.get("salary")),
                        "text": text,
                        "sections": secs,
                        "source": "google_jobs",
                    }
                )
            token = (data.get("serpapi_pagination") or {}).get("next_page_token", "")
            if not token:
                break  # no more pages for this query
            time.sleep(0.5)  # be polite between pages of the same query
    return out


def _title_of(row: dict) -> str:
    """The row's title, GUARANTEED to be a str.

    `keep` is the caller's relevance gate, and it runs here -- one layer UPSTREAM of
    engine._coerce, which is what used to make every title safe before anything called
    `.lower()` on it. A vendor `null` title therefore reached `scoring.relevant` raw and
    raised AttributeError, and because a depth adapter's exception is caught per-COMPANY
    in engine._fetch_company, one malformed posting cost the whole employer -- the good
    roles on that board included.

    That is the exact failure engine._TEXT_FIELDS was written to prevent (see its
    comment: "one malformed posting killed the whole run"). Moving the gate earlier for
    the request saving moved it past that guard, so the guard comes along.

    Deliberately here rather than in engine._fetch_company: the adapter owns the raw
    vendor data, and fixing only the engine would leave the same landmine armed for any
    direct caller that passes its own `keep`.
    """
    t = row.get("title")
    return t if isinstance(t, str) else ("" if t is None else str(t))


def fetch_workday(slug: str, host: str = "wd1", site: str = "", keep=None):
    """Workday CxS job feed. Unlike every other ATS here, Workday needs a THREE-part
    key: tenant (`slug`), the numbered host shard (`wd1`..`wd103`), and the site slug
    — `nvidia`+`wd5`+`NVIDIAExternalCareerSite`. The site slug is unguessable, which
    is why the watchlist stores all three (discovery: `job_radar.discover`).

    Reaches the enterprise/government/healthcare employers the startup ATSs never
    see.

    Descriptions ARE fetched, by default. They do not exist on the list endpoint, so
    each role costs one additional detail request (WORKDAY_FETCH_DETAILS=0 turns that
    off) -- and that detail pass is the single most expensive thing in a harvest.

    `keep(title) -> bool` is what makes the cost sane. The list endpoint returns the
    title; the caller's relevance gate reads only the title; so the gate can run
    BEFORE the bodies are bought instead of after. The engine passes
    `scoring.relevant` (see engine._fetch_company). Measured across the ten shipped
    Workday employers:

        cap 200, bodies for all      1,663 requests -> 1,583 roles (23% of 6,922)
        uncapped, bodies for all     7,272 requests -> 6,922 roles
        uncapped, bodies after keep    903 requests -> 6,922 roles

    i.e. every role, for roughly half the requests the truncated version costs today.
    That is why the page cap could be raised: the cap was standing in for a request
    budget, and `keep` is the thing that actually bounds it.

    `keep=None` preserves the old behaviour exactly (fetch every body), so a direct
    caller that does not filter is unaffected.

    Returns at most workday_max_pages x WORKDAY_PAGE roles, silently truncated -- see
    the cap comment above.
    """
    base = f"https://{slug}.{host}.myworkdayjobs.com/wday/cxs/{slug}/{site}"
    out: list[dict] = []
    offset, total = 0, None
    for _ in range(_depth("workday_max_pages")):
        try:
            data = post_json(
                f"{base}/jobs",
                {
                    "appliedFacets": {},
                    "limit": WORKDAY_PAGE,
                    "offset": offset,
                    "searchText": "",
                },
            )
        except Exception:  # noqa: BLE001
            # A page that fails mid-walk must not discard the pages that already
            # succeeded -- the same best-effort discipline _workday_add_details
            # uses one function below. Keep what we have and stop early; a 400-role
            # employer returning its first 120 beats returning nothing.
            #
            # Page 1 is different: with `out` still empty there is no partial
            # result to salvage, and swallowing it would report a live employer as
            # having zero jobs. Re-raise so engine._fetch_company records a real
            # error instead of a silent empty.
            if not out:
                raise
            break
        # Workday reports `total` ONLY on the first page; every later page returns
        # total=0. Re-reading it per page made the loop exit after 2 pages (offset
        # >= 0), silently capping every employer at 40 roles. Latch it once.
        if total is None:
            total = data.get("total") or 0
        postings = data.get("jobPostings") or []
        for j in postings:
            path = j.get("externalPath", "")
            # bulletFields carries a real 'Posting Date: MM/DD/YYYY'; postedOn is a
            # relative string ('Posted 26 Days Ago') that would rot in the cache.
            posted, posted_basis = "", None
            for b in j.get("bulletFields") or []:
                m = _WD_POSTED.search(str(b))
                if m:
                    mo, day, yr = m.groups()
                    posted = f"{yr}-{int(mo):02d}-{int(day):02d}"
                    posted_basis = "stated"  # a real 'Posting Date: MM/DD/YYYY'
                    break
            if not posted:
                # Only SOME tenants put an absolute date in bulletFields; the rest
                # expose just 'Posted 26 Days Ago'. Derive the date from it rather
                # than leaving posted empty — a blank date sinks the role in any
                # freshness filter, which would silently bury whole employers.
                # The helper labels it ARITHMETIC ON A PHRASE, not a date the tenant
                # published -- "30+ Days Ago" could be 30 or 300. This is the one
                # adapter that emits both bases, since only SOME tenants fill
                # bulletFields, so the two arrive side by side and must be told apart.
                rel = posted_from_relative(j.get("postedOn", ""))
                posted, posted_basis = rel["posted"], rel["posted_basis"]
            out.append(
                {
                    "title": j.get("title", ""),
                    "location": _workday_place(j, path),
                    "url": f"https://{slug}.{host}.myworkdayjobs.com/en-US/{site}{path}",
                    "posted": posted,
                    "posted_basis": posted_basis,
                    "employment_type": "",
                    "salary": "",
                    "text": "",
                    "_wd_path": path,  # consumed by the detail pass, stripped after
                }
            )
        offset += WORKDAY_PAGE
        if len(postings) < WORKDAY_PAGE or offset >= total:
            break

    # THE GATE RUNS BEFORE THE BODIES ARE BOUGHT. Filtering here rather than in the
    # engine is not a layering violation -- `keep` reads the title, and the title is
    # already in hand from the list endpoint. Everything this drops would have been
    # dropped by engine._consume moments later, after paying a request for it.
    if keep is not None:
        out = [r for r in out if keep(_title_of(r))]
    if _depth("workday_fetch_details") and out:
        _workday_add_details(base, out)
    for r in out:
        r.pop("_wd_path", None)
    return out


# ONE detail pool for the whole process, not one per employer.
#
# This used to be a `with ThreadPoolExecutor(WORKDAY_DETAIL_WORKERS)` inside
# _workday_add_details, i.e. inside a function the engine already calls from a
# 12-thread pool. Twelve employers in flight each opened their own 8-thread pool:
# a measured peak of 96 concurrent requests against a nominal cap of 12, and 12
# pools spawned and torn down per harvest. Sharing one pool makes the real ceiling
# `depth workers + WORKDAY_DETAIL_WORKERS` (~20) and makes the number mean what it
# says.
#
# Deadlock invariant: detail tasks only fetch: they never submit further work to
# this pool, so an outer worker blocking on `map` here cannot starve it.
_DETAIL_POOL: ThreadPoolExecutor | None = None
_DETAIL_POOL_SIZE: int | None = None
_DETAIL_POOL_LOCK = threading.Lock()


def _detail_pool() -> ThreadPoolExecutor:
    """The shared detail-fetch pool, built on first use and rebuilt if the worker
    count changes (tests do exactly that)."""
    global _DETAIL_POOL, _DETAIL_POOL_SIZE
    with _DETAIL_POOL_LOCK:
        if _DETAIL_POOL is None or _DETAIL_POOL_SIZE != _depth(
            "workday_detail_workers"
        ):
            if _DETAIL_POOL is not None:
                _DETAIL_POOL.shutdown(wait=False)
            _DETAIL_POOL = ThreadPoolExecutor(
                max_workers=_depth("workday_detail_workers"),
                thread_name_prefix="wd-detail",
            )
            _DETAIL_POOL_SIZE = _depth("workday_detail_workers")
        return _DETAIL_POOL


@atexit.register
def _shutdown_detail_pool() -> None:
    if _DETAIL_POOL is not None:
        _DETAIL_POOL.shutdown(wait=False)


def _wd_unslug(seg: str) -> str:
    """A Workday URL path segment -> the location string it was made from.

    Workday builds the segment by replacing each space with `-`, so a literal hyphen
    surrounded by spaces becomes `---`. Decoding therefore has to run longest-first:
    `Sao-Paulo---Barueri` is "Sao Paulo - Barueri", not "Sao Paulo   Barueri".
    """
    s = str(seg or "").replace("---", " \x00 ").replace("--", "\x00")
    return " ".join(s.replace("-", " ").replace("\x00", "-").split())


def _workday_place(j: dict, path: str) -> str:
    """The city for one Workday posting.

    `locationsText` -- which this adapter read for its entire life -- IS NOT IN THE
    LIST RESPONSE. Measured on accenture: `location` was empty on 120 of 120 rows,
    the same absent-key failure Workable had. The consequence was not a blank column
    but SILENT ROW LOSS: `dedup_key` is `company|title|location|job_id`, and with the
    location empty and the job id unparsed, every same-titled role a company posts
    worldwide collapsed into one row. On accenture (n=400) that discarded 89 rows,
    22% of the board, each with its own apply URL and its own city.

    Both values were in the payload already, at no extra request. Two sources, in
    preference order:

      * `bulletFields` -- the tenant's own display string, no decoding needed, but
        present on only some tenants (accenture sends ['R00333425', 'Buenos Aires'];
        academy and 3m send only ['R327553']).
      * `externalPath` -- `/job/<Location>/<Title>_<ReqId>` on EVERY tenant probed,
        so it is the reliable fallback.

    bulletFields is read BY SHAPE, never by position: it holds a requisition id, a
    location and sometimes 'Posting Date: MM/DD/YYYY', in no guaranteed order. A
    positional read is the same mistake this repo already made and fixed in the HN
    adapter, where the remote token turned out to be in slot 2 sixty-two times and
    slot 3 thirty-four times.
    """
    # `locationsText` FIRST, and kept even though it is absent from every tenant
    # probed: it is the documented field, some tenant may well send it, and reading a
    # real value costs nothing. The bug was never that this key is wrong -- it was
    # that it was the ONLY thing read, so its absence produced an empty column with no
    # fallback at all.
    if str(j.get("locationsText") or "").strip():
        return str(j["locationsText"]).strip()
    for b in j.get("bulletFields") or []:
        s = str(b or "").strip()
        if not s or _WD_POSTED.search(s) or _WD_REQID.fullmatch(s):
            continue
        return s
    # /job/<Location>/<Title>_<ReqId> -- take the segment after "job".
    parts = [p for p in str(path or "").split("/") if p]
    if len(parts) >= 3 and parts[0] == "job":
        return _wd_unslug(parts[1])
    return ""


def _workday_add_details(base: str, rows: list[dict]) -> None:
    """Fill in `text` (and salary parsed from it) from Workday's per-job detail call.

    Mutates in place. Best-effort per row: one unreachable detail must not cost the
    whole employer, so a failure leaves that row's body empty rather than raising.
    """

    def _one(r):
        path = r.get("_wd_path")
        if not path:
            return
        try:
            data = get_json(f"{base}{path}")
        except NET_ERRORS:
            return
        except Exception:  # noqa: BLE001
            return
        info = data.get("jobPostingInfo") or {}
        text, secs = clean_with_sections(info.get("jobDescription", "") or "")
        if text:
            r["text"] = text
            r["sections"] = secs
            r["salary"] = r["salary"] or salary_from_text(text)
        # startDate is a real ISO date; prefer it over anything derived from a
        # relative string when the detail call gives us one. THROUGH THE HELPER, so
        # the basis is upgraded with the value -- setting posted alone left the row
        # claiming "relative" while holding a date the tenant actually published,
        # which is the same drift _rippling_detail had.
        if (fresh := posted_from(info.get("startDate")))["posted"]:
            r.update(fresh)
        if info.get("timeType"):
            r["employment_type"] = info["timeType"]

    list(_detail_pool().map(_one, rows))


# Rippling's LIST endpoint returns five fields and no body or date -- those live on a
# per-job detail call, exactly like Workday. Same reasoning applies (a body-less job is
# unrankable and unreadable), so details are ON by default and this is the expensive
# half: Rippling's own board is 748 roles, i.e. 1 list + 748 detail requests. Set
# RIPPLING_FETCH_DETAILS=0 for a tight harvest window. Discovery never pays it --
# live_rippling below answers "is this board real" from the list alone.
RIPPLING_API = "https://api.rippling.com/platform/api/ats/v1/board"


def _rippling_detail(slug: str, row: dict) -> None:
    """Fill one row from the detail endpoint. Best-effort: a failure leaves the row
    with its list fields rather than sinking the whole board."""
    try:
        d = get_json(f"{RIPPLING_API}/{slug}/jobs/{row['_uuid']}")
    except NET_ERRORS:
        return
    except Exception:  # noqa: BLE001
        return
    if not isinstance(d, dict):
        # The list endpoint returns an array and the detail endpoint an object. A
        # vendor that ever serves the wrong one here must cost this row its body,
        # not sink the whole board with an AttributeError -- the same reasoning as
        # engine._coerce, applied one layer earlier.
        return
    desc = d.get("description")
    if isinstance(desc, dict):
        # Two HTML blocks: `company` is boilerplate repeated across every role,
        # `role` is the actual posting. Order matters -- role first, so a truncated
        # body keeps the part that describes the job.
        row["text"], row["sections"] = clean_with_sections(
            " ".join(x for x in (desc.get("role"), desc.get("company")) if x)
        )
    elif isinstance(desc, str):
        # BOTH branches. An earlier draft wired only the dict-shaped one above, which
        # would have left every string-shaped Rippling response with a body and no
        # sections -- a per-source hole nothing at the adapter level would surface.
        row["text"], row["sections"] = clean_with_sections(desc)
    # Through the helper, so the basis travels with the date. The list endpoint sends
    # no date at all, so this detail call is where a Rippling row gets one -- and
    # setting `posted` directly here left every Rippling row with a date and no
    # basis, which is exactly the drift posted_from exists to make impossible.
    if (fresh := posted_from(d.get("createdOn")))["posted"]:
        row.update(fresh)
    # payRangeDetails is a LIST (one entry per pay location) and is present on only
    # 3 of 30 sampled postings -- rare, but it is a figure the employer committed to,
    # which no text scrape can claim. First entry that carries a real range; the rest
    # differ by location, which this record has no column for.
    for pr in d.get("payRangeDetails") or []:
        if not isinstance(pr, dict):
            continue
        got = salary(
            pr.get("rangeStart"),
            pr.get("rangeEnd"),
            pr.get("currency"),
            salary_period(pr.get("frequency")),
        )
        if got["salary_min"] is not None:
            row.update(got)
            break
    et = d.get("employmentType")
    if isinstance(et, dict):
        # INVERTED, and not a typo: `id` holds the human string ("Salaried,
        # full-time") while `label` holds the code ("SALARIED_FT"). The list
        # endpoint's `department` uses the opposite convention.
        row["employment_type"] = et.get("id") or et.get("label") or ""
    locs = d.get("workLocations")
    if isinstance(locs, list) and locs:
        # One posting can list several places; the list endpoint shows only one.
        row["location"] = "; ".join(str(x) for x in locs if x)
    row["salary"] = salary_from_text(row["text"])


def fetch_rippling(slug: str, keep=None):
    """Rippling ATS board -- keyless JSON array, one request for the whole board.

    Bodies and dates are NOT on the list endpoint; see rippling_fetch_details above
    for what fetching them costs. Rippling's own board is 739 roles, so a full fetch
    is 740 requests and 739 of them are bodies.

    `keep(title) -> bool` runs BEFORE the detail pass, for the same reason it does in
    fetch_workday: the list endpoint already carries the title, and the relevance gate
    reads nothing else. `keep=None` fetches every body, as before.
    """
    rows = get_json(f"{RIPPLING_API}/{slug}/jobs")
    out = []
    for j in rows if isinstance(rows, list) else []:
        dept = j.get("department") or {}
        loc = j.get("workLocation") or {}
        out.append(
            {
                "_uuid": j.get("uuid", ""),
                "title": j.get("name", ""),
                "location": loc.get("label", "") if isinstance(loc, dict) else "",
                "url": j.get("url", ""),
                "posted": "",
                # THE PRECONDITION FOR THE 0.9.0 REMOVAL, and it had to land first.
                # This was the ONLY adapter of nineteen that set no `team` and no
                # `category`: every other one assigned its org unit to `team`
                # (greenhouse, lever, ashby, smartrecruiters, workable) or its job
                # family to `category` on the line beside the `department` it also
                # filled, so cutting that field cost them nothing -- while here it
                # would have silently dropped Rippling's org unit from every row.
                # Invisible to any corpus measurement: rippling is keyless but was not
                # among the sources in the harvest the cut was measured on. Found by
                # reading all nineteen adapters, which is the only method that covers a
                # source that did not run. `department.label` is an org unit
                # ({"id": "Eng", "label": "Engineering"}) -- the same vendor shape the
                # other four map to `team`.
                "team": dept.get("label") if isinstance(dept, dict) else None,
                "employment_type": "",
                "salary": "",
                "text": "",
            }
        )
    if keep is not None:  # gate before the bodies — see the docstring
        out = [r for r in out if keep(_title_of(r))]
    if _depth("rippling_fetch_details") and out:
        list(_detail_pool().map(lambda r: _rippling_detail(slug, r), out))
    for r in out:
        r.pop("_uuid", None)
    return out


def fetch_teamtailor(slug: str):
    """Teamtailor career-site feed -- JSON Feed, one request, body and date included.

    Each item also carries `_jobposting`, a schema.org JobPosting used here only for
    the fields the feed itself omits. The feed's own `title` is the COMPANY name,
    which almost no other ATS reports (see catalog/teamtailor.md).
    """
    data = get_json(f"https://{slug}.teamtailor.com/jobs.json")
    out = []
    for j in data.get("items", []) if isinstance(data, dict) else []:
        jp = j.get("_jobposting") if isinstance(j.get("_jobposting"), dict) else {}
        # `jobLocation` IS A LIST, not a dict. This guarded on `isinstance(place,
        # dict)`, which never fired -- so `location` was "" on 53 of 53 rows across
        # three boards, every one of which carries a full structured address.
        raw_places = jp.get("jobLocation")
        places: list[dict] = []
        for x in raw_places if isinstance(raw_places, list) else [raw_places]:
            addr = x.get("address") if isinstance(x, dict) else None
            if isinstance(addr, dict):
                places.append(addr)

        def _fmt(a: dict) -> str:
            return ", ".join(
                str(p)
                for p in (
                    a.get("addressLocality"),
                    a.get("addressRegion"),
                    a.get("addressCountry"),
                )
                if p
            )

        first: dict = places[0] if places else {}
        loc = _fmt(first)
        if jp.get("jobLocationType") == "TELECOMMUTE":
            loc = (loc + " (Remote)").strip()
        text, secs = clean_with_sections(
            j.get("content_html", "")
            or (jp.get("description", "") if isinstance(jp, dict) else "")
        )
        out.append(
            {
                "title": j.get("title", ""),
                "location": loc,
                "url": j.get("url", ""),
                **posted_from(j.get("date_published")),
                "city": first.get("addressLocality") or None,
                "state": first.get("addressRegion") or None,
                "country": first.get("addressCountry") or None,
                "locations": [
                    {
                        "raw": _fmt(a),
                        "city": a.get("addressLocality"),
                        "state": a.get("addressRegion"),
                        "country": a.get("addressCountry"),
                    }
                    for a in places
                ]
                or None,
                # The only WORKING expires on the depth lane -- 8 of 53 measured.
                "expires": to_date((jp.get("validThrough") or "")[:10]),
                "employment_type": jp.get("employmentType") or "",
                "salary": salary_from_text(text),
                "text": text,
                "sections": secs,
            }
        )
    return out


DEPTH_ALL: dict[str, Callable[..., list]] = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "smartrecruiters": fetch_smartrecruiters,
    "workable": fetch_workable,
    "workday": fetch_workday,
    "rippling": fetch_rippling,
    "teamtailor": fetch_teamtailor,
}

# Adapters needing more than a bare slug. engine._fetch_company passes these extra
# watchlist fields through as kwargs; every other adapter keeps the fetch(slug)
# contract the funnel's probe depends on.
DEPTH_EXTRA_FIELDS = {"workday": ("host", "site")}

# Adapters that accept a `keep(title) -> bool` predicate and apply it BEFORE their
# per-role detail pass. Only the two adapters that buy bodies one request at a time
# need it; everywhere else the whole board arrives in one call and there is nothing
# to defer. Declared here, like DEPTH_EXTRA_FIELDS, rather than discovered by
# signature inspection: the engine should read a registry, not guess from a function
# object, and adding an adapter to this set is the whole opt-in.
DEPTH_ACCEPTS_KEEP = frozenset({"workday", "rippling"})

# Sources whose apply URL reaches the place an application is actually SUBMITTED,
# rather than a page that links onward. schema.org's `directApply` asks exactly this
# -- "can you complete an application from this URL" -- and it is the distinction the
# whole product is built on.
#
# Every DEPTH source qualifies by construction: the url IS the employer's applicant-
# tracking system. Two breadth sources also qualify and would be wrong to exclude
# merely for being breadth:
#   usajobs     applications for federal roles are submitted ON usajobs.gov. It is
#               the government's own system, not a board that points at one.
#   braintrust  the application is completed on Braintrust; the client is hidden by
#               design, so there is no other destination to be redirected to.
# Everything else serves a redirect: adzuna's field is literally `redirect_url`.
# google_jobs is decided PER ROW by _best_apply_link and never falls through to this.
DIRECT_APPLY_SOURCES = frozenset({"usajobs", "braintrust"})


# ── SOURCES THAT PUBLISH MODEL-PREDICTED PAY ────────────────────────────────
#
# `engine.derive_salary` refuses to parse anything for a row from one of these when
# the vendor sent no numeric figures of its own. A model's guess must never reach
# `salary_min`, which README.md defines as pay an employer committed to.
#
# THE SOURCE NAME IS THE ONLY KEY THE RECORD CARRIES, and that is forced rather than
# preferred -- do not go looking for a cleaner one. A PREDICTED adzuna row and a
# genuine NO-FIGURE adzuna row are byte-identical in the record: `_adzuna_pay` returns
# `{"salary": ""}` for the prediction, and a row with no figures renders
# `util.salary_range(None, None) == ""` alongside `vocab.salary(None, None, ...)`
# all-None. `salary_is_predicted` is a vendor field and never enters the record.
#
# WHY THE GUARD IS NOT ONLY IN `_adzuna_pay`. Until this set existed the quarantine was
# entirely upstream and `derive_salary` had none of its own -- measured on 1a1414d,
# `{"source": "adzuna", "salary": "$129,584–$129,584"}` parsed straight through to
# `salary_min=129584.0, salary_basis='parsed', salary_kind='base'`. That EN-DASH
# (U+2013) fake range is the exact shape 6,633 rows carried [live prod, engine 0.8.2],
# so the one string that had actually reached a production store was the one string
# with no defence inside the parser. Two independent guards now, mutation-tested apart.
#
# THAT STRING IS NOT REACHABLE FROM THIS ADAPTER AT HEAD, and saying so is the
# difference between a claim that survives re-derivation and one that does not.
# `util.salary_range` renders a point estimate as `$129,584` -- no dash -- so
# `salary_from_display` refuses it even if the discard branch below regressed, and that
# regression would write `salary_min` DIRECTLY via `vocab.salary(...)` rather than
# through any parse. `$129,584–$129,584` is the 0.8.2 rendering, alive in the live
# consumer's store and in nothing this tree emits. The guard is defence in depth against
# re-introducing it, and against a library caller feeding `derive_salary` rows from its
# own 0.8.2-era store. Not a live pipeline leak.
#
# ADZUNA IS THE ONLY MEMBER, and that was established by sweeping the `salary:` field
# notes of all 22 `catalog/` profiles: `salary_is_predicted` is the only estimate flag
# in the set, and every other structured salary is a vendor-stated figure. (Two profiles
# match `predict` as the English word "predicts" in prose -- a grep count is not a
# membership test.) That sweep is the instrument; no live probe was needed or run.
#
# google_jobs was the one worth checking twice, being the least documented adapter, and
# it is ABSENT for a structural reason rather than a judgement about Google.
# `vocab.google_salary` fills `salary_min` AT THE ADAPTER whenever
# `detected_extensions.salary` parses, so `derive_salary` returns at its fill-only guard
# and never reaches this set: adding google_jobs would be a no-op on precisely the rows
# it would be added for -- verified by running the shipped functions, not by reading
# them. If Google is ever found to emit an ESTIMATE there, the defect is `google_salary`
# writing basis="parsed" onto it at the adapter, and this set is the wrong instrument.
#
# WHAT IT COSTS IS ZERO TODAY AND NON-ZERO THE MOMENT A BODY SCAN EXISTS, and that is
# worth a sentence here rather than an argument later. `derive_salary` returns on this
# set BEFORE it reads any body, so a scan would be skipped on EVERY Adzuna row --
# including one whose employer stated pay in the description and whose vendor simply
# sent no structured numbers. That row is a true positive being refused, and it is
# already pinned by `test_a_non_predicted_adzuna_row_keeps_its_kind_but_not_its_figure`,
# so revisiting the decision means CHANGING a test rather than adding one. It is
# deliberate: that row is byte-identical in the record to a prediction, so admitting it
# admits predictions too. The recall lost is probably small -- Adzuna ships
# `text_basis: "excerpt"`, a 500-character truncation on 275 of 275 rows locally and
# 7,146 of 7,150 [live prod, 2026-08-20], so there is little prose there to read -- but
# "probably small" is an estimate and nobody has measured it on a real Adzuna body.
#
# WHAT THIS SET DOES NOT COVER, said plainly because a register reads as broader than it
# is: it keys on WHO PUBLISHED the row, so it cannot see a third-party estimate quoted
# inside an ordinary source's prose, and it would not catch a second predicting source
# until someone adds it here. A guard on the SHAPE of the figure -- refusing lo == hi on
# a body-derived number -- covers both and is the right instrument for the body-scan lane
# when that lane exists. It must NOT be applied to display strings globally: 212 of
# 39,849 rows with a display string are genuine employer point values [102,799-row
# harvest, 2026-08-20], greenhouse 195 and lever 17, and a blanket refusal deletes them.
PREDICTED_PAY_SOURCES = frozenset({"adzuna"})


# ── LIVENESS: does this board exist? -- live_<ats>(slug, **extra) -> int ─────
#
# Three callers -- discover.probe, funnel.funnel and seed.seed_universe -- only
# ever needed a COUNT, but all three called the full production adapter to get
# one. That is the most expensive possible way to answer a yes/no:
#
#   workday      210 requests (10 list pages + 200 per-job detail GETs) -> 1
#   greenhouse   4.4 MB of job bodies -> 244 KB   (measured 2026-07-22)
#   lever        379 KB -> 8 KB                   (measured 2026-07-22)
#
# The Workday case is the one that mattered: probing a few hundred tenants at 210
# requests each is what tripped their rate limiter, so the 429 handling in
# discover.probe existed to survive a storm this over-fetch was itself causing.
#
# Every variant below returns an EXACT count, never an approximation, because
# discover.discover() and from_names() sort candidates by `-roles`; a capped or
# estimated number would silently reorder the review queue.
def live_greenhouse(slug: str) -> int:
    # Same endpoint as fetch_greenhouse minus `content=true`: the job list without
    # the descriptions, which is where ~95% of the bytes are.
    return len(get_json(f"{GREENHOUSE_API}/{slug}/jobs").get("jobs") or ())


def live_lever(slug: str) -> int:
    # Lever honours ?limit= but reports no total, so this proves >=1 posting
    # rather than counting them. probe only branches on zero/non-zero.
    return len(get_json(f"https://api.lever.co/v0/postings/{slug}?mode=json&limit=1"))


def live_smartrecruiters(slug: str) -> int:
    return int(
        get_json(
            f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=1"
        ).get("totalFound")
        or 0
    )


def live_workable(slug: str) -> int:
    # UNVERIFIED SAVING, deliberately noted: `details=false` is the documented
    # lighter variant and returns the same {name, description, jobs} shape (checked
    # 2026-07-22), but every Workable account reachable for testing had zero open
    # roles, so the byte saving on a POPULATED board was never measured. It cannot
    # be worse than details=true; it may simply be equal. Do not quote a number for
    # this one until someone probes a real board.
    return len(
        get_json(
            f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=false"
        ).get("jobs")
        or ()
    )


def live_workday(slug: str, host: str = "wd1", site: str = "") -> int:
    # One POST, and crucially NO detail pass -- the detail pass is 200 of the 210
    # requests the full adapter costs. `total` is authoritative on page 1 (it is
    # reported as 0 on every later page, which is the trap fetch_workday latches).
    data = post_json(
        f"https://{slug}.{host}.myworkdayjobs.com/wday/cxs/{slug}/{site}/jobs",
        {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""},
    )
    return int(data.get("total") or 0)


# Ashby is deliberately absent: measured 2026-07-22, its posting-api returns the
# whole board (1.98 MB / 120 jobs) with or without includeCompensation, so there
# is no cheaper variant to call. It falls back to the full adapter below.
def live_rippling(slug: str) -> int:
    # The list endpoint alone: one request, no per-role detail calls. That gap is the
    # whole point -- a full fetch of a 748-role board costs 749 requests, liveness
    # costs 1. Teamtailor deliberately has NO cheap variant: its feed is a single
    # document, so a liveness call and a full fetch are the same request, and
    # liveness_for() falls back to counting a full fetch (same as ashby).
    rows = get_json(f"{RIPPLING_API}/{slug}/jobs")
    return len(rows) if isinstance(rows, list) else 0


LIVENESS: dict[str, Callable[..., int]] = {
    "greenhouse": live_greenhouse,
    "lever": live_lever,
    "smartrecruiters": live_smartrecruiters,
    "workable": live_workable,
    "workday": live_workday,
    "rippling": live_rippling,
}


def liveness_for(ats: str):
    """A callable answering "how many live roles?" for one board, or None if this
    build has no adapter for `ats` at all.

    Callers never need to know which ATSs have a cheap variant: one lands on the
    real thing, the rest transparently fall back to counting a full fetch. Adding
    a cheap variant later is a one-line change here and touches no caller.
    """
    cheap = LIVENESS.get(ats)
    if cheap:
        return cheap
    full = DEPTH_ALL.get(ats)
    if not full:
        return None
    return lambda *a, **kw: len(full(*a, **kw) or ())


# ── BREADTH: keyword aggregators -- search_<src>(queries) -> [posting] ───────
def search_remotive(queries, strict: bool = False):
    """ONE request. `queries` is accepted for signature parity and deliberately never
    reaches the URL.

    This used to loop `queries[:4]` sending `?search={query}`, with a comment calling
    the cap "polite". Both halves were wrong, measured 2026-08-03 (catalog/remotive.md):

      * `search` DOES NOTHING. `?search=nurse`, `?search=engineer` and `?limit=5` each
        return the identical 31 rows as the bare endpoint -- every parameter is
        ignored. So those were four IDENTICAL requests, not four searches, and the
        adapter had no way to know it was not filtering.
      * 31 rows is the WHOLE corpus, not a page. `total-job-count` says 31 too.
      * Remotive's own notice advises a maximum of FOUR REQUESTS PER DAY and warns of
        blocking. Four per RUN is 96/day on an hourly schedule -- the "polite" cap was
        24x the vendor's stated limit.

    One unfiltered request returns everything there is, is 4x cheaper, and is the only
    version that fits inside what Remotive asks for. The engine's own relevance gate
    does the filtering `search=` never did.
    """
    try:
        data = get_json("https://remotive.com/api/remote-jobs")
    except NET_ERRORS:
        if strict:  # see the note on `strict` in search_himalayas
            raise
        return []
    out = []
    for j in data.get("jobs", []):
        text, secs = clean_with_sections(j.get("description", ""))
        out.append(
            {
                "title": j.get("title", ""),
                "company": j.get("company_name", ""),
                # NOT the job's location -- `candidate_required_location` is where a
                # candidate may LIVE ("USA, UK", "Anywhere"). Kept because it is the
                # only geography Remotive sends, and every row here is remote by
                # definition (it is a remote-only board), so the " (Remote)" suffix is
                # accurate even though the place is a hiring region, not a workplace.
                "location": (j.get("candidate_required_location") or "") + " (Remote)",
                # The same string, read for what it IS: where a candidate may live.
                # 31/31, and today it is only concatenated into the display string.
                **stated_scope(j.get("candidate_required_location")),
                "url": j.get("url", ""),
                **posted_from(j.get("publication_date")),
                "category": j.get("category") or None,
                "remote_type": "remote",  # a remote-only board by definition
                # `board`, NOT `stated`: nothing on the ROW says remote -- every posting
                # here is remote because that is what this board is. Collapsing the two
                # into one label is what the basis field exists to prevent.
                "remote_basis": "board",
                "tags": [t for t in (j.get("tags") or []) if t] or None,
                "employment_type": j.get("job_type", ""),
                "salary": j.get("salary", "") or salary_from_text(text),
                "text": text,
                "sections": secs,
                "source": "remotive",
            }
        )
    return out


def _joined(v) -> str:
    """Jobicy returns several fields as EITHER a string or a list of strings, and
    which one is not documented. A posting's values must all be `str` — the CSV
    writer stringifies whatever it is given, so a list reaches the file as a Python
    repr (`['Engineering']`) rather than a value anyone can filter on."""
    if isinstance(v, list):
        return ", ".join(str(x) for x in v if x)
    return str(v) if v else ""


def search_jobicy(queries):
    data = get_json("https://jobicy.com/api/v2/remote-jobs?count=100")
    out = []
    for j in data.get("jobs", []):
        text, secs = clean_with_sections(
            j.get("jobDescription") or j.get("jobExcerpt", "")
        )
        out.append(
            {
                "title": j.get("jobTitle", ""),
                "company": j.get("companyName", ""),
                "location": (j.get("jobGeo") or "") + " (Remote)",
                "url": j.get("url", ""),
                **posted_from(j.get("pubDate")),
                "category": _joined(j.get("jobIndustry")) or None,
                "seniority": _joined(j.get("jobLevel")) or None,
                "remote_type": "remote",  # a remote-only board by definition
                # `board`, NOT `stated`: nothing on the ROW says remote -- every posting
                # here is remote because that is what this board is. Collapsing the two
                # into one label is what the basis field exists to prevent.
                "remote_basis": "board",
                "employment_type": _joined(j.get("jobType")),
                "salary": salary_from_text(text),
                # salaryMin/Max/Currency/Period are REAL fields on 46 of 100 rows --
                # and multi-currency (EUR/GBP/CAD, not just USD). The adapter was
                # regexing the description instead of reading the vendor's own
                # commitment sitting four keys away.
                **vocab.salary(
                    j.get("salaryMin"),
                    j.get("salaryMax"),
                    j.get("salaryCurrency"),
                    j.get("salaryPeriod"),
                ),
                # `jobGeo` is where a remote worker may sit ("USA", "EMEA, UK"),
                # 100/100 -- a region, never a city.
                **stated_scope(j.get("jobGeo")),
                "text": text,
                "sections": secs,
                "source": "jobicy",
            }
        )
    return out


def search_arbeitnow(queries):
    data = get_json("https://www.arbeitnow.com/api/job-board-api")
    out = []
    for j in data.get("data", []):
        if not j.get("remote"):
            continue
        text, secs = clean_with_sections(j.get("description", ""))
        jt = j.get("job_types")
        out.append(
            {
                "title": j.get("title", ""),
                "company": j.get("company_name", ""),
                "location": (j.get("location") or "") + " (Remote)",
                "url": j.get("url", ""),
                **posted_from(j.get("created_at")),
                "remote_type": "remote",  # the adapter already filtered on j["remote"] above
                "remote_basis": "stated",
                "tags": [t for t in (j.get("tags") or []) if t] or None,
                "employment_type": ", ".join(jt)
                if isinstance(jt, list)
                else (jt or ""),
                "salary": salary_from_text(text),
                "text": text,
                "sections": secs,
                "source": "arbeitnow",
            }
        )
    return out


def search_remoteok(queries):
    data = get_json("https://remoteok.com/api")
    out = []
    for j in data:
        if not isinstance(j, dict) or not j.get("position"):
            continue  # first element is legal/attribution metadata
        text, secs = clean_with_sections(j.get("description", ""))
        out.append(
            {
                "title": j.get("position", ""),
                "company": j.get("company", ""),
                "location": (j.get("location") or "") + " (Remote)",
                "url": j.get("url") or j.get("apply_url", ""),
                **posted_from(j.get("date") or j.get("epoch")),
                "employment_type": "",
                "remote_type": "remote",  # a remote-only board by definition
                # `board`, NOT `stated`: nothing on the ROW says remote -- every posting
                # here is remote because that is what this board is. Collapsing the two
                # into one label is what the basis field exists to prevent.
                "remote_basis": "board",
                "tags": [t for t in (j.get("tags") or []) if t] or None,
                # salary_min/salary_max are present on all 100 rows of this feed and
                # both are 0 (probed 2026-08-05) -- the keys exist, the data does not.
                # vocab.salary drops a falsy figure to None rather than asserting a
                # salary of zero on every row.
                "salary": salary_range(j.get("salary_min"), j.get("salary_max")),
                **vocab.salary(
                    j.get("salary_min"), j.get("salary_max"), currency="USD"
                ),
                "text": text,
                "sections": secs,
                "source": "remoteok",
            }
        )
    return out


def search_himalayas(queries, strict: bool = False):
    """`strict` exists for the live canary, and only for it.

    A harvest wants every source to fail soft: one dead aggregator must not sink a
    scan, so a network error is swallowed and the query is skipped. But that makes
    "the endpoint is down" and "the endpoint answered with a shape we can no longer
    parse" both arrive as an empty list, and the canary cannot tell a real outage
    from real drift — so it reports every failure as an ambiguous skip and can never
    go red. `strict=True` re-raises instead, which is what lets the canary
    distinguish the two. The engine never passes it.
    """
    out: list[dict] = []
    seen: set[str] = set()  # spans both lanes and every query
    for qy in queries:
        for page in range(1, _depth("himalayas_max_pages") + 1):
            try:
                data = get_json(
                    f"https://himalayas.app/jobs/api/search"
                    f"?q={q(qy)}&limit={HIMALAYAS_PAGE}&page={page}"
                )
            except NET_ERRORS:
                if strict:
                    raise
                break  # a dead page ends this query; other queries still run
            jobs = data.get("jobs") or []
            _himalayas_rows(jobs, out, seen)
            if len(jobs) < HIMALAYAS_PAGE:
                break  # short page -> no more results for this query
            time.sleep(0.5)  # be polite between pages of the same query
    _himalayas_browse(out, strict, cfg=config.active(), seen=seen)
    return out


def _himalayas_browse(out: list, strict: bool = False, cfg=None, seen=None) -> None:
    """The BROWSE lane -- the whole corpus, newest first.

    Himalayas has TWO endpoints with different pagination, and picking the wrong one
    costs an order of magnitude. `/jobs/api/search` takes `page` and walls at ~8,020
    rows. `/jobs/api` takes `offset` and walks the entire corpus -- `totalCount`
    reported 96,934 when this was measured. Sending `offset` to the SEARCH endpoint is
    silently ignored and returns page 1 forever, which is the trap that hid this.

    `q` does nothing here, so this cannot replace the search lane; it is a SECOND
    lane that sweeps the market while the search lane answers the title queries.

    Browse is DATE-ORDERED -- measured 2026-08-05: offset 0 -> median age 0 days,
    20,000 -> 8 days, 60,000 -> 28 days. That is what makes a bounded lane worth
    having: the rows this takes are the FRESHEST rows in the corpus, not an arbitrary
    slice of it.

    WHAT ACTUALLY BOUNDS IT: `himalayas_browse_pages` (default 50 = 1,000 rows). Be
    precise about this, because an earlier version of this comment claimed "the age
    gate is the budget" and that was FALSE at every shipped setting. Do the
    arithmetic: 50 pages x 20 = offset 980, and a 60-day row (the default
    `max_age_days`) sits near offset 130,000. The age branch below cannot fire at
    defaults -- it only binds when `max_age_days` is small enough to be reached inside
    the page cap. It is a secondary guard, not the budget.

    So this lane takes ~1,000 of ~97,000 rows, and the honest reason it is still worth
    it is ordering, not coverage: those 1,000 are the newest 1,000. Walking the whole
    corpus is 4,850 requests, which is a harvest of its own rather than one source
    among nineteen.
    """
    # The caller's cfg, falling back to the global. A library consumer (jobfitr)
    # passes an explicit Config to harvest(); reading config.active() here would have
    # given it the global default's age window instead of its own.
    cfg = cfg or config.active()
    cutoff = cfg.max_age_days
    for page in range(_depth("himalayas_browse_pages")):
        offset = page * HIMALAYAS_PAGE
        try:
            data = get_json(
                f"https://himalayas.app/jobs/api?limit={HIMALAYAS_PAGE}&offset={offset}"
            )
        except NET_ERRORS:
            if strict:
                raise
            return
        jobs = data.get("jobs") or []
        if not jobs:
            return
        before = len(out)
        _himalayas_rows(jobs, out, seen)
        # Secondary guard, NOT the budget (see the docstring). Stops once the newest
        # row on this page is already past the age gate -- the feed is ordered, so if
        # the best row here is too old, everything after it is too. Only fires when
        # `max_age_days` is small enough to be reached inside HIMALAYAS_BROWSE_PAGES.
        ages = [
            a
            for a in (age_int(r.get("posted", "")) for r in out[before:])
            if a is not None
        ]
        if ages and min(ages) > cutoff:
            return
        total = data.get("totalCount")
        if isinstance(total, int) and offset + HIMALAYAS_PAGE >= total:
            return  # the envelope announces the end; believe it
        time.sleep(0.5)


def _himalayas_rows(jobs, out, seen=None):
    """Map one page of Himalayas jobs into `out`, skipping URLs already seen.

    Dedup spans BOTH lanes and every title query, because this source now hits the
    same job several ways: six title queries against the search endpoint, plus the
    browse sweep, all overlapping. Measured without it: one job, three queries -> 53
    rows for 1 unique URL. The engine's dedup absorbs that downstream, but an adapter
    should not be transporting 50x the rows it can deliver -- and The Muse in this
    same release dedups its fan-out, so leaving this one duplicating would be two
    adapters disagreeing about whose job it is.
    """
    for j in jobs:
        url = j.get("applicationLink") or j.get("guid", "")
        if seen is not None and url:
            if url in seen:
                continue
            seen.add(url)
        text, secs = clean_with_sections(j.get("description") or j.get("excerpt", ""))
        regions = j.get("locationRestrictions") or []
        loc = (", ".join(regions) if regions else "") + " (Remote)"
        # The browse lane returns the literal string "name" as companyName on the
        # FRESHEST rows (20/20 at offset 0, reproduced twice ~20 min apart), while
        # `companySlug` is correct on every row of every probe. A placeholder company
        # is worse than a missing one -- it is a real-looking value that groups every
        # affected posting under one fake employer.
        comp = j.get("companyName") or ""
        if comp.strip().lower() in ("", "name"):
            comp = (j.get("companySlug") or "").replace("-", " ").title()
        out.append(
            {
                "title": j.get("title", ""),
                "company": comp,
                "location": loc.strip(),
                "url": url,
                **posted_from(j.get("pubDate")),
                "category": ", ".join(
                    x
                    for x in (j.get("parentCategories") or j.get("categories") or [])
                    if isinstance(x, str)
                )
                or None,
                "seniority": ", ".join(
                    x for x in (j.get("seniority") or []) if isinstance(x, str)
                )
                or None,
                "remote_type": "remote",  # a remote-only board by definition
                # `board`, NOT `stated`: nothing on the ROW says remote -- every posting
                # here is remote because that is what this board is. Collapsing the two
                # into one label is what the basis field exists to prevent.
                "remote_basis": "board",
                # `locationRestrictions` is a COUNTRY LIST ("United States", or forty of
                # them) -- never a city -- and it is passed through as a list. It used to be
                # joined into a string, which is unsplittable because ISO country names
                # contain commas, and `or None` turned the empty array (himalayas' documented
                # "open worldwide") into "unknown" on 29 rows. See stated_scope.
                **stated_scope(j.get("locationRestrictions")),
                # 20/20 on BOTH lanes -- the only wired source that actually
                # populates an expiry.
                "expires": to_date(j.get("expiryDate")),
                "source_extra": {
                    k: v
                    for k, v in (
                        ("company_slug", j.get("companySlug")),
                        ("timezones", j.get("timezoneRestrictions")),
                    )
                    if v
                }
                or None,
                "tags": [x for x in (j.get("categories") or []) if isinstance(x, str)]
                or None,
                "employment_type": j.get("employmentType", ""),
                # THE CURRENCY REACHES THE RECORD AND NOT THE DISPLAY, which is the
                # half of that discard the 0.8.x fix below did not close: `vocab.salary`
                # gets `currency` on the very next line while the string a listing page
                # RENDERS was still built without it, so a Toronto band showed as
                # `$168,000-$231,000` and a Polish one as `$25,200` for PLN -- about
                # $6,300. 46 rows [full harvest, 0.9.0]. himalayas is the only one of
                # `salary_range`'s four call sites this reaches: remoteok and adzuna
                # hard-code USD and usajobs is US-federal, so a bare `$` is right there.
                "salary": salary_range(
                    j.get("minSalary"), j.get("maxSalary"), j.get("currency")
                ),
                # `salaryPeriod` ("annual") and `currency` ("USD") are real fields,
                # confirmed live 2026-08-05 -- 7 of 20 rows carry them. Both were
                # being discarded while the two numbers beside them were kept.
                **vocab.salary(
                    j.get("minSalary"),
                    j.get("maxSalary"),
                    j.get("currency"),
                    j.get("salaryPeriod"),
                ),
                "text": text,
                "sections": secs,
                "source": "himalayas",
            }
        )


def _adzuna_pay(j: dict) -> dict:
    """Adzuna salary -> the commitment columns, or nothing at all.

    OWNS THE DISPLAY STRING TOO, and that is the point of the function. The split was
    enforced on the numeric columns and the caller built `salary` one line earlier from
    the same raw figures, so a model's point estimate rendered as `$109,106` -- a
    string a human reads as an employer's offer. 221 of 277 adzuna rows carried one
    [local 94-board harvest, 0.9.0, 2026-08-20]; 7,150 of 7,150 adzuna rows in the
    live consumer's store had a salary string while only 369 had a salary_min
    [live prod, engine 0.8.2].

    `util.salary_range` already stopped rendering the fake range `$109,106-$109,106`,
    and its comment names this exact failure -- but removing the RANGE appearance is
    not the same as removing the COMMITMENT appearance, and the bare figure kept it.

    NOTHING IS EMITTED FOR A PREDICTED ROW AS OF 0.9.0, and the figures ARE lost. The
    salary_estimated_* pair that used to carry them was removed.

    WHY THEY WERE REMOVABLE, stated carefully because the obvious argument is wrong.
    The columns existed so a model's guess could never sit beside a commitment, and on
    the last release that shipped them the separation leaked anyway: all 6,633 rows
    holding an estimate also rendered it as `$129,584–$129,584` -- the separator is an
    EN-DASH (U+2013), which is what the store holds and what a re-verification has to
    match; an ASCII hyphen returns 0 of 6,633. A point estimate shaped
    like a posted range, with 0 of them carrying a commitment figure
    `[live prod, engine 0.8.2]`. But `fa0cee3` and `9907e55` closed that leak EARLIER IN
    0.9.0 -- by the commit that removed the columns, a predicted row already emitted no
    display string and the quarantine was intact. So the leak is the HISTORY of why the
    pair existed, not the reason it went. The reason it went is that nothing downstream
    ever read it: the one known consumer writes both columns into its own schema and
    reads them back nowhere.

    `salary` STAYING EMPTY IS NOT THE PROTECTION, although an earlier version of this
    paragraph said it was -- and so did engine.py, vocab.py and two tests. Measured on
    1a1414d, `derive_salary` carried no guard of its own: handed a predicted row that
    still had the en-dash fake range `$129,584–$129,584`, it wrote
    `salary_min=129584.0, salary_basis='parsed'`. Emitting "" only avoids handing it
    one, which is a different thing from refusing it.

    THE PROTECTION IS `engine.derive_salary`'s PREDICTED_PAY_SOURCES guard, which
    refuses by SOURCE and does not read what this function emits. Said precisely: at
    HEAD that en-dash range is unreachable from here -- `salary_range` renders a point
    estimate dashless -- so the guard is defence in depth against re-introducing the
    0.8.2 rendering and against a caller passing us rows from a 0.8.2-era store, not a
    patch for a live leak. The two are
    independent on purpose, so a regression here no longer reaches the commitment
    columns by itself. (Belt and braces, and worth knowing: `vocab.salary_from_display`
    also returns all-None for both "" and None -- but it ACCEPTS `$129,584–$129,584`,
    so that refusal never covered the shape that actually leaked.)
    """
    lo, hi = j.get("salary_min"), j.get("salary_max")
    if str(j.get("salary_is_predicted")) == "1":

        # The prediction is DISCARDED. See the docstring for why the estimate columns
        # went; the short version is that nothing read them, not that nothing filled
        # them -- Adzuna fills them on 92.8% of its rows in the live consumer's store.
        return {"salary": ""}
    return {
        "salary": salary_range(lo, hi),
        **vocab.salary(lo, hi, currency="USD", period=None, basis="stated"),
    }


def _adzuna_place(area):
    """Adzuna's `location.area` -> (city, state, country, remote|None).

    `area` is a HIERARCHY, outermost first, and its depth varies:

        ['US']                                                 -> nationwide
        ['US','Texas','Howard County','Big Spring']            -> depth 4
        ['US','New York','New York City','Manhattan','Prince'] -> depth 5

    Two things make it worth reading. `area[1]` is a real US state in 246 of 246
    sampled rows -- structured geography this adapter previously discarded in favour
    of `display_name`, which is "City, County" and carries no state at all. And
    `area == ['US']` EXACTLY means nationwide, i.e. remote: 23 of 50 rows in a
    remote-filtered sample. Those rows' `display_name` is the bare string "US", which
    no text rule can read as remote -- so the signal existed and was invisible.

    Depth 5 shifts city one slot, so branch on length rather than indexing blindly.
    THIS SAID SO AND THEN DID NOT DO IT: the line read
    `city = area[-1] if len(area) >= 4 else None`, which takes whatever is last. Two
    defects fell out of that, and the docstring above was already right about both.

    At DEPTH 5 the last slot is a neighbourhood, not the city -- 'Prince',
    'Grand Central', 'Hayes Valley', 'SoMa'. At DEPTH 3 the city is sitting in
    `area[2]` and the `>= 4` test threw it away entirely: 'San Francisco, California'
    (15 rows) and 'New York City, New York' (8) returned `city=None` while the vendor
    had supplied a clean name. That second one is 23 of 276 rows -- larger than the
    neighbourhood problem it was filed alongside.

    PIN THE CITY TO ITS SLOT rather than reading a suffix list. The alternative
    considered was 'take the second display_name token unless it ends in County /
    Parish / Borough', which is a US-ENGLISH word list: Adzuna's UK, Australian and
    German hierarchies put a district, an LGA and a Kreis in that tier and it would
    read every one of them as a city. Position is a property of the vendor's data
    structure; 'ends in County' is a property of American English.

    WHAT THIS DOES NOT FIX, so nobody claims it does: Adzuna's hierarchy is itself
    unreliable. 'Times Square, King County' resolves to state='WA' on 3 rows, and
    Times Square is not in King County, Washington. Reading the hierarchy correctly is
    the most this can promise; producing correct geography is not available from this
    source, and a spot-check of a small sample will not surface it.

    Returns None (not "") for anything the array does not contain: unknown is not
    empty.
    """
    if not isinstance(area, list) or not area:
        return None, None, None, None
    country = area[0] or None
    if len(area) == 1:
        return None, None, country, True  # nationwide == remote
    state = area[1] or None
    # The BRANCH the docstring has always described. depth>=4: the city tier is
    # area[3] (identical to area[-1] at depth 4, and the neighbourhood is what
    # area[-1] returns at depth 5). depth==3: the city is the last slot.
    if len(area) >= 4:
        city = area[3] or None
    elif len(area) == 3:
        city = area[2] or None
    else:
        city = None
    return city, state, country, None


def search_adzuna(queries):
    cfg = config.active()
    app_id, app_key = cfg.env(cfg.adzuna_app_id_env), cfg.env(cfg.adzuna_app_key_env)
    if not (app_id and app_key):
        print("  adzuna: no API keys set -- skipped (the free sources still run)")
        return []
    # `where` resolves against Adzuna's PLACE HIERARCHY, so "remote" is not a value it
    # can take -- it returns 0 rows, which is indistinguishable from "no such jobs"
    # behind the `except NET_ERRORS: break` below. Measured 2026-08-03 on
    # what="AI Engineer", US:
    #
    #     where=remote           ->      0 rows
    #     where="" (the tempting fix) -> 55,052 rows, 2% actually remote
    #     what_and=remote        -> 15,500 rows, 84% actually remote
    #
    # So blanking `where` is NOT the fix: it trades zero results for a nationwide
    # scatter that the remote gate then throws away. `what_and` is a keyword AND and
    # is the only remote filter Adzuna offers. Real places still work and compose
    # with it (Louisville, KY -> 102; + what_and=remote -> 37).
    remote_query = _is_remote_query(cfg)
    where = "" if remote_query else cfg.location.strip()
    remote_filter = "&what_and=remote" if remote_query else ""
    # A radius (miles) around `location`; Adzuna's `distance` is in km. Only when
    # searching a real place and the user asked for one. Shares `remote_query` with
    # the branch above so the two cannot drift -- this used to test
    # `cfg.location.lower() != "remote"` on its own, which let "anywhere" through as
    # if it were a town.
    dist = ""
    if cfg.radius_miles > 0 and not remote_query:
        dist = f"&distance={round(cfg.radius_miles * 1.60934)}"
    # Adzuna caps a page at 50; walk `adzuna_pages` pages per query so a selective
    # downstream filter (remote-only) still has a deep pool to carve from. Stop a
    # query early once a page comes back short — there are no more results.
    pages = max(1, getattr(cfg, "adzuna_pages", 1))
    out = []
    for qy in queries:
        for page in range(1, pages + 1):
            try:
                data = get_json(
                    f"https://api.adzuna.com/v1/api/jobs/us/search/{page}"
                    f"?app_id={app_id}&app_key={app_key}&what={q(qy)}"
                    f"{f'&where={q(where)}' if where else ''}{remote_filter}{dist}"
                    f"&results_per_page=50&content-type=application/json"
                )
            except NET_ERRORS:
                break  # a dead page ends this query; other queries still run
            results = data.get("results", [])
            for j in results:
                text, secs = clean_with_sections(j.get("description", ""))
                loc = j.get("location") or {}
                city, state, country, is_remote = _adzuna_place(loc.get("area"))
                out.append(
                    {
                        "title": j.get("title", ""),
                        "company": (j.get("company") or {}).get("display_name", ""),
                        # `display_name` is "City, County" and carries NO state, which
                        # is why 39% of a downstream corpus was unparseable. The `area`
                        # array beside it has the state; see _adzuna_place.
                        "location": loc.get("display_name", ""),
                        "url": j.get("redirect_url", ""),
                        # SET HERE, not sniffed downstream: this adapter knows it is
                        # Adzuna. The API truncates every description at 500 chars and
                        # ends it with an ellipsis -- 275 of 275 rows locally, 7,146 of
                        # 7,150 [live prod, 2026-08-20], and a live probe confirms no
                        # fuller field exists on the result object. Without this the
                        # record cannot distinguish a 500-char excerpt from a real body
                        # averaging 6,870.
                        "text_basis": "excerpt",
                        **posted_from(j.get("created")),
                        "expires": to_date(j.get("deadline")),  # 1 of 20 populated
                        # `category.label` is a JOB FAMILY ("IT Jobs"), not an org unit
                        # -- one of the four different things `department` carried.
                        "category": (j.get("category") or {}).get("label") or None,
                        "city": city,
                        "state": state,
                        "country": country,
                        "remote_type": "remote" if is_remote else None,
                        "remote_basis": "location" if is_remote else None,
                        # `area == ["US"]` does not just mean "remote", it names the
                        # REGION a remote worker may sit in -- and that was the whole
                        # signal, flattened to a boolean. area[0] is the country.
                        "remote_areas": [country] if (is_remote and country) else None,
                        "employment_type": j.get("contract_time", ""),
                        # `salary` is built inside _adzuna_pay, not here. It used to
                        # be set on this line from the raw figures, which meant the
                        # predicted/stated split was decided in two places and the
                        # display string was outside the half that enforced it.
                        # PREDICTED salaries never touch the commitment columns.
                        # Measured 2026-08-05 across three queries: 140 of 150 rows
                        # with a salary were `salary_is_predicted: "1"` -- 93%, and
                        # 100% for nursing and warehouse. They are point estimates
                        # (min == max, to two decimals) produced by Adzuna's model,
                        # not figures an employer posted. In one column with real
                        # salaries, a single forgotten WHERE clause silently poisons
                        # every average built on the corpus.
                        #
                        # Adzuna sends NO period, so these stay period-less rather
                        # than being assumed annual, however annual they look.
                        **_adzuna_pay(j),
                        "text": text,
                        "sections": secs,
                        "source": "adzuna",
                    }
                )
            if len(results) < 50:
                break  # last page for this query
            time.sleep(0.5)  # be polite between pages of the same query
    return out


# How many "Who is Hiring?" threads to read. Two, because one is the start-of-month
# cliff: on the 1st the newest thread is nearly empty and the prior month's 245 rows
# vanish. The Algolia search already returns four, so this only costs the fetch.


def search_hn_whoishiring(queries):
    """HN's monthly 'Who is Hiring?' thread via the free Algolia API. Posts follow
    a loose 'COMPANY | ROLE | LOCATION | TYPE | url' convention; parse those."""
    try:
        hits = get_json(
            "https://hn.algolia.com/api/v1/search_by_date"
            "?tags=story,author_whoishiring&hitsPerPage=8"
        ).get("hits", [])
    except NET_ERRORS:
        return []
    # TWO threads, not one. This took only the newest, and the failure shape is ugly:
    # on the 1st of a month it switches to a thread with almost nothing in it and
    # silently drops the entire prior month. Measured 2026-08-04 (August thread one
    # day old): Aug 138 parsable rows, Jul 245 -- so one extra request nearly triples
    # the yield and removes the start-of-month cliff. The search above already returns
    # four matching threads, so the extra breadth is free apart from the fetch.
    threads = [h for h in hits if "who is hiring" in (h.get("title") or "").lower()][
        : _depth("hn_threads")
    ]
    if not threads:
        return []
    out: list[dict] = []
    for thread in threads:
        try:
            tree = get_json(f"https://hn.algolia.com/api/v1/items/{thread['objectID']}")
        except NET_ERRORS:
            continue  # one dead thread must not cost the other
        _hn_rows(tree, out)
    return out


# An HN comment's pipe segments are `Company | Title | Location | ...`, but the convention
# is loose and a comment that runs out of pipes puts the ENTIRE BODY in the segment the
# location is read from. Measured: hn `location` averaged 461 characters and reached 2,158,
# against a maximum of 331 for greenhouse, 121 for themuse and 34 for ashby -- 82 of 196
# rows over 100 characters. Every prose rule in `vocab.remote_scope` is written for a short
# location string, so feeding it a job description is a category error, and it produced
# wrong values rather than merely noisy ones:
#   'REMOTE (EU, Switzerland, Norway) ... A lot of us have ...' -> ['CH','NO','US']
#   'Toronto, Canada REMOTE (Canada only) ...'                  -> ['CA','US']
#   'ONSITE, NYC ... backends are global scale built on AWS'    -> []  (stated worldwide)
# `US_LOCATION_RE` carries a bare `us` on purpose -- vocab says so in as many words, because
# "Remote - US" is 227 rows -- and against 2 KB of prose that matches the English pronoun.
# Three of the 12 affected rows matched inside a URL: `https://grnh.se/bhfswi9e5us`.
#
# TRUNCATED, NOT DROPPED, and that distinction is the fix. Filtering out over-long segments
# instead emptied the location entirely on 9 rows whose header and body share one segment
# ("REMOTE (US) Origamics is building...") -- discarding a genuinely stated boundary to
# remove noise attached to it.
#
# 64 IS NOT ARBITRARY. A mid-token cut invents places: at 48, "Remote (USA, most states) or
# Onsite (NYC, NC, MA)" truncates inside the list and `split_place` reads the fragment as a
# city with state NC, which flips `has_city` and suppresses a correct ['US']. 64 is the
# shortest cap tested that keeps every measured multi-place header intact -- "London / NYC /
# SF / Seattle / Remote (US + Europe)" needs it, and resolves to (['US'], ['EUROPE']) only
# there.
#
# THE WORD-BOUNDARY REWIND IS UNEXERCISED BY REAL DATA AT THIS CAP, and that is worth
# writing down rather than letting a green suite imply otherwise. All 196 hn segments were
# truncated with and without it: ZERO changed `remote_scope`. It is kept because the
# mid-token failure is real and demonstrated at 48, so the rewind is what makes the cap safe
# to lower later. The test pins it with a CONSTRUCTED string, not an observed one -- the
# first version of that assertion passed with the rewind deleted, which is the same
# green-but-blind shape this file keeps finding.
#
# A MITIGATION, NOT A CURE. A body that begins inside the first 64 characters still
# contaminates: "Utrecht, The Netherlands HYBRID We are a non-profit..." keeps ['NL'] from
# an office address. 2 rows, unchanged by this and not made worse.
# THE CAP IS PER SEGMENT, SO THE FIELD'S REAL BOUND IS TWICE IT. `location` joins
# `parts[2]` and `parts[3]`, each truncated independently, so a two-segment header can reach
# 129 characters and the measured maximum is 119 -- not 64. Spelled out because the constant
# name reads like a bound on the field and is not one.
#
# IT ALSO CANNOT SEE A BLOCK-TAG BOUNDARY, which is the residual `id-pro` measured: on the
# Portless row the header ends at a `<p>`, not a newline, so this leaves 83 characters with
# 49 of them body prose. That is fixed upstream, by splitting the DECODED markup before the
# strip -- and NOT by lowering this cap, because split-only reaches a WORSE maximum (548) on
# comments carrying no block tag before a long header. Split first, this second.
_HN_LOCATION_CAP = 64

# The header/body boundary in an HN comment is a BLOCK TAG -- not a pipe, and not a
# newline. `clean` flattens the tag into the same text run, so the segment that should
# hold a location swallows the whole posting body instead. Splitting the DECODED markup
# here, BEFORE the strip, is the cure the cap above calls itself a mitigation for.
_HN_BLOCK = re.compile(r"<(?:p|br|div|ul|ol|li|blockquote)\b[^>]*>", re.I)


_HN_HREF = re.compile(r'<a\s+href="([^"]+)"', re.I)


def _slug_owns_company(url: str, company: str) -> bool:
    """Does this board actually belong to `company`?

    A host check proves a board is REAL, never WHOSE it is. Measured: the HN comment
    for Phaselaw carries `jobs.ashbyhq.com/Pear-VC/...` -- an investor's board, posting
    for a portfolio company. `_is_direct_apply` passes it because `ashbyhq.com` is on the
    ATS allowlist, and handing a user the wrong employer's posting is worse than handing
    them a broken one: the broken link fails visibly, this one looks right.

    Gates only links this module RECOVERS, never a URL the poster typed -- the point is
    to avoid ASSERTING an owner we invented, not to second-guess the source.

    True when there is nothing to check. A board with no slug, or a name too short to
    compare, is not evidence of a mismatch, and `_ATS_HOSTS` already carries the
    positive evidence. CLAUDE.md records the same limit from the other side: identity
    verification covers Greenhouse only, because it is the one ATS that reports an owner.
    """
    from .dedup import ats_from_url
    from .discover import _norm_name

    got = ats_from_url(url)
    if not got or not got[1]:
        return True
    slug = re.sub(r"[^a-z0-9]", "", got[1].lower())
    token = re.sub(r"[^a-z0-9]", "", _norm_name(company).lower())
    # 4, and prefix-compared, because a board slug abbreviates: "Eleos Technologies"
    # ships as `eleostech`, so equality fails and containment must run BOTH ways.
    if len(token) < 4 or len(slug) < 4:
        return True
    return token[:6] in slug or slug[:6] in token


def _hn_url(raw: str, company: str, text: str, item_id) -> tuple[str, bool]:
    """The apply link in an HN comment, read from the MARKUP rather than the prose.

    Returns `(url, recovered)`. `recovered` is True only when the link came out of an
    href this function went looking for -- the caller uses it to withhold a
    `direct_apply` promotion, because a link WE dug up must earn that flag rather than
    inherit it from host shape. See the note in `_hn_rows` where it is stashed.

    HN renders a long link as `<a href="FULL">https://boards.greenhouse.io/acme/j...</a>`.
    `clean` strips the tag, keeps the DISPLAY text and throws the href away -- so the
    regex below recovers a URL with a literal ellipsis in it, which 404s. 48 of 196 rows
    carry one, and they skew to `boards.greenhouse.io` and `jobs.lever.co`: the bug
    destroys the highest-value links. The full target was in the response the whole time.

    Same invariant as `sections` and as the location split above: STRUCTURE IS READ
    BEFORE THE STRIP, OR NOT AT ALL. An href is structure.

    TIER 1 ONLY, and deliberately not `_best_apply_link`'s full preference order. Its
    middle tier is "anything not on the known-aggregator list", running on a list
    measured as under-populated -- applying it here promotes 15 unclassified hosts
    (`notion.site`, `careers-page.com`) and, on one measured row, trades an employer
    homepage for `linkedin.com/jobs/view/...` on a product whose whole claim is
    direct-to-employer. Reuse the TIER, not the ORDER.
    """
    prose = m.group(0) if (m := re.search(r"https?://[^\s)\]]+", text)) else ""
    thread = f"https://news.ycombinator.com/item?id={item_id}"
    for href in (html.unescape(h) for h in _HN_HREF.findall(raw)):
        parsed = urlparse(href)
        if not (parsed.path.strip("/") or parsed.query):
            continue  # a bare homepage is not a posting, and we already have one
        if _is_direct_apply(href, company) and _slug_owns_company(href, company):
            return href, True
    # A truncated URL is a KNOWN 404, so the thread link -- which reaches the comment
    # holding the posting -- beats it. Not a promotion: `_is_direct_apply` reads False
    # on news.ycombinator.com, so these rows stay honestly not-direct and stay out of a
    # direct-only consumer's intake. 15 of the 26 unrecovered rows are provably dead.
    if prose and ("..." in prose or "…" in prose):
        return thread, False
    return (prose or thread), False


def _hn_location(segment: str) -> str:
    """One pipe segment -> at most a location-shaped head of it."""
    head = segment.split("\n", 1)[0].strip()  # a newline ends the header line outright
    if len(head) <= _HN_LOCATION_CAP:
        return head
    cut = head[:_HN_LOCATION_CAP]
    space = cut.rfind(" ")
    return (cut[:space] if space > 0 else cut).strip()


# The "Seeking Freelancer / Wanted" template, as labelled fields. A JOB SEEKER posting
# inside a hiring thread -- `search_hn_whoishiring` already filters threads to "who is
# hiring", so no thread-level filter can reach this, and it needs a per-comment shape
# test instead.
#
# NOT ANCHORED TO A LINE START, and that is the whole reason the first version of this
# fired on nothing: `clean` flattens the seeker block onto one line, so `^` never
# matches. The first detector scored 0 of 219 INCLUDING the row it was written for.
_HN_SEEKER_LABELS = tuple(
    re.compile(rf"(?<![a-z]){p}\s*:", re.I)
    for p in (
        r"willing to relocate",
        r"r[\u00e9e]sum[\u00e9e](?:\s*/\s*cv)?",
        r"location",
        r"remote",
        r"e-?mail",
        r"technologies",
        r"seeking",
    )
)


def _is_hn_seeker_post(text: str) -> bool:
    """Does this comment use the job-SEEKER template rather than a hiring one?

    THREE OR MORE labels, and the threshold is measured rather than chosen. Scored
    against every hn row in a 102,799-row harvest [2026-08-20] -- 219 comments, of
    which 1 is a seeker:

        >= 3 of the seven labels      fires on 1 of 219   0 false positives
        'willing to relocate:' alone  fires on 1 of 219   0 false positives
        resume/cv OR relocate         fires on 2 of 219   1 FALSE POSITIVE

    THE FALSE POSITIVE IS WHY `r\u00e9sum\u00e9` CANNOT DECIDE ALONE: a genuine listing
    said "Resume:" because it was ASKING for one. So no single label is sufficient,
    including the most seeker-specific one -- a seeker who omits it would still be
    caught by the other six, and a hiring post has to use three before it is dropped.

    WHAT THE ZERO DOES AND DOES NOT MEAN. 0 of 218 genuine hiring posts is an upper
    bound of roughly 1.4% at 95% confidence, not proof of zero, and no hiring post in
    that corpus reached even two labels -- so the margin is real but it is one thread
    on one day. RECALL RESTS ON n=1: exactly one seeker row exists in the corpus, so
    this is measured against a single example of what it is built to catch, and a
    seeker using a different template is not covered by any of it.

    Dropping the whole comment is the point rather than scrubbing the fields. The row
    that motivated this carries a private individual's email and GitHub profile in
    `title`, their location line in `company`, and the same details again in the 1,126
    characters of `text` -- so a field-level fix leaves the body republishing them.
    Re-publishing a third party's contact details is a decision, not a default.
    """
    return sum(bool(rx.search(text)) for rx in _HN_SEEKER_LABELS) >= 3


def _hn_rows(tree, out: list) -> None:
    for c in tree.get("children", []):
        text, secs = clean_with_sections(c.get("text"))
        parts = [p.strip() for p in text.split("|")]
        if len(parts) < 2 or not parts[0]:
            continue
        if _is_hn_seeker_post(text):
            continue
        # STRUCTURE BEFORE THE STRIP, or not at all -- the same invariant `sections`
        # ships for headers. An HN comment ends its header at a BLOCK TAG, not a pipe
        # and not a newline ("$180k-$230k<p>AI usage today is basic..."), and `clean`
        # flattens that tag into the same text run. So `parts[3]` above holds the whole
        # posting body, and no amount of trimming it afterwards recovers the boundary.
        # Splitting the DECODED markup here returns the header outright:
        # ['Portless', 'AI Engineer (Founding seat)', 'Remote (North America)', '$180k-$230k'].
        #
        # ONLY `location` reads this. `parts[0]`/`parts[1]` reproduce company and title
        # on 196 of 196 measured comments and are deliberately left alone, as is the
        # segment SCAN below -- narrowing the input to remote_type/employment_type is a
        # different change with a different blast radius.
        head_parts = [
            p.strip()
            for p in clean_with_sections(_HN_BLOCK.split(c.get("text") or "", 1)[0])[
                0
            ].split("|")
        ]
        # Fall back when the split leaves no location segment: a comment whose block tag
        # precedes its pipes would otherwise EMPTY a location that is currently
        # populated. 4 of 196 comments split short; 0 lose a location today, and this
        # keeps that true for a thread that formats differently next month.
        loc_parts = head_parts if len(head_parts) > 2 else parts
        hn_url, hn_recovered = _hn_url(c.get("text") or "", parts[0], text, c.get("id"))
        # SCAN the segments, do not index them. The convention is loose: measured
        # across 174 comments in one thread, the remote token lands in slot 2 (62x),
        # slot 3 (34x), slot 1 (18x) and slot 4 (7x) -- "PostHog | Full-Time |
        # Technical CSMs | REMOTE" puts an employment type where a title is expected.
        #
        # This matters beyond tidiness. HN set remote_type on NOTHING, so 40 ONSITE
        # and 19 HYBRID rows per thread fell through to remote_posting()'s text
        # matching -- which sees the word "remote" somewhere in a long comment and
        # passes them. On-site jobs were entering a remote-only harvest.
        rtype = rbasis = None
        etype = None
        for seg in parts:
            if rtype is None and (got := remote_type(seg)):
                rtype, rbasis = got, "text"
            if etype is None:
                norm, _ = vocab.employment_type(seg)
                if norm and norm != "OTHER":
                    etype = norm
        out.append(
            {
                "title": parts[1][:120],
                "company": parts[0][:80],
                "location": " ".join(
                    t for t in (_hn_location(p) for p in loc_parts[2:4]) if t
                ),
                "url": hn_url,
                # A link this adapter RECOVERED does not earn `direct_apply` from host
                # shape. Measured: of 5 rows the ATS-platform additions newly reach, 4
                # are 404/410 -- `applytojob.com` postings expire fast -- so promoting on
                # the host would assert "you can apply here" about a dead posting. That is
                # the same lie as the `direct_apply=False` this release fixes, pointed the
                # other way, and manufactured by the fix for it. Transient key, popped in
                # engine._consume alongside _blk/_nt.
                "_url_recovered": hn_recovered,
                **posted_from(c.get("created_at")),
                "remote_type": rtype,
                "remote_basis": rbasis,
                "source_extra": {"hn_author": c.get("author"), "hn_id": c.get("id")}
                if c.get("author")
                else None,
                "employment_type": etype
                or (
                    "contract"
                    if re.search(
                        r"contract|freelance|part.?time|fractional|1099", text, re.I
                    )
                    else ""
                ),
                "salary": salary_from_text(text),
                "text": text,
                "sections": secs,
                "source": "hn",
            }
        )


def _names(v):
    out = []
    for x in v or []:
        if isinstance(x, dict):
            out.append(x.get("name") or x.get("skill") or x.get("location") or "")
        else:
            out.append(str(x))
    return [s for s in out if s]


def _bt_rate(j):
    unit = {
        "hourly": "hr",
        "monthly": "mo",
        "annual": "yr",
        "fixed_price": "fixed",
    }.get(j.get("payment_type") or "", j.get("payment_type") or "")
    try:
        lo, hi = float(j.get("budget_minimum_usd")), float(j.get("budget_maximum_usd"))
    except (TypeError, ValueError):
        return ""
    if not hi:
        return ""
    return f"${lo:,.0f}/{unit}" if lo == hi else f"${lo:,.0f}-{hi:,.0f}/{unit}"


_BT_LABEL = re.compile(
    r"trainer|annotat|\bai training\b|evaluation|labeler|labelling|linguist|"
    r"\bvoice\b|transcrib|data collection|\bevaluator\b|quality analyst|"
    r"quality specialist|\bqa\b",
    re.I,
)


def search_braintrust(queries):
    """Braintrust freelance network -- a public, no-auth paginated job API. A gig
    lane with real hourly rates; low-paid AI-labeling crowdwork is filtered out."""
    out = []
    url = "https://app.usebraintrust.com/api/jobs/?limit=20"
    pages = 0
    while url and pages < 10:
        try:
            d = get_json(url)
        except NET_ERRORS:
            break
        for j in d.get("results", []):
            t = j.get("title") or ""
            if _BT_LABEL.search(t):
                continue
            # A five-digit integer is not a skill. Braintrust mixes opaque numeric
            # ids into both skill arrays -- 164 of its 227 tag tokens corpus-wide,
            # and every purely-numeric tag in the whole corpus is this source. They
            # were reaching TWO fields: `tags`, whose contract says "skills the
            # source itself extracted", and `text`, where they were interpolated
            # under the label "Skills:" and then read by relevant() and
            # score_and_signals(). Filtering here, ONCE and upstream of both, is why
            # this is a local rather than two separate guards -- cleaning `tags`
            # alone would have left the ids in the scored, searchable body on 42
            # live rows while reporting the defect fixed.
            skill_names = [
                s
                for s in _names(j.get("main_skills")) + _names(j.get("job_skills"))
                if not s.isdigit()
            ]
            skills = " ".join(skill_names)
            hrs = j.get("expected_hours_per_week")
            emp = j.get("employer") or {}
            text = f"{t}. Skills: {skills}. {j.get('contract_type', '')} contract" + (
                f", ~{hrs}h/wk." if hrs else "."
            )
            # ROUTED THROUGH THE SAME PATH AS EVERY OTHER BODY, which yields `[]` here
            # -- a built sentence has no markup and therefore no headers. It used to
            # skip this and emit `sections: null`, and the contract says `null` means
            # "there was no body to read" while `[]` means "a body with no headers".
            # There IS a body on 29 of 29 rows, so `null` was a false statement about
            # the posting, and the two states exist precisely so a consumer can tell
            # a missing body from an unstructured one.
            text, bt_secs = clean_with_sections(text)
            out.append(
                {
                    "title": t,
                    "company": emp.get("name", "") if isinstance(emp, dict) else "",
                    "location": (
                        " ".join(_names(j.get("locations"))) + " (Remote)"
                    ).strip(),
                    "url": f"https://app.usebraintrust.com/jobs/{j.get('id')}/",
                    # There is no prose body in this payload; `text` above is BUILT from
                    # the title and skills, ~157 chars on 29 of 29 rows. A consumer that
                    # reads it as a description is reading our sentence, not the
                    # employer's, and nothing else in the record says so.
                    "sections": bt_secs,
                    "text_basis": "synthesized",
                    **posted_from(j.get("created")),
                    # `level` was going into `department` -- a seniority filed as a
                    # category, one of the four meanings that made that column
                    # unusable downstream. It now says what it is.
                    # `level` DOES NOT EXIST in this payload -- 0 of 20 rows carry
                    # the key, so this mapping has been dead since it was written and
                    # the 20% seniority fill came from the title decomposition, not
                    # from Braintrust. `role.name` is a clean job FAMILY on 20/20 and
                    # `category` was empty.
                    "category": (j.get("role") or {}).get("name") or None,
                    "expires": to_date(j.get("deadline")),
                    "remote_type": "remote",  # a remote freelance network by definition
                    # `board`, NOT `stated`: nothing on the ROW says remote -- every posting
                    # here is remote because that is what this board is. Collapsing the two
                    # into one label is what the basis field exists to prevent.
                    "remote_basis": "board",
                    "tags": skill_names or None,  # numerics already dropped, above
                    "employment_type": f"contract ({j.get('contract_type', '')})".strip(),
                    "salary": _bt_rate(j),
                    # `payment_type` is annual|hourly|per_task (probed), and the
                    # budgets arrive as strings ("50000.00"). An hourly 42 and an
                    # annual 50000 in one column with no period makes every aggregate
                    # wrong, silently -- both are valid numbers.
                    **vocab.salary(
                        j.get("budget_minimum_usd"),
                        j.get("budget_maximum_usd"),
                        currency="USD",
                        period=j.get("payment_type"),
                    ),
                    "text": text,
                    "source": "braintrust",
                }
            )
        # Follow the API-supplied `next` only if it stays on Braintrust's own host
        # — never chase an arbitrary URL a response could point us at (SSRF guard).
        nxt = d.get("next")
        nxt = nxt.replace("http://", "https://") if nxt else None
        url = nxt if nxt and urlparse(nxt).hostname == "app.usebraintrust.com" else None
        pages += 1
        time.sleep(0.4)
    return out


_CC_NAME = {
    "US": "United States",
    "GB": "United Kingdom",
    "PL": "Poland",
    "DE": "Germany",
    "RO": "Romania",
    "CA": "Canada",
    "SG": "Singapore",
    "CO": "Colombia",
    "AR": "Argentina",
    "FR": "France",
    "ES": "Spain",
    "PT": "Portugal",
    "NL": "Netherlands",
    "IE": "Ireland",
    "SE": "Sweden",
    "DK": "Denmark",
    "NO": "Norway",
    "FI": "Finland",
    "IN": "India",
    "AU": "Australia",
    "JP": "Japan",
    "BR": "Brazil",
    "MX": "Mexico",
}


def _usajobs_grade(d: dict) -> str | None:
    """LowGrade/HighGrade + the pay plan -> "GS-13" / "GS-11/12"."""
    det = (d.get("UserArea") or {}).get("Details") or {}
    lo, hi = det.get("LowGrade"), det.get("HighGrade")
    plan = ((d.get("JobGrade") or [{}])[0] or {}).get("Code") or ""
    if not lo and not hi:
        return None
    band = f"{lo}/{hi}" if lo and hi and lo != hi else (lo or hi)
    return f"{plan}-{band}" if plan else band


def _usajobs_text(d: dict) -> tuple[str, list[dict]]:
    """The whole federal posting plus its sections, not just its summary.

    `JobSummary` averages 305 characters (probed 2026-08-05, n=25) while the content
    a scorer needs sits in sibling fields: MajorDuties 1,692, Evaluations 1,487,
    Requirements 322. Reading only the summary fed the fit score under a tenth of
    each posting, so federal roles scored near zero regardless of match -- the same
    shape as the Lever body bug. Several of these arrive as LISTS of paragraphs.
    """
    det = (d.get("UserArea") or {}).get("Details") or {}
    parts = []
    for k in ("JobSummary", "MajorDuties", "QualificationSummary", "Requirements",
              "Evaluations"):  # fmt: skip
        v = det.get(k)
        if isinstance(v, (list, tuple)):
            v = " ".join(str(x) for x in v if x)
        if v:
            parts.append(str(v))
    return clean_with_sections("\n".join(parts))


def _usajobs_remote(d: dict) -> dict:
    """USAJOBS row -> {remote_type, remote_basis, remote_areas}.

    `RemoteIndicator` on a FEDERAL posting means nationwide within the US -- the
    location display literally reads "Anywhere in the U.S. (remote job)" -- so the
    region is knowable rather than a guess.
    """
    ua = (d.get("UserArea") or {}).get("Details") or {}
    if "RemoteIndicator" not in ua and "TeleworkEligible" not in ua:
        return {"remote_type": None, "remote_basis": None, "remote_areas": None}
    if ua.get("RemoteIndicator") is True:
        return {
            "remote_type": "remote",
            "remote_basis": "stated",
            "remote_areas": ["US"],
        }
    rt = "hybrid" if ua.get("TeleworkEligible") is True else "onsite"
    return {"remote_type": rt, "remote_basis": "stated", "remote_areas": None}


def _usajobs_place(locations) -> dict:
    """USAJOBS `PositionLocation[]` -> {city, state, country}.

    Structured geography, already present, previously discarded in favour of the
    `PositionLocationDisplay` blob. One posting can list MANY locations (a federal
    role open in twelve cities), so this takes the first and leaves the full list in
    the display string rather than inventing a multi-value shape the contract does
    not yet have.
    """
    first = (locations or [{}])[0] if isinstance(locations, list) else {}
    if not isinstance(first, dict):
        return {"city": None, "state": None, "country": None}
    # Every one of these three needed normalizing, and the key names actively lie
    # (probed 2026-08-05, n=25): CityName is "New Orleans, Louisiana" -- the city
    # field carries the state too; CountrySubDivisionCode is a NAME ("Louisiana"),
    # not the code it claims; CountryCode is "United States". Passed through, this
    # source put a second vocabulary into all three columns.
    city = str(first.get("CityName") or "").split(",")[0].strip() or None
    return {
        "city": city,
        "state": vocab.us_state_code(first.get("CountrySubDivisionCode")),
        "country": country_code(first.get("CountryCode")),
    }


def search_usajobs(queries):
    """USAJOBS -- the US federal government's official jobs API (every field, not
    just tech). Free with a key + your email. Skipped gracefully if unset."""
    import urllib.request

    cfg = config.active()
    key, email = cfg.env("USAJOBS_API_KEY"), cfg.env("USAJOBS_EMAIL")
    if not (key and email):
        print("  usajobs: no USAJOBS_API_KEY/USAJOBS_EMAIL -- skipped")
        return []
    # THE SHARED PREDICATE, not a literal comparison. This read
    # `cfg.location.lower() != "remote"`, which is the exact drift adzuna's comment
    # already names as a bug it fixed -- and CLAUDE.md lists "one predicate for
    # remote-vs-place" as an invariant learned expensively. It matched only the
    # literal string: with `location` set to "anywhere", "any", "" or " remote ",
    # this built `&LocationName=%20remote%20` with NO RemoteIndicator, so the remote
    # filter silently never reached the API and an empty LocationName went out.
    is_place = not _is_remote_query(cfg)
    loc = f"&LocationName={q(cfg.location)}" if is_place else ""
    # USAJOBS Radius is in miles and only applies alongside a LocationName.
    rad = f"&Radius={cfg.radius_miles}" if (is_place and cfg.radius_miles > 0) else ""
    remote = "" if is_place else "&RemoteIndicator=True"
    rpp = max(1, getattr(cfg, "usajobs_results_per_page", 500))
    # This adapter built ONE url per query and never paged, so any keyword with more
    # than `rpp` matches was silently truncated -- and USAJOBS reports the true count
    # in `SearchResultCountAll`, which nothing read. Measured in catalog/usajobs.md:
    # "medical assistant" 736 and "registered nurse" 620 against a 500-row page, i.e.
    # 236 and 120 postings dropped, invisibly, on every run.
    #
    # The docs are explicit: "Specific pages are retrieved by passing the 'Page'
    # parameter with the number of the paged result desired" (worked example
    # ?Page=3&ResultsPerPage=50 -> results 151-200).
    max_pages = max(1, getattr(cfg, "usajobs_max_pages", 3))
    out: list[dict] = []
    for qy in queries:
        for page in range(1, max_pages + 1):
            url = (
                f"https://data.usajobs.gov/api/Search?Keyword={q(qy)}"
                f"&ResultsPerPage={rpp}&Page={page}{loc}{rad}{remote}"
            )
            req = urllib.request.Request(
                url,
                headers={
                    "Host": "data.usajobs.gov",
                    "User-Agent": email,
                    "Authorization-Key": key,
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=cfg.timeout) as r:
                    import json as _json

                    data = _json.loads(r.read().decode("utf-8", "replace"))
            except NET_ERRORS:
                break  # a dead page ends this query; other queries still run
            result = data.get("SearchResult") or {}
            _usajobs_rows(result, remote, out)
            got = len(result.get("SearchResultItems") or ())
            if got < rpp:
                break  # short page -> the tail of this keyword
            total = result.get("SearchResultCountAll")
            if isinstance(total, int) and page * rpp >= total:
                break  # the API told us the true total; believe it
            # Every other multi-call source pauses between requests. This one hits a
            # FEDERAL api with the largest page size in the codebase, so the pause
            # belongs between pages too, not only between queries.
            time.sleep(0.5)
        time.sleep(0.5)
    return out


def _usajobs_rows(result: dict, remote: str, out: list) -> None:
    """Map one USAJOBS page into `out` (split out so the paging loop reads as
    paging rather than as parsing)."""
    for it in result.get("SearchResultItems", []):
        d = it.get("MatchedObjectDescriptor") or {}
        pay = (d.get("PositionRemuneration") or [{}])[0]
        # HOISTED out of the dict literal below, which is the whole reason this line
        # exists: `_usajobs_text` returns a pair now, and writing `_usajobs_text(d)[0]`
        # inline would compile, pass every existing test, and silently discard the
        # sections for this source alone.
        _text, _secs = _usajobs_text(d)
        out.append(
            {
                "title": d.get("PositionTitle", ""),
                "company": d.get("OrganizationName", ""),
                "location": (
                    d.get("PositionLocationDisplay", "")
                    + (" (Remote)" if remote else "")
                ),
                "url": d.get("PositionURI", ""),
                **posted_from(d.get("PublicationStartDate")),
                # Every federal posting carries a close date (10/10 measured
                # 2026-08-05, some only days out) and it was being discarded. A job
                # that shut yesterday is worse than no job: it wastes the one thing
                # the user actually spends, which is the time to read and apply.
                "expires": to_date(d.get("ApplicationCloseDate")),
                # THE ONE PLACE THE 0.9.0 REMOVAL COST INFORMATION. `DepartmentName`
                # ("Department of Veterans Affairs") is the EMPLOYING DEPARTMENT, and
                # it rode in the deprecated `department` until that field was cut. Of
                # nineteen adapters this is the only one whose value is not recoverable
                # from the two keys below: `team` is `SubAgency`, a FACILITY (77 of 82
                # live federal rows carry one, e.g. "Central Virginia VA Health Care
                # System"), and `category` is the OPM series. `parent_company` was the
                # documented recovery and went earlier in the same release. So the
                # employing department is now simply unmapped here -- accepted, not
                # overlooked, and asserted in the parser test so it stays visible.
                # Pouring it into a category column is how a downstream store ended up
                # with employer names among its most common "categories"; the two keys
                # below say what each thing actually is.
                "team": d.get("SubAgency") or None,
                # OPM occupational series -- a real, coded job family.
                "category": ", ".join(
                    c.get("Name", "")
                    for c in (d.get("JobCategory") or [])
                    if c.get("Name")
                )
                or None,
                # The GRADE, not the pay plan. `JobGrade[].Code` is GS / ND / GG /
                # FV -- those are pay PLANS, not levels, and putting them in
                # `seniority` asserted something the value is not. The actual band is
                # LowGrade/HighGrade in UserArea.Details, present on 50/50 measured
                # ("13"-"13", "11"-"12"). Reported as "GS-13" or "GS-11/12", which is
                # how a federal applicant reads it.
                "seniority": _usajobs_grade(d),
                **_usajobs_place(d.get("PositionLocation")),
                # THE ROW'S OWN FIELDS, not our query parameter. This used to read
                # `"stated" if remote`, where `remote` is the string WE appended to
                # the request URL -- so the basis was reporting our own search, and
                # every row came back "remote/stated" simply because we had asked for
                # remote. Probed 2026-08-05 (n=25, plain query): every posting carries
                # UserArea.Details.RemoteIndicator (False on 25/25 there) and
                # TeleworkEligible (True on 13, False on 12), so the fact was
                # available per row the whole time.
                #
                # TeleworkEligible -> hybrid is the one judgment here: a federal role
                # that is telework-eligible but not remote is partly-in-office, which
                # is what `hybrid` means. The basis stays `stated` because basis names
                # WHERE a value came from -- a vendor field -- not how certain it is.
                **_usajobs_remote(d),
                # OPM occupational series and pay grade are real federal identifiers
                # -- better join keys than the label already in `category` -- and no
                # other source has anything like them. One-source fields belong in
                # source_extra, not in a core column 18 adapters leave empty.
                "source_extra": {
                    k: v
                    for k, v in (
                        (
                            "opm_series",
                            ",".join(
                                c.get("Code", "")
                                for c in (d.get("JobCategory") or [])
                                if c.get("Code")
                            ),
                        ),
                        ("pay_grade", (d.get("JobGrade") or [{}])[0].get("Code")),
                        ("announcement", d.get("PositionID")),
                    )
                    if v
                }
                or None,
                # `.Code`, not `.Name`. The name is EMPTY on 47 of 50 rows and a
                # shift pattern on the rest, so not one row in 50 produced a usable
                # employment type; the code is present on 50/50.
                "employment_type": vocab.USAJOBS_SCHEDULE.get(
                    (d.get("PositionSchedule") or [{}])[0].get("Code", ""), ""
                ),
                "employment_type_raw": (d.get("PositionSchedule") or [{}])[0].get(
                    "Name"
                )
                or None,
                "salary": salary_range(
                    pay.get("MinimumRange"), pay.get("MaximumRange")
                ),
                # PROBED 2026-08-05: {MinimumRange, MaximumRange, RateIntervalCode,
                # Description}. There is NO currency field -- these are US federal
                # postings, so USD is structural rather than stated. The period comes
                # from RateIntervalCode ("PA"), with Description ("Per Year") as a
                # readable fallback; only PA was seen live, the other OPM codes are
                # unverified. Ranges arrive as STRINGS ("105053").
                **vocab.salary(
                    pay.get("MinimumRange"),
                    pay.get("MaximumRange"),
                    currency="USD",
                    period=pay.get("RateIntervalCode") or pay.get("Description"),
                ),
                "text": _text,
                "sections": _secs,
                "source": "usajobs",
            }
        )


# The Muse hard-caps at page 99 (page 100 is a 400 "Value page is too high"), so one
# query reaches at most 100 x 20 = 2,000 rows however many it advertises -- `page_count`
# said 20,223 when this was measured on 2026-08-03, which is a count of pages that do
# not exist. Its ~36,000 real rows are only reachable by fanning out over the 19
# 20 categories, whose slices are nearly disjoint (19 dupes in 1,138 sampled).
#
# The default here is deliberately small. A full 19-category fan-out to the cap is
# ~2,000 requests, which is a harvest of its own rather than one source among nineteen;
# widen it with THEMUSE_MAX_PAGES when The Muse is the point of the run.
#
# It is also NOT sorted by date (page 0 median 420d, page 90 median 45d), so the
# freshness cut has to happen at ingest -- you cannot page to the fresh part.
# max(1, ...) like adzuna_pages and usajobs_max_pages: a cap of 0 would make zero
# requests and return [], which is indistinguishable from an empty source.
THEMUSE_PAGE_CAP = 99


# The 20-value taxonomy, verbatim from catalog/themuse.md. "Unknown" is a real
# category value in the API, not a placeholder -- it is where The Muse files a job it
# could not classify, so dropping it would silently lose rows.
# VERIFIED 2026-08-05, all 20, one probe each: every value returns a page_count
# distinct from the unfiltered feed (20,287) and 20/20 rows carrying that exact
# category. This mattered because The Muse SILENTLY IGNORES an unrecognised parameter
# value and serves the generic feed instead -- so an unverified slice would look
# healthy while being a copy of the others, the same silent-truncation class the
# fan-out exists to fix. Per-slice page_counts ranged Animal Care 3 to Software
# Engineering 5,028; "Unknown" is real (1,058) and is where The Muse files a job it
# could not classify.
THEMUSE_CATEGORIES = (
    "Account Management",
    "Accounting and Finance",
    "Advertising and Marketing",
    "Animal Care",
    "Business Operations",
    "Data and Analytics",
    "Education",
    "Food and Hospitality Services",
    "Healthcare",
    "Human Resources and Recruitment",
    "Installation, Maintenance, and Repairs",
    "Legal Services",
    "Management",
    "Product Management",
    "Project Management",
    "Retail",
    "Sales",
    "Science and Engineering",
    "Software Engineering",
    "Unknown",
)


def search_themuse(queries):
    """The Muse -- keyless, and the least tech-skewed source here (11% tech titles
    when measured), which is the reason to carry it at all.

    `queries` is accepted and IGNORED: The Muse has no title search, verified across
    nine parameter names, so it is a harvest-lane source only. Passing a query would
    silently return the unfiltered set, which is the failure this docstring exists to
    prevent.

    FANS OUT over the 20-value category taxonomy, and that is the whole yield of this
    adapter. The unfiltered feed hard-caps at page 99 = 2,000 rows, and this adapter
    used to page ONLY that feed at themuse_max_pages=5 -- **100 rows out of ~36,060
    reachable.** The cap applies PER SLICE, and the slices are near-disjoint, so
    querying each category is the only way past 2,000.

    What blocked this for so long was a wrong entry in our own catalog claiming
    category filtering was unreliable. Re-measured 2026-08-05: `category=Healthcare`
    returns 20/20 Healthcare rows with zero overlap against the unfiltered page. The
    trap has been corrected in catalog/themuse.md; do not re-add it.

    Cost is themuse_max_pages x 20 categories requests per run (default 5 -> 100
    requests, ~2,000 rows). `seen` dedups across slices because a job can carry two
    categories.
    """
    out: list[dict] = []
    seen: set = set()
    pages = min(_depth("themuse_max_pages"), THEMUSE_PAGE_CAP + 1)
    for category in THEMUSE_CATEGORIES:
        for page in range(pages):
            try:
                data = get_json(
                    "https://www.themuse.com/api/public/jobs"
                    f"?page={page}&category={q(category)}"
                )
            except NET_ERRORS:
                break  # a dead slice ends this category; the others still run
            except Exception:  # noqa: BLE001
                break
            results = data.get("results") or []
            if not results:
                break  # this category is exhausted
            _themuse_rows(results, seen, out)
            time.sleep(0.2)  # be polite between pages of one slice
    return out


def _themuse_rows(results, seen: set, out: list) -> None:
    """Map one page of Muse results into `out`, skipping ids already seen.

    Cross-slice dedup is required, not defensive: a posting can carry several
    categories, so the same job legitimately appears in more than one fan-out slice.
    """
    for j in results:
        jid = j.get("id")
        # An id-less row must not poison the set: adding None once would make every
        # LATER id-less row look like a duplicate and silently drop it.
        if jid is not None:
            if jid in seen:
                continue
            seen.add(jid)
        locs = [
            x.get("name", "") for x in (j.get("locations") or []) if isinstance(x, dict)
        ]
        cats = [
            x.get("name", "")
            for x in (j.get("categories") or [])
            if isinstance(x, dict)
        ]
        # The Muse ships BOTH forms of the level in the same object --
        # {'name': 'Mid Level', 'short_name': 'mid'} -- and this read only `name`,
        # the display string, throwing the vendor's own canonical token away. That
        # is the same class of defect as reading a description's plain-text field
        # when the structured one is right beside it. `short_name` is what goes in
        # `seniority` (it needs no interpretation from us and is already the form a
        # consumer can filter on); `name` is what the vendor displays, so it is the
        # raw. Falls back to `name` if a row ever omits the token.
        levels = [x for x in (j.get("levels") or []) if isinstance(x, dict)]
        level_tokens = [x.get("short_name") or x.get("name") or "" for x in levels]
        level_names = [x.get("name") or "" for x in levels]
        text, secs = clean_with_sections(j.get("contents", ""))
        out.append(
            {
                "title": j.get("name", ""),
                "company": (j.get("company") or {}).get("name", ""),
                "location": "; ".join(x for x in locs if x),
                # The Muse sends locations only as "Waco, TX" display strings -- no
                # structured geography anywhere in the payload -- so the parse is the
                # only way this source fills city/state. FIRST location only, matching
                # every other multi-location adapter; `locations` keeps the rest.
                **split_place(next((x for x in locs if x), "")),
                "url": (j.get("refs") or {}).get("landing_page", ""),
                **posted_from(j.get("publication_date")),
                # A job FAMILY ("Data Science"), not an org unit -- see
                # catalog/_SCHEMA.md on why that distinction is load-bearing.
                "category": "; ".join(x for x in cats if x) or None,
                # `levels` is a real seniority string ("Senior Level"). It was held
                # back while `seniority` was a strand-B key not yet on any adapter;
                # the contract exists now, so it ships.
                "seniority": "; ".join(x for x in level_tokens if x) or None,
                "seniority_raw": "; ".join(x for x in level_names if x) or None,
                # DELIBERATELY EMPTY, not removed: `_assert_contract` requires every
                # adapter to emit the key, and `_NULLABLE_TEXT` turns "" into None at
                # the boundary, so this is how an adapter says "this API has no such
                # field". Before, it read `type`, which is
                # The Muse's posting-PROVENANCE flag: it is the literal string
                # "external" on 20 of 20 rows probed live 2026-08-20 (with a sibling
                # `model_type` beside it), so every Muse row normalized to OTHER --
                # 216 of 216 in the last harvest, and the single largest contributor
                # to that bucket. catalog/themuse.md records `employment_type: null`
                # for this API and has been right since it was written; the code was
                # reading a field the profile says does not exist. Emitting nothing is
                # the honest answer, and it is the second time in this repo that the
                # catalog was correct while an adapter was not.
                "employment_type": "",
                "salary": salary_from_text(text),
                "text": text,
                "sections": secs,
                "source": "themuse",
            }
        )


BREADTH_ALL = {
    "remotive": search_remotive,
    "usajobs": search_usajobs,
    "jobicy": search_jobicy,
    "arbeitnow": search_arbeitnow,
    "remoteok": search_remoteok,
    "himalayas": search_himalayas,
    "adzuna": search_adzuna,
    "google_jobs": search_google_jobs,
    "hn": search_hn_whoishiring,
    "braintrust": search_braintrust,
    "themuse": search_themuse,
}


def enabled_depth(cfg):
    """Depth adapters to run. cfg.depth_sources None = all of them (the registry above
    is the single source of truth); a list selects a subset and silently ignores names
    this build doesn't have."""
    if cfg.depth_sources is None:
        return dict(DEPTH_ALL)
    return {k: DEPTH_ALL[k] for k in cfg.depth_sources if k in DEPTH_ALL}


def enabled_breadth(cfg):
    """Breadth sources to run. Same contract as enabled_depth: None = all registered."""
    if cfg.breadth_sources is None:
        return list(BREADTH_ALL.items())
    return [(k, BREADTH_ALL[k]) for k in cfg.breadth_sources if k in BREADTH_ALL]
