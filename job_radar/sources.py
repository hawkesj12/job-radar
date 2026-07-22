"""Job sources.

DEPTH  -- per-company ATS feeds (Greenhouse/Lever/Ashby/SmartRecruiters/Workable),
          polled for every company on the watchlist. All official public no-auth
          JSON endpoints.
BREADTH -- keyword aggregators + whole-board feeds searched across the whole
          market (Remotive/Jobicy/Arbeitnow/RemoteOK/Himalayas/Adzuna/HN/
          Braintrust/TechTree). All official public APIs.

Every source is a documented public API -- no scraping. (Scraper sources are an
opt-in extra, off by default; see the README.)
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from . import config
from .util import (
    NET_ERRORS,
    clean,
    get_json,
    post_json,
    q,
    salary_from_text,
    salary_range,
    to_date,
)


# ── DEPTH: per-company ATS feeds -- fetch_<ats>(slug) -> [posting] ───────────
def fetch_greenhouse(slug: str):
    data = get_json(
        f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    )
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
                "department": depts[0].get("name", "") if depts else "",
                "employment_type": "",
                "salary": salary_from_text(text),
                "text": text,
            }
        )
    return out


def fetch_lever(slug: str):
    data = get_json(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    out = []
    for j in data:
        cats = j.get("categories") or {}
        text = clean(j.get("descriptionPlain") or j.get("description", ""))
        sr = j.get("salaryRange") or {}
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
                "employment_type": cats.get("commitment", ""),
                "salary": salary,
                "text": text,
            }
        )
    return out


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
                "employment_type": j.get("employmentType", ""),
                "salary": salary or salary_from_text(text),
                "text": text,
            }
        )
    return out


def fetch_smartrecruiters(slug: str):
    data = get_json(
        f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100"
    )
    out = []
    for j in data.get("content", []):
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
                "employment_type": (j.get("typeOfEmployment") or {}).get("label", ""),
                "salary": "",
                "text": "",
            }
        )
    return out


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
# 400-req employer costs 20 calls. Cap it: 10 pages = 200 roles/employer keeps a
# nightly harvest across ~100 enterprise tenants bounded at ~1k requests.
WORKDAY_MAX_PAGES = 10
WORKDAY_PAGE = 20
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


def fetch_workday(slug: str, host: str = "wd1", site: str = ""):
    """Workday CxS job feed. Unlike every other ATS here, Workday needs a THREE-part
    key: tenant (`slug`), the numbered host shard (`wd1`..`wd103`), and the site slug
    — `nvidia`+`wd5`+`NVIDIAExternalCareerSite`. The site slug is unguessable, which
    is why the watchlist stores all three (discovery: `job_radar.discover`).

    Reaches the enterprise/government/healthcare employers the startup ATSs never
    see. Descriptions are deliberately NOT fetched: they live on a per-job detail
    endpoint, so pulling them would cost one request per posting instead of one per
    20. Title + location still carry the BM25 signal; the body is the trade for
    covering ~200 employers a night instead of ~8.
    """
    base = f"https://{slug}.{host}.myworkdayjobs.com/wday/cxs/{slug}/{site}"
    out, offset, total = [], 0, None
    for _ in range(WORKDAY_MAX_PAGES):
        data = post_json(
            f"{base}/jobs",
            {
                "appliedFacets": {},
                "limit": WORKDAY_PAGE,
                "offset": offset,
                "searchText": "",
            },
        )
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
                }
            )
        offset += WORKDAY_PAGE
        if len(postings) < WORKDAY_PAGE or offset >= total:
            break
    return out


DEPTH_ALL = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "smartrecruiters": fetch_smartrecruiters,
    "workable": fetch_workable,
    "workday": fetch_workday,
}

# Adapters needing more than a bare slug. engine._fetch_company passes these extra
# watchlist fields through as kwargs; every other adapter keeps the fetch(slug)
# contract the funnel's probe depends on.
DEPTH_EXTRA_FIELDS = {"workday": ("host", "site")}


# ── BREADTH: keyword aggregators -- search_<src>(queries) -> [posting] ───────
def search_remotive(queries):
    out = []
    for qy in queries[:4]:  # Remotive: <=4 calls per run (be a polite API citizen)
        try:
            data = get_json(f"https://remotive.com/api/remote-jobs?search={q(qy)}")
        except NET_ERRORS:
            continue
        for j in data.get("jobs", []):
            text = clean(j.get("description", ""))
            out.append(
                {
                    "title": j.get("title", ""),
                    "company": j.get("company_name", ""),
                    "location": (j.get("candidate_required_location") or "")
                    + " (Remote)",
                    "url": j.get("url", ""),
                    "posted": to_date(j.get("publication_date")),
                    "department": j.get("category", ""),
                    "employment_type": j.get("job_type", ""),
                    "salary": j.get("salary", "") or salary_from_text(text),
                    "text": text,
                    "source": "remotive",
                }
            )
        time.sleep(1)
    return out


def search_jobicy(queries):
    data = get_json("https://jobicy.com/api/v2/remote-jobs?count=100")
    out = []
    for j in data.get("jobs", []):
        text = clean(j.get("jobDescription") or j.get("jobExcerpt", ""))
        jt = j.get("jobType")
        out.append(
            {
                "title": j.get("jobTitle", ""),
                "company": j.get("companyName", ""),
                "location": (j.get("jobGeo") or "") + " (Remote)",
                "url": j.get("url", ""),
                "posted": to_date(j.get("pubDate")),
                "department": j.get("jobIndustry", ""),
                "employment_type": ", ".join(jt)
                if isinstance(jt, list)
                else (jt or ""),
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
                "salary": salary_range(j.get("salary_min"), j.get("salary_max")),
                "text": text,
                "source": "remoteok",
            }
        )
    return out


def search_himalayas(queries):
    out = []
    for qy in queries:
        try:
            data = get_json(f"https://himalayas.app/jobs/api/search?q={q(qy)}&limit=20")
        except NET_ERRORS:
            continue
        for j in data.get("jobs", []):
            text = clean(j.get("description") or j.get("excerpt", ""))
            regions = j.get("locationRestrictions") or []
            loc = (", ".join(regions) if regions else "") + " (Remote)"
            out.append(
                {
                    "title": j.get("title", ""),
                    "company": j.get("companyName", ""),
                    "location": loc.strip(),
                    "url": j.get("applicationLink") or j.get("guid", ""),
                    "posted": to_date(j.get("pubDate")),
                    "department": "",
                    "employment_type": j.get("employmentType", ""),
                    "salary": salary_range(j.get("minSalary"), j.get("maxSalary")),
                    "text": text,
                    "source": "himalayas",
                }
            )
        time.sleep(0.5)
    return out


def search_adzuna(queries):
    cfg = config.active()
    app_id, app_key = cfg.env(cfg.adzuna_app_id_env), cfg.env(cfg.adzuna_app_key_env)
    if not (app_id and app_key):
        print("  adzuna: no API keys set -- skipped (the free sources still run)")
        return []
    # A radius (miles) around `location`; Adzuna's `distance` is in km. Only when
    # searching a real place (not "remote") and the user asked for one.
    dist = ""
    if cfg.radius_miles > 0 and cfg.location.lower() != "remote":
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
                    f"&where={q(cfg.location)}{dist}&results_per_page=50&content-type=application/json"
                )
            except NET_ERRORS:
                break  # a dead page ends this query; other queries still run
            results = data.get("results", [])
            for j in results:
                text = clean(j.get("description", ""))
                out.append(
                    {
                        "title": j.get("title", ""),
                        "company": (j.get("company") or {}).get("display_name", ""),
                        "location": (j.get("location") or {}).get("display_name", ""),
                        "url": j.get("redirect_url", ""),
                        "posted": to_date(j.get("created")),
                        "department": (j.get("category") or {}).get("label", ""),
                        "employment_type": j.get("contract_time", ""),
                        "salary": salary_range(
                            j.get("salary_min"), j.get("salary_max")
                        ),
                        "text": text,
                        "source": "adzuna",
                    }
                )
            if len(results) < 50:
                break  # last page for this query
            time.sleep(0.5)  # be polite between pages of the same query
    return out


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
    thread = next(
        (h for h in hits if "who is hiring" in (h.get("title") or "").lower()), None
    )
    if not thread:
        return []
    try:
        tree = get_json(f"https://hn.algolia.com/api/v1/items/{thread['objectID']}")
    except NET_ERRORS:
        return []
    out = []
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
    return out


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
                    "department": "",
                    "employment_type": f"contract ({j.get('contract_type', '')})".strip(),
                    "salary": _bt_rate(j),
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


def search_techtree(queries):
    """TechTree -- an AI-native recruiting platform whose public board API fronts
    roles for hidden client companies the ATS/keyword feeds structurally miss."""
    data = get_json(
        "https://jobs.techtree.dev/api/public-job-posting?visibility=job_board_only"
    )
    out = []
    for j in data.get("jobs", []):
        parts = []
        for loc in j.get("locations") or []:
            if not isinstance(loc, dict):
                continue
            lbl = loc.get("display_label") or ""
            name = _CC_NAME.get((loc.get("country") or "").upper(), "")
            piece = lbl
            if name and name.lower() not in lbl.lower():
                piece = f"{lbl}, {name}".strip(", ") if lbl else name
            if piece:
                parts.append(piece)
        location = "; ".join(dict.fromkeys(parts))
        if j.get("workplace_type") == "Remote":
            location = (location + " (Remote)").strip()
        reqs, skills = j.get("requirements") or [], j.get("skills") or []
        text = clean(
            " ".join(
                str(x)
                for x in (
                    j.get("short_description", ""),
                    j.get("description", ""),
                    j.get("company_overview", ""),
                    " ".join(str(r) for r in reqs),
                    " ".join(str(s) for s in skills),
                )
                if x
            )
        )
        out.append(
            {
                "title": j.get("title", ""),
                "company": j.get("company_name", "") or "TechTree's client",
                "location": location,
                "url": j.get("application_url", ""),
                "posted": to_date(j.get("posted_date") or j.get("created_at")),
                "department": j.get("level", ""),
                "employment_type": j.get("job_type", ""),
                "salary": salary_range(j.get("salary_min"), j.get("salary_max")),
                "text": text,
                "source": "techtree",
            }
        )
    return out


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
    out = []
    for qy in queries:
        url = (
            f"https://data.usajobs.gov/api/Search?Keyword={q(qy)}"
            f"&ResultsPerPage={rpp}{loc}{rad}{remote}"
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
            continue
        for it in (data.get("SearchResult") or {}).get("SearchResultItems", []):
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
                    "department": d.get("DepartmentName", ""),
                    "employment_type": ", ".join(
                        s.get("Name", "") for s in (d.get("PositionSchedule") or [])
                    ),
                    "salary": salary_range(
                        pay.get("MinimumRange"), pay.get("MaximumRange")
                    ),
                    "text": clean(
                        (d.get("UserArea") or {})
                        .get("Details", {})
                        .get("JobSummary", "")
                    ),
                    "source": "usajobs",
                }
            )
    return out


BREADTH_ALL = {
    "remotive": search_remotive,
    "usajobs": search_usajobs,
    "jobicy": search_jobicy,
    "arbeitnow": search_arbeitnow,
    "remoteok": search_remoteok,
    "himalayas": search_himalayas,
    "adzuna": search_adzuna,
    "hn": search_hn_whoishiring,
    "braintrust": search_braintrust,
    "techtree": search_techtree,
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
