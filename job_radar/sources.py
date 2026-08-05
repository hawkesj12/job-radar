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
import os
import re
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from . import config
from . import vocab
from .vocab import remote_type
from .util import (
    NET_ERRORS,
    age_int,
    clean,
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


def fetch_greenhouse(slug: str):
    data = get_json(f"{GREENHOUSE_API}/{slug}/jobs?content=true")
    out = []
    for j in data.get("jobs", []):
        text = clean(j.get("content", ""))
        depts = j.get("departments") or []
        out.append(
            {
                "title": j.get("title", ""),
                "location": (j.get("location") or {}).get("name", ""),
                "url": j.get("absolute_url", ""),
                "posted": to_date(j.get("updated_at") or j.get("first_published")),
                # Present on every Greenhouse posting and NULL on all 397 of the
                # board measured 2026-08-05 -- the key exists, the data usually does
                # not. Mapped anyway: it costs nothing, other boards may fill it, and
                # to_date returns "" for a null so an absent deadline stays absent.
                "expires": to_date(j.get("application_deadline")),
                "department": depts[0].get("name", "") if depts else "",
                "team": (depts[0].get("name") if depts else None) or None,
                "employment_type": "",
                "salary": salary_from_text(text),
                "text": text,
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


def fetch_lever(slug: str):
    data = get_json(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    out = []
    for j in data:
        cats = j.get("categories") or {}
        text = clean(j.get("descriptionPlain") or j.get("description", ""))
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
                "url": j.get("hostedUrl", ""),
                "posted": to_date(j.get("createdAt")),
                "department": cats.get("team") or cats.get("department", ""),
                "team": cats.get("team") or cats.get("department") or None,
                # `workplaceType` is a real Lever field ("remote"/"hybrid"/"onsite")
                # that this adapter never read -- remoteness was being re-derived from
                # the description while the source stated it outright.
                **_lever_remote(j.get("workplaceType")),
                "employment_type": cats.get("commitment", ""),
                **pay,
                "salary": salary,
                "text": text,
            }
        )
    return out


def _ashby_place(address) -> dict:
    """Ashby `address.postalAddress` -> {city, state, country}. schema.org shape:
    addressLocality / addressRegion / addressCountry."""
    pa = (address or {}).get("postalAddress") if isinstance(address, dict) else None
    if not isinstance(pa, dict):
        return {"city": None, "state": None, "country": None}
    return {
        "city": pa.get("addressLocality") or None,
        "state": pa.get("addressRegion") or None,
        "country": pa.get("addressCountry") or None,
    }


def fetch_ashby(slug: str):
    data = get_json(
        f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"
    )
    out = []
    for j in data.get("jobs", []):
        loc = j.get("location", "")
        if j.get("isRemote"):
            loc = (loc + " (Remote)").strip()
        text = clean(j.get("descriptionPlain", ""))
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
                "posted": to_date(
                    j.get("publishedAt") or j.get("updatedAt") or j.get("publishedDate")
                ),
                "department": j.get("department", "") or j.get("team", ""),
                "team": j.get("department") or j.get("team") or None,
                # `isRemote` is a real boolean on every Ashby posting. Structured
                # geography lives at address.postalAddress -- present in 657 of 737
                # measured, and never read until now.
                "remote_type": _rt(j.get("isRemote")),
                "remote_basis": "stated" if "isRemote" in j else None,
                **_ashby_place(j.get("address")),
                "employment_type": j.get("employmentType", ""),
                "salary": salary or salary_from_text(text),
                "text": text,
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
SMARTRECRUITERS_MAX_PAGES = max(
    1, int(os.environ.get("SMARTRECRUITERS_MAX_PAGES", "10"))
)
SMARTRECRUITERS_PAGE = 100  # the API's max; larger values are silently clamped


def fetch_smartrecruiters(slug: str):
    out: list[dict] = []
    for page in range(SMARTRECRUITERS_MAX_PAGES):
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
                "posted": to_date(j.get("releasedDate") or j.get("createdOn")),
                "department": (j.get("department") or {}).get("label", ""),
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
                "country": loc.get("country") or None,
                "remote_type": _rt(loc.get("remote"), loc.get("hybrid")),
                "remote_basis": "stated" if "remote" in loc else None,
                "employment_type": (j.get("typeOfEmployment") or {}).get("label", ""),
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
        loc = j.get("location") or {}
        parts = [loc.get("city", ""), loc.get("region", ""), loc.get("country", "")]
        loctext = ", ".join(p for p in parts if p)
        if loc.get("telecommuting") or j.get("telecommuting"):
            loctext = (loctext + " (Remote)").strip()
        text = clean(j.get("description", ""))
        out.append(
            {
                "title": j.get("title", "") or j.get("full_title", ""),
                "location": loctext,
                "url": j.get("application_url")
                or j.get("url")
                or f"https://apply.workable.com/{slug}/j/{j.get('shortcode', '')}/",
                "posted": to_date(j.get("created_at") or j.get("published_on")),
                "department": j.get("department", ""),
                "employment_type": j.get("employment_type", ""),
                "salary": salary_from_text(text),
                "text": text,
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
WORKDAY_MAX_PAGES = int(os.environ.get("WORKDAY_MAX_PAGES", "25"))
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
WORKDAY_FETCH_DETAILS = os.environ.get("WORKDAY_FETCH_DETAILS", "1") not in (
    "0",
    "false",
    "no",
)
WORKDAY_DETAIL_WORKERS = int(os.environ.get("WORKDAY_DETAIL_WORKERS", "8"))
_ET = ZoneInfo("America/New_York")  # every date in job-radar is Eastern
_WD_POSTED = re.compile(r"Posting Date:\s*(\d{1,2})/(\d{1,2})/(\d{4})")
_WD_RELATIVE = re.compile(r"Posted\s+(\d+)\+?\s+(day|week|month)s?\s+ago", re.I)
_WD_TODAY = re.compile(r"Posted\s+(today|yesterday)", re.I)


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
HIMALAYAS_MAX_PAGES = max(1, int(os.environ.get("HIMALAYAS_MAX_PAGES", "10")))
HIMALAYAS_PAGE = 20  # the API's own page size on this endpoint
# The browse lane's budget. THIS is what bounds it -- 50 pages x 20 = 1,000 of the
# ~97,000 rows in the corpus. An earlier version of this comment said freshness was
# the budget and the cap was only a backstop; that was wrong, and the arithmetic says
# so: a 60-day row (the default max_age_days) sits near offset 130,000, which this cap
# cannot reach, so the age stop in _himalayas_browse is a secondary guard that only
# fires on a short window. Worth having anyway because browse is date-ordered: these
# are the NEWEST 1,000, not an arbitrary slice.
HIMALAYAS_BROWSE_PAGES = max(1, int(os.environ.get("HIMALAYAS_BROWSE_PAGES", "50")))


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


def _is_aggregator(url: str) -> bool:
    """Does this apply link land on an aggregator rather than the employer?"""
    host = urlparse(url or "").netloc.lower()
    return bool(host) and any(agg in host for agg in _G_AGGREGATORS)


def _best_apply_link(apply_options: list, fallback: str = "") -> str:
    """Pick the direct-to-employer apply link from Google's apply_options, else the
    first option, else `fallback`. Google orders these by its own preference, so the
    first non-aggregator link is the best direct-to-company URL available."""
    links = [o.get("link", "") for o in (apply_options or []) if o.get("link")]
    for link in links:
        host = urlparse(link).netloc.lower()
        if not any(agg in host for agg in _G_AGGREGATORS):
            return link  # a direct careers/ATS link — jobfitr's preferred target
    return links[0] if links else fallback


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
    out = []
    for qy in queries:
        token = ""
        for _ in range(pages):
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
                text = clean(j.get("description", ""))
                out.append(
                    {
                        "title": j.get("title", ""),
                        "company": j.get("company_name", ""),
                        "location": j.get("location", ""),
                        "url": (
                            _url := _best_apply_link(
                                j.get("apply_options"), j.get("share_link", "")
                            )
                        ),
                        # _best_apply_link ALREADY made this judgement to pick the
                        # url -- it prefers the first non-aggregator option. Saying
                        # so costs one call and turns an internal preference into a
                        # fact a consumer can filter on.
                        "direct_apply": not _is_aggregator(_url),
                        "posted": _google_posted(ext.get("posted_at", "")),
                        # ALWAYS relative -- Google states recency as "2 days ago",
                        # never a date. The absolute value above is arithmetic done
                        # at fetch time, and a consumer sorting by freshness deserves
                        # to know that rather than trusting it like a timestamp.
                        "posted_basis": "relative" if ext.get("posted_at") else None,
                        "department": "",
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

    Returns at most WORKDAY_MAX_PAGES x WORKDAY_PAGE roles, silently truncated -- see
    the cap comment above.
    """
    base = f"https://{slug}.{host}.myworkdayjobs.com/wday/cxs/{slug}/{site}"
    out: list[dict] = []
    offset, total = 0, None
    for _ in range(WORKDAY_MAX_PAGES):
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
            posted = ""
            for b in j.get("bulletFields") or []:
                m = _WD_POSTED.search(str(b))
                if m:
                    mo, day, yr = m.groups()
                    posted = f"{yr}-{int(mo):02d}-{int(day):02d}"
                    break
            if not posted:
                # Only SOME tenants put an absolute date in bulletFields; the rest
                # expose just 'Posted 26 Days Ago'. Derive the date from it rather
                # than leaving posted empty — a blank date sinks the role in any
                # freshness filter, which would silently bury whole employers.
                posted = _relative_posted(j.get("postedOn", ""))
            out.append(
                {
                    "title": j.get("title", ""),
                    "location": j.get("locationsText", ""),
                    "url": f"https://{slug}.{host}.myworkdayjobs.com/en-US/{site}{path}",
                    "posted": posted,
                    "department": "",
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
    if WORKDAY_FETCH_DETAILS and out:
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
        if _DETAIL_POOL is None or _DETAIL_POOL_SIZE != WORKDAY_DETAIL_WORKERS:
            if _DETAIL_POOL is not None:
                _DETAIL_POOL.shutdown(wait=False)
            _DETAIL_POOL = ThreadPoolExecutor(
                max_workers=WORKDAY_DETAIL_WORKERS,
                thread_name_prefix="wd-detail",
            )
            _DETAIL_POOL_SIZE = WORKDAY_DETAIL_WORKERS
        return _DETAIL_POOL


@atexit.register
def _shutdown_detail_pool() -> None:
    if _DETAIL_POOL is not None:
        _DETAIL_POOL.shutdown(wait=False)


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
        text = clean(info.get("jobDescription", "") or "")
        if text:
            r["text"] = text
            r["salary"] = r["salary"] or salary_from_text(text)
        # startDate is a real ISO date; prefer it over anything derived from a
        # relative string when the detail call gives us one.
        if info.get("startDate"):
            r["posted"] = to_date(info["startDate"])
        if info.get("timeType"):
            r["employment_type"] = info["timeType"]

    list(_detail_pool().map(_one, rows))


# Rippling's LIST endpoint returns five fields and no body or date -- those live on a
# per-job detail call, exactly like Workday. Same reasoning applies (a body-less job is
# unrankable and unreadable), so details are ON by default and this is the expensive
# half: Rippling's own board is 748 roles, i.e. 1 list + 748 detail requests. Set
# RIPPLING_FETCH_DETAILS=0 for a tight harvest window. Discovery never pays it --
# live_rippling below answers "is this board real" from the list alone.
RIPPLING_FETCH_DETAILS = os.environ.get("RIPPLING_FETCH_DETAILS", "1") not in (
    "0",
    "false",
    "no",
)
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
        row["text"] = clean(
            " ".join(x for x in (desc.get("role"), desc.get("company")) if x)
        )
    elif isinstance(desc, str):
        row["text"] = clean(desc)
    row["posted"] = to_date(d.get("createdOn")) or row["posted"]
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

    Bodies and dates are NOT on the list endpoint; see RIPPLING_FETCH_DETAILS above
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
                "department": dept.get("label", "") if isinstance(dept, dict) else "",
                "employment_type": "",
                "salary": "",
                "text": "",
            }
        )
    if keep is not None:  # gate before the bodies — see the docstring
        out = [r for r in out if keep(_title_of(r))]
    if RIPPLING_FETCH_DETAILS and out:
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
        jp = j.get("_jobposting") or {}
        loc = ""
        place = jp.get("jobLocation") if isinstance(jp, dict) else None
        if isinstance(place, dict):
            addr = place.get("address") or {}
            if isinstance(addr, dict):
                parts = [
                    addr.get("addressLocality", ""),
                    addr.get("addressRegion", ""),
                    addr.get("addressCountry", ""),
                ]
                loc = ", ".join(str(p) for p in parts if p)
        if isinstance(jp, dict) and jp.get("jobLocationType") == "TELECOMMUTE":
            loc = (loc + " (Remote)").strip()
        text = clean(
            j.get("content_html", "")
            or (jp.get("description", "") if isinstance(jp, dict) else "")
        )
        out.append(
            {
                "title": j.get("title", ""),
                "location": loc,
                "url": j.get("url", ""),
                "posted": to_date(j.get("date_published")),
                "department": "",
                "employment_type": (
                    jp.get("employmentType", "") if isinstance(jp, dict) else ""
                )
                or "",
                "salary": salary_from_text(text),
                "text": text,
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
        text = clean(j.get("description", ""))
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
                "url": j.get("url", ""),
                "posted": to_date(j.get("publication_date")),
                "department": j.get("category", ""),
                "category": j.get("category") or None,
                "remote_type": "remote",  # a remote-only board by definition
                "remote_basis": "stated",
                "tags": [t for t in (j.get("tags") or []) if t] or None,
                "employment_type": j.get("job_type", ""),
                "salary": j.get("salary", "") or salary_from_text(text),
                "text": text,
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
        text = clean(j.get("jobDescription") or j.get("jobExcerpt", ""))
        out.append(
            {
                "title": j.get("jobTitle", ""),
                "company": j.get("companyName", ""),
                "location": (j.get("jobGeo") or "") + " (Remote)",
                "url": j.get("url", ""),
                "posted": to_date(j.get("pubDate")),
                "department": _joined(j.get("jobIndustry")),
                "category": _joined(j.get("jobIndustry")) or None,
                "seniority": _joined(j.get("jobLevel")) or None,
                "remote_type": "remote",  # a remote-only board by definition
                "remote_basis": "stated",
                "employment_type": _joined(j.get("jobType")),
                "salary": salary_from_text(text),
                "text": text,
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
        text = clean(j.get("description", ""))
        jt = j.get("job_types")
        out.append(
            {
                "title": j.get("title", ""),
                "company": j.get("company_name", ""),
                "location": (j.get("location") or "") + " (Remote)",
                "url": j.get("url", ""),
                "posted": to_date(j.get("created_at")),
                "department": "",
                "remote_type": "remote",  # the adapter already filtered on j["remote"] above
                "remote_basis": "stated",
                "tags": [t for t in (j.get("tags") or []) if t] or None,
                "employment_type": ", ".join(jt)
                if isinstance(jt, list)
                else (jt or ""),
                "salary": salary_from_text(text),
                "text": text,
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
        text = clean(j.get("description", ""))
        out.append(
            {
                "title": j.get("position", ""),
                "company": j.get("company", ""),
                "location": (j.get("location") or "") + " (Remote)",
                "url": j.get("url") or j.get("apply_url", ""),
                "posted": to_date(j.get("date") or j.get("epoch")),
                "department": "",
                "employment_type": "",
                "remote_type": "remote",  # a remote-only board by definition
                "remote_basis": "stated",
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
        for page in range(1, HIMALAYAS_MAX_PAGES + 1):
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

    WHAT ACTUALLY BOUNDS IT: `HIMALAYAS_BROWSE_PAGES` (default 50 = 1,000 rows). Be
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
    for page in range(HIMALAYAS_BROWSE_PAGES):
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
        text = clean(j.get("description") or j.get("excerpt", ""))
        regions = j.get("locationRestrictions") or []
        loc = (", ".join(regions) if regions else "") + " (Remote)"
        out.append(
            {
                "title": j.get("title", ""),
                "company": j.get("companyName", ""),
                "location": loc.strip(),
                "url": url,
                "posted": to_date(j.get("pubDate")),
                "department": "",
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
                "remote_basis": "stated",
                "tags": [x for x in (j.get("categories") or []) if isinstance(x, str)]
                or None,
                "employment_type": j.get("employmentType", ""),
                "salary": salary_range(j.get("minSalary"), j.get("maxSalary")),
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
                "source": "himalayas",
            }
        )


def _adzuna_pay(j: dict) -> dict:
    """Adzuna salary -> the commitment columns OR the estimate columns, never both."""
    lo, hi = j.get("salary_min"), j.get("salary_max")
    if str(j.get("salary_is_predicted")) == "1":

        def _n(v):
            try:
                return float(v) or None
            except (TypeError, ValueError):
                return None

        return {"salary_estimated_min": _n(lo), "salary_estimated_max": _n(hi)}
    return vocab.salary(lo, hi, currency="USD", period=None, basis="stated")


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
    Returns None (not "") for anything the array does not contain: unknown is not
    empty.
    """
    if not isinstance(area, list) or not area:
        return None, None, None, None
    country = area[0] or None
    if len(area) == 1:
        return None, None, country, True  # nationwide == remote
    state = area[1] or None
    city = area[-1] if len(area) >= 4 else None
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
                text = clean(j.get("description", ""))
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
                        "posted": to_date(j.get("created")),
                        "expires": to_date(j.get("deadline")),  # 1 of 20 populated
                        "department": (j.get("category") or {}).get("label", ""),
                        # `category.label` is a JOB FAMILY ("IT Jobs"), not an org unit
                        # -- one of the four different things `department` carried.
                        "category": (j.get("category") or {}).get("label") or None,
                        "city": city,
                        "state": state,
                        "country": country,
                        "remote_type": "remote" if is_remote else None,
                        "remote_basis": "location" if is_remote else None,
                        "employment_type": j.get("contract_time", ""),
                        "salary": salary_range(
                            j.get("salary_min"), j.get("salary_max")
                        ),
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
HN_THREADS = max(1, int(os.environ.get("HN_THREADS", "2")))


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
        :HN_THREADS
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


def _hn_rows(tree, out: list) -> None:
    for c in tree.get("children", []):
        text = clean(c.get("text"))
        parts = [p.strip() for p in text.split("|")]
        if len(parts) < 2 or not parts[0]:
            continue
        m = re.search(r"https?://[^\s)\]]+", text)
        out.append(
            {
                "title": parts[1][:120],
                "company": parts[0][:80],
                "location": " ".join(parts[2:4]),
                "url": m.group(0)
                if m
                else f"https://news.ycombinator.com/item?id={c.get('id')}",
                "posted": to_date(c.get("created_at")),
                "department": "",
                "employment_type": "contract"
                if re.search(
                    r"contract|freelance|part.?time|fractional|1099", text, re.I
                )
                else "",
                "salary": salary_from_text(text),
                "text": text,
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
            skills = " ".join(
                _names(j.get("main_skills")) + _names(j.get("job_skills"))
            )
            hrs = j.get("expected_hours_per_week")
            emp = j.get("employer") or {}
            text = f"{t}. Skills: {skills}. {j.get('contract_type', '')} contract" + (
                f", ~{hrs}h/wk." if hrs else "."
            )
            out.append(
                {
                    "title": t,
                    "company": emp.get("name", "") if isinstance(emp, dict) else "",
                    "location": (
                        " ".join(_names(j.get("locations"))) + " (Remote)"
                    ).strip(),
                    "url": f"https://app.usebraintrust.com/jobs/{j.get('id')}/",
                    "posted": to_date(j.get("created")),
                    # `level` was going into `department` -- a seniority filed as a
                    # category, one of the four meanings that made that column
                    # unusable downstream. It now says what it is.
                    "department": "",
                    "seniority": j.get("level") or None,
                    "remote_type": "remote",  # a remote freelance network by definition
                    "remote_basis": "stated",
                    "tags": _names(j.get("main_skills")) + _names(j.get("job_skills"))
                    or None,
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
    return {
        "city": first.get("CityName") or None,
        "state": first.get("CountrySubDivisionCode") or None,
        "country": first.get("CountryCode") or None,
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
    is_place = cfg.location.lower() != "remote"
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
    out = []
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
        out.append(
            {
                "title": d.get("PositionTitle", ""),
                "company": d.get("OrganizationName", ""),
                "location": (
                    d.get("PositionLocationDisplay", "")
                    + (" (Remote)" if remote else "")
                ),
                "url": d.get("PositionURI", ""),
                "posted": to_date(d.get("PublicationStartDate")),
                # Every federal posting carries a close date (10/10 measured
                # 2026-08-05, some only days out) and it was being discarded. A job
                # that shut yesterday is worse than no job: it wastes the one thing
                # the user actually spends, which is the time to read and apply.
                "expires": to_date(d.get("ApplicationCloseDate")),
                # PRESERVED byte-identical (deprecated, removed at 1.0) -- but it
                # was never a department. "Department of Veterans Affairs" is the
                # EMPLOYER, and pouring it into a category column is how a
                # downstream store ended up with employer names among its most
                # common "categories". The three keys below say what each thing is.
                "department": d.get("DepartmentName", ""),
                "parent_company": d.get("DepartmentName") or None,
                "team": d.get("SubAgency") or None,
                # OPM occupational series -- a real, coded job family.
                "category": ", ".join(
                    c.get("Name", "")
                    for c in (d.get("JobCategory") or [])
                    if c.get("Name")
                )
                or None,
                "seniority": ", ".join(
                    g.get("Code", "")
                    for g in (d.get("JobGrade") or [])
                    if g.get("Code")
                )
                or None,
                **_usajobs_place(d.get("PositionLocation")),
                "remote": True if remote else None,
                "remote_basis": "source_field" if remote else None,
                "employment_type": ", ".join(
                    s.get("Name", "") for s in (d.get("PositionSchedule") or [])
                ),
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
                "text": clean(
                    (d.get("UserArea") or {}).get("Details", {}).get("JobSummary", "")
                ),
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
THEMUSE_MAX_PAGES = max(1, int(os.environ.get("THEMUSE_MAX_PAGES", "5")))
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
    used to page ONLY that feed at THEMUSE_MAX_PAGES=5 -- **100 rows out of ~36,060
    reachable.** The cap applies PER SLICE, and the slices are near-disjoint, so
    querying each category is the only way past 2,000.

    What blocked this for so long was a wrong entry in our own catalog claiming
    category filtering was unreliable. Re-measured 2026-08-05: `category=Healthcare`
    returns 20/20 Healthcare rows with zero overlap against the unfiltered page. The
    trap has been corrected in catalog/themuse.md; do not re-add it.

    Cost is THEMUSE_MAX_PAGES x 20 categories requests per run (default 5 -> 100
    requests, ~2,000 rows). `seen` dedups across slices because a job can carry two
    categories.
    """
    out, seen = [], set()
    pages = min(THEMUSE_MAX_PAGES, THEMUSE_PAGE_CAP + 1)
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
        levels = [
            x.get("name", "") for x in (j.get("levels") or []) if isinstance(x, dict)
        ]
        text = clean(j.get("contents", ""))
        out.append(
            {
                "title": j.get("name", ""),
                "company": (j.get("company") or {}).get("name", ""),
                "location": "; ".join(x for x in locs if x),
                "url": (j.get("refs") or {}).get("landing_page", ""),
                "posted": to_date(j.get("publication_date")),
                # A job FAMILY ("Data Science"), not an org unit -- see
                # catalog/_SCHEMA.md on why that distinction is load-bearing.
                "department": "; ".join(x for x in cats if x),
                "category": "; ".join(x for x in cats if x) or None,
                # `levels` is a real seniority string ("Senior Level"). It was held
                # back while `seniority` was a strand-B key not yet on any adapter;
                # the contract exists now, so it ships.
                "seniority": "; ".join(x for x in levels if x) or None,
                "employment_type": j.get("type", ""),
                "salary": salary_from_text(text),
                "text": text,
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
