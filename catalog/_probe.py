#!/usr/bin/env python3
"""Run the probe procedure from `_SCHEMA.md` against one source and emit frontmatter.

Steps 1-5 of that procedure are deterministic, so they live here rather than in a
human's head: hand-rolling them per source produces numbers that cannot be compared,
which is the failure this file exists to prevent. Steps 6-7 (fields, rights) still
need judgement and stay in the profile.

This carries its OWN transport rather than importing `job_radar.util.get_json`, for
the reason the schema calls rule 7: never probe from inside a normalizer. The package
transport reads `config.active()` — a user's timeout, User-Agent, and the redirect
guard — and every one of those would silently colour a measurement. A probe must hit
the raw endpoint with known settings. That duplication is deliberate; it is the only
copy of a request in this repo that is allowed to differ from the engine's.

    python catalog/_probe.py --name jobicy \\
        --base 'https://jobicy.com/api/v2/remote-jobs?count=100' \\
        --items jobs --date-field pubDate

Nothing here writes a profile. It prints YAML you paste, and saves the raw capture to
`catalog/_raw/<name>.json` so `## A real record` can be pasted from evidence.
"""

from __future__ import annotations

import argparse
import html.entities
import json
import pathlib
import re
import statistics
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

UA = "job-radar-catalog-probe/0.1 (+https://github.com/hawkesj12/job-radar)"
TIMEOUT = 30
RAW_DIR = pathlib.Path(__file__).parent / "_raw"

# The schema's closing note: several of these sources are being probed FOR their 429
# behaviour, so parallel requests make a real rate limit indistinguishable from
# self-contention. One governor, one thread, and every caller goes through it.
_LOCK = threading.Lock()
_LAST = [0.0]
MIN_INTERVAL = 1.0


def fetch(url: str, *, method: str = "GET", body: bytes | None = None) -> tuple:
    """Return (status, bytes, seconds). Never raises for an HTTP error status —
    a 400 IS the measurement in step 1, so it must come back as data."""
    with _LOCK:
        gap = MIN_INTERVAL - (time.monotonic() - _LAST[0])
        if gap > 0:
            time.sleep(gap)
        _LAST[0] = time.monotonic()
    req = urllib.request.Request(
        url, data=body, headers={"User-Agent": UA, "Accept": "*/*"}, method=method
    )
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, r.read(), time.monotonic() - t0
    except urllib.error.HTTPError as e:
        return e.code, e.read(), time.monotonic() - t0
    except (
        Exception
    ) as e:  # URLError, timeout, bad TLS — a status of 0 means "no answer"
        print(f"    ! {type(e).__name__}: {e}", file=sys.stderr)
        return 0, b"", time.monotonic() - t0


def add_param(url: str, key: str, value) -> str:
    parts = urllib.parse.urlsplit(url)
    qs = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    qs = [(k, v) for k, v in qs if k != key]
    qs.append((key, str(value)))
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(qs)))


def dig(obj, path: str):
    """Walk a dotted path. An empty path means the payload itself is the array."""
    if not path:
        return obj
    for part in path.split("."):
        if isinstance(obj, list):
            obj = obj[0] if obj else None
        if not isinstance(obj, dict):
            return None
        obj = obj.get(part)
    return obj


_XML_NAMED = {"amp", "lt", "gt", "quot", "apos"}
_NAMED_ENTITY = re.compile(rb"&([A-Za-z][A-Za-z0-9]*);")


def _numeric_entities(raw: bytes) -> bytes:
    """Rewrite HTML named entities to numeric refs so an XML parser accepts them.

    XML defines five named entities; HTML defines about 2,000. NoDesk publishes valid
    RSS whose descriptions contain `&rsquo;`, and `ET.fromstring` answers
    `undefined entity: line 17, column 37` — which `records()` catches and turns into
    an empty list. So nodesk reported 0 rows, `title_search: false`, and no captured
    record at all, from a 200 carrying 11 KB of real items. A parse failure and an
    empty feed must not look alike."""

    def swap(m: re.Match) -> bytes:
        name = m.group(1).decode()
        if name in _XML_NAMED:
            return m.group(0)
        cp = html.entities.name2codepoint.get(name)
        return f"&#{cp};".encode() if cp else m.group(0)

    return _NAMED_ENTITY.sub(swap, raw)


def parse_rss(raw: bytes) -> list:
    """RSS/Atom -> a list of dicts, so the rest of this file sees one record shape.

    Several sources (We Work Remotely, NoDesk, Personio) publish only a feed, and a
    feed with no query interface is still a measurable source — it just answers
    `title_search: false` by shape rather than by probe."""
    root = ET.fromstring(_numeric_entities(raw))
    out = []
    for item in root.iter():
        tag = item.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        rec = {}
        for child in item:
            key = child.tag.split("}")[-1]
            rec[key] = (child.text or "").strip() or child.attrib.get("href", "")
        out.append(rec)
    return out


def records(raw: bytes, items_path: str, fmt: str) -> list:
    if not raw:
        return []
    if fmt == "rss":
        try:
            return parse_rss(raw)
        except ET.ParseError:
            return []
    try:
        payload = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        return []
    got = dig(payload, items_path)
    return got if isinstance(got, list) else []


def _epoch(n: float) -> float:
    """Seconds or milliseconds? Lever sends ms, and feeding ms to fromtimestamp()
    raises `year 51188 is out of range` — which killed the whole lever probe rather
    than costing one date."""
    return n / 1000 if n > 1e11 else n


def to_dt(value) -> datetime | None:
    """Lenient date parsing. Vendors send ISO, RFC 822, epoch seconds, and text."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(_epoch(value), timezone.utc)
    s = str(value).strip()
    if s.isdigit() and len(s) >= 9:
        return datetime.fromtimestamp(_epoch(int(s)), timezone.utc)
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


# ── step 1 ────────────────────────────────────────────────────────────────────
def step_junk(base: str, items: str, fmt: str) -> tuple[str, str]:
    """Does a parameter this API has never heard of change anything?

    This runs FIRST because it decides what every later number is worth. A 400 means
    a parameter that returns a count is real. Silently ignored means a typo'd
    parameter is a permanent, invisible no-op — and every count below is weaker
    evidence than it looks."""
    s_ctl, b_ctl, _ = fetch(base)
    s_junk, b_junk, _ = fetch(add_param(base, "zzz_not_real", 1))
    n_ctl = len(records(b_ctl, items, fmt))
    n_junk = len(records(b_junk, items, fmt))
    note = f"control {s_ctl} ({n_ctl} rows) vs junk {s_junk} ({n_junk} rows)"
    if s_junk == 400:
        return "rejects_unknown_400", note
    if s_junk in (401, 403):
        return "unknown", note + " — auth wall, not a validation answer"
    if s_junk == s_ctl and n_junk == n_ctl:
        return "ignores_unknown", note
    return "unknown", note + " — junk param CHANGED the result; investigate by hand"


# ── step 2 ────────────────────────────────────────────────────────────────────
# The generic names. A source that 400s on everything unknown will answer `false` to
# all of these and still have a working title search under a name of its own —
# jobicy's is `tag`, and `tag=nurse` returns "Clinical Review Nurse". So the vendor's
# own parameter names go in _targets.json and are tried alongside these.
TITLE_PARAMS = ("q", "query", "search", "keyword", "keywords", "title", "name")


TITLE_FIELD = ["title"]


def relevance(rows: list, term: str) -> float:
    """Share of returned rows whose title actually contains the term.

    The title key is per-source (jobicy says `jobTitle`, remoteok says `position`), and
    reading the wrong key returns 0% for every parameter — which looks exactly like a
    source with no title search."""
    titles = [str(r.get(TITLE_FIELD[0], "")) for r in rows if isinstance(r, dict)]
    if not titles:
        return 0.0
    return sum(term.lower() in t.lower() for t in titles) / len(titles)


def step_title(
    base: str, items: str, fmt: str, term: str, params: tuple = TITLE_PARAMS
) -> tuple[str, list[str]]:
    """Try each candidate parameter individually against an unfiltered control.

    Judged by RELEVANCE, not by "the response differed". Arbeitnow taught this on the
    first run of this file: it serves a rotating slice, so every parameter — including
    ones it has never heard of — produces a different-looking body, and a
    difference-based test called all seven of them real. Its `search=nurse` returns 175
    rows of unrelated titles. The honest question is not "did the response change" but
    "is the response ABOUT the term", which is also the only thing a live per-request
    fetch needs to be true."""
    _, b_ctl, _ = fetch(base)
    ctl = records(b_ctl, items, fmt)
    base_rel = relevance(ctl, term)
    notes = [f"control: {len(ctl)} rows, {base_rel:.0%} already match '{term}'"]
    works, empties = [], []
    for p in params:
        status, raw, _ = fetch(add_param(base, p, term))
        rows = records(raw, items, fmt)
        rel = relevance(rows, term)
        hit = bool(status == 200 and rows and rel >= 0.3 and rel > base_rel + 0.2)
        # A 200 with ZERO rows, where the control had plenty, is not a dead
        # parameter — it is a live one whose term matched nothing. landing.jobs
        # answered `q=nurse` with 0 rows and was scored `false`; `q=engineer`
        # then returned 42 rows at 95% title relevance. Scoring the empty answer
        # as "no title search" routed a genuinely queryable source to the
        # harvest lane, which is the one decision this step exists to make.
        empty_hit = bool(status == 200 and not rows and ctl)
        notes.append(
            f"{p}={term} -> {status}, {len(rows)} rows, {rel:.0%} relevant"
            + (" <- FILTERS" if hit else "")
            + (
                " <- ZERO rows vs a non-empty control: RETRY with a term this "
                "source could plausibly match before recording false"
                if empty_hit
                else ""
            )
        )
        if hit:
            works.append(p)
        elif empty_hit:
            empties.append(p)
    verdict = "true" if works else ("unknown" if empties else "false")
    return verdict, notes + [
        f"filters on: {works or 'nothing'}"
        + (
            f"; INCONCLUSIVE (zero rows, retry another term): {empties}"
            if empties
            else ""
        )
    ]


# ── step 3 ────────────────────────────────────────────────────────────────────
def step_pages(
    base: str, items: str, fmt: str, param: str, start: int, size: int, mode: str
):
    """Walk the page parameter upward until it breaks, then retry the first failure
    three times — a hard cap and a throttle look identical on one request, and calling
    a throttle a cap is how an advertised total gets believed.

    Retries sleep before trying again. Without that, a source that throttled during the
    earlier steps reports a ceiling of 1 (arbeitnow did, on the first run of this file,
    while `?page=2` was working fine by hand a minute later)."""

    def page_rows(n: int) -> tuple[int, int]:
        """3 attempts with a growing pause; the best answer wins, so a transient
        failure cannot masquerade as the end of the data."""
        value = n * size if mode == "offset" else n
        best = (0, 0)
        for attempt in range(3):
            if attempt:
                time.sleep(2 * attempt)
            status, raw, _ = fetch(add_param(base, param, value))
            rows = len(records(raw, items, fmt))
            if status == 200 and rows:
                return status, rows
            if status == 400:  # an explicit "page too high" is an answer, not a blip
                return status, rows
            best = max(best, (status, rows))
        return best

    # Does this parameter do anything at all? If asking for a page well past the end
    # returns the same volume as the control, the source is ignoring it and there is
    # no ceiling to find — only the illusion of one.
    _, ctl_raw, _ = fetch(base)
    n_ctl = len(records(ctl_raw, items, fmt))
    far_status, far_rows = page_rows(999)
    if far_status == 200 and far_rows == n_ctl and n_ctl:
        return "unknown", (
            f"`{param}` appears IGNORED: page 999 returned the same {n_ctl} rows as the "
            "control. No reachable ceiling was established"
        )

    good, probe = start, max(start, 1)
    while probe <= 4096:
        status, n = page_rows(probe)
        if status != 200 or n == 0:
            break
        good = probe
        probe *= 2
    lo, hi = good, min(probe, 4096)
    while lo + 1 < hi:  # binary-search the real edge between last good and first bad
        mid = (lo + hi) // 2
        status, n = page_rows(mid)
        if status == 200 and n:
            lo = mid
        else:
            hi = mid
    return lo, f"last page returning rows: {lo} (first failure at {hi})"


# ── step 4 ────────────────────────────────────────────────────────────────────
def step_rate(base: str, burst: int) -> str:
    """Opt-in, because this deliberately abuses somebody else's server until it says
    no. Off by default: `unknown` is a valid answer and a needless 429 is not."""
    global MIN_INTERVAL
    saved, MIN_INTERVAL = MIN_INTERVAL, 0.0
    try:
        for i in range(1, burst + 1):
            status, _, _ = fetch(base)
            if status == 429:
                return f"429 after {i} consecutive requests with no pause"
            if status >= 500:
                return f"HTTP {status} after {i} requests — server-side, not a documented limit"
        return f"unknown — {burst} consecutive requests, no 429"
    finally:
        MIN_INTERVAL = saved


# ── step 5 ────────────────────────────────────────────────────────────────────
def step_fresh(base, items, fmt, date_field, param, size, mode, max_page):
    """Sample pages spread across the WHOLE range, not just page 1, and record
    date_sorted: if false a consumer cannot page to the fresh part and must cut at
    ingest instead."""
    picks = sorted({0, max_page // 4, max_page // 2, (3 * max_page) // 4, max_page})
    now = datetime.now(timezone.utc)
    ages, per_page, sample = [], [], []
    for p in picks:
        value = p * size if mode == "offset" else p
        _, raw, _ = fetch(add_param(base, param, value) if max_page else base)
        rows = records(raw, items, fmt)
        sample.extend(rows)
        page_ages = []
        for r in rows:
            dt = to_dt(dig(r, date_field)) if isinstance(r, dict) else None
            if dt:
                page_ages.append((now - dt).days)
        ages.extend(page_ages)
        if page_ages:
            per_page.append((p, statistics.median(page_ages)))
        if not max_page:
            break
    if not ages:
        return None, None, "unknown", "no parseable dates in the sample", sample
    within = sum(1 for a in ages if a <= 100) / len(ages)
    # Monotonicity across EVERY sampled page, not just the endpoints. Comparing only
    # first-vs-last called 4dayweek date-sorted on medians of 0, 79, 5, 55, 46 days: the
    # ends happened to slope the right way while the middle was noise. `date_sorted` is
    # the field that decides whether a consumer can page to the fresh part, so a false
    # positive there is worse than `unknown`.
    sorted_by_date = "unknown"
    if len(per_page) >= 3:
        ages = [m for _, m in per_page]
        rising = all(b >= a - 5 for a, b in zip(ages, ages[1:]))  # 5d slack for churn
        sorted_by_date = "true" if rising and ages[-1] > ages[0] + 30 else "false"
    elif len(per_page) == 2:
        sorted_by_date = "unknown"  # two points cannot distinguish a trend from noise
    note = (
        f"{len(ages)} dated rows across pages {[p for p, _ in per_page]}; "
        + ", ".join(f"p{p} median {m}d" for p, m in per_page)
    )
    return statistics.median(ages), round(within, 2), sorted_by_date, note, sample


def is_prose(v: str) -> bool:
    """A body is prose, not a long URL. devitjobs has NO description field, and taking
    the longest string per record reported a 393-char 'body' that was really an Indeed
    redirect URL — which would have contradicted its own rejected status."""
    if len(v) < 200:
        return False
    head = v.strip()[:12].lower()
    if head.startswith(("http://", "https://", "www.")):
        return False
    return v.count(" ") >= 20  # real prose, not a delimited blob or a query string


def bar(pct: float) -> str:
    filled = round(pct * 5)
    return "█" * filled + "░" * (5 - filled)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--name", required=True, help="slug; matches the profile filename")
    ap.add_argument("--base", required=True, help="a working, unfiltered request URL")
    ap.add_argument("--items", default="", help="dotted path to the array ('' = root)")
    ap.add_argument("--date-field", default="", help="dotted path to the posted date")
    ap.add_argument("--format", choices=("json", "rss"), default="json")
    ap.add_argument("--page-param", default="page")
    ap.add_argument("--page-start", type=int, default=1)
    ap.add_argument("--page-size", type=int, default=0, help="rows per page, if fixed")
    ap.add_argument("--page-mode", choices=("page", "offset"), default="page")
    ap.add_argument("--title-term", default="nurse")
    ap.add_argument("--title-field", default="title", help="the record's title key")
    ap.add_argument(
        "--title-params",
        default="",
        help="extra vendor-specific parameter names to try, comma-separated",
    )
    ap.add_argument(
        "--rate-limit",
        type=int,
        default=0,
        metavar="N",
        help="burst N requests with no pause to find the 429. OFF by default",
    )
    ap.add_argument("--skip", default="", help="comma-separated step numbers to skip")
    ap.add_argument(
        "--record-index",
        type=int,
        default=0,
        help="which row to save as the captured record. Remote OK's row 0 is a "
        "metadata object (`last_updated` + `legal`), not a job, so saving row 0 "
        "produced a `## A real record` section containing no job at all",
    )
    a = ap.parse_args()
    skip = {s.strip() for s in a.skip.split(",") if s.strip()}
    TITLE_FIELD[0] = a.title_field
    fmt, items = a.format, a.items
    out: dict = {}

    print(f"probing {a.name} — {a.base}\n", file=sys.stderr)

    if "1" not in skip:
        print("  [1/5] junk parameter", file=sys.stderr)
        out["param_validation"], out["_junk"] = step_junk(a.base, items, fmt)

    if "2" not in skip:
        print("  [2/5] title search", file=sys.stderr)
        if fmt == "rss":
            out["title_search"], out["_title"] = (
                "false",
                ["RSS feed — no query interface"],
            )
        else:
            extra = tuple(
                x.strip()
                for x in a.title_params.split(",")
                if x.strip() and x.strip() not in TITLE_PARAMS
            )
            out["title_search"], out["_title"] = step_title(
                a.base, items, fmt, a.title_term, TITLE_PARAMS + extra
            )

    max_page = 0
    if "3" not in skip:
        print("  [3/5] page ceiling", file=sys.stderr)
        if fmt == "rss":
            out["max_page"], out["_pages"] = "null", "RSS feed — single document"
        else:
            max_page, out["_pages"] = step_pages(
                a.base,
                items,
                fmt,
                a.page_param,
                a.page_start,
                a.page_size or 1,
                a.page_mode,
            )
            out["max_page"] = max_page

    out["rate_limit"] = "unknown"
    if a.rate_limit and "4" not in skip:
        print(f"  [4/5] rate limit (burst {a.rate_limit})", file=sys.stderr)
        out["rate_limit"] = step_rate(a.base, a.rate_limit)

    sample: list = []
    if "5" not in skip:
        print("  [5/5] freshness", file=sys.stderr)
        median, within, sortd, note, sample = step_fresh(
            a.base,
            items,
            fmt,
            a.date_field,
            a.page_param,
            a.page_size or 1,
            a.page_mode,
            max_page if isinstance(max_page, int) else 0,
        )
        out.update(
            median_age_days=median,
            pct_within_100d=within,
            date_sorted=sortd,
            _fresh=note,
        )

    if not sample:
        _, raw, _ = fetch(a.base)
        sample = records(raw, items, fmt)
    bodies = []
    for r in sample:
        if isinstance(r, dict):
            longest = max(
                (str(v) for v in r.values() if isinstance(v, str) and is_prose(v)),
                key=len,
                default="",
            )
            if len(longest) > 200:
                bodies.append(len(longest))

    RAW_DIR.mkdir(exist_ok=True)
    if len(sample) > a.record_index:
        raw_path = RAW_DIR / f"{a.name}.json"
        raw_path.write_text(
            json.dumps(sample[a.record_index], indent=2, ensure_ascii=False)
        )
        print(f"\n  captured record -> {raw_path}", file=sys.stderr)

    today = datetime.now().strftime("%Y-%m-%d")
    pct = out.get("pct_within_100d")
    print(f"\n# ── {a.name}: paste into the profile, then finish steps 6-7 by hand")
    print("query:")
    print(f"  title_search: {out.get('title_search', 'unknown')}")
    print(f"  param_validation: {out.get('param_validation', 'unknown')}")
    print("limits:")
    print(f"  page_size: {a.page_size or 'unknown'}")
    print(f"  max_page: {out.get('max_page', 'unknown')}")
    reach = (
        (a.page_size * out["max_page"])
        if a.page_size and isinstance(out.get("max_page"), int)
        else "unknown"
    )
    print(f"  reachable_per_query: {reach}")
    print(f"  rate_limit: {out['rate_limit']}")
    print("freshness:")
    print(
        f"  median_age_days: {out.get('median_age_days') if out.get('median_age_days') is not None else 'unknown'}"
    )
    print(
        f"  pct_within_100d: {pct if pct is not None else 'unknown'}"
        + (f"  # {bar(pct)}" if pct is not None else "")
    )
    print(f"  date_sorted: {out.get('date_sorted', 'unknown')}")
    print(f"  measured_at: {today}")
    if bodies:
        print(
            f"# body: median {int(statistics.median(bodies))} chars over {len(bodies)} rows"
        )
    print("\n# ── how this was probed (paste into '## How this was probed')")
    for key in ("_junk", "_title", "_pages", "_fresh"):
        if key in out:
            value = out[key]
            for line in value if isinstance(value, list) else [value]:
                print(f"#   {key[1:]}: {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
