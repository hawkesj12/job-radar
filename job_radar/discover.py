"""Bulk company discovery from the Common Crawl CDX index.

The depth lane's bottleneck was never fetching — it was SUPPLY. Every per-company
ATS fetch needs a slug you have to already know, and the breadth funnel only learns
a slug when an aggregator happens to link straight at an ATS (rare: measured 45 new
slugs across 3,162 companies). So the watchlist grew by hand, one company at a time.

Common Crawl inverts that. It has already crawled the web and published a queryable
URL index (CDX), so every company with a public job board is already in there as a
`boards.greenhouse.io/{slug}` (or equivalent) URL. We are not crawling anyone — we
query an index someone else built and published for exactly this purpose.

Two-stage, and the second stage is what makes it trustworthy:

  1. MINE  — pull candidate slugs out of CDX by URL pattern (one HTTP call per ATS).
  2. PROBE — hit each candidate against its real ATS API and keep it only if it
             returns >=1 live role.

The probe IS the verification: a dead or misparsed slug returns nothing and costs a
single request, so a bad guess can never reach the watchlist. That is the same
discipline the hand-curated list already recorded ("each live-probed, returned >=1
open role") — just run in bulk instead of by hand.

Measured 2026-07-22: one capped CDX query returned 595 distinct Greenhouse slugs,
66% of the not-yet-known ones resolving to live boards; `*.myworkdayjobs.com/*`
returned 217 (tenant, host, site) triples at 57%.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from . import config
from .sources import DEPTH_ALL
from .util import NET_ERRORS

CDX_COLLINFO = "https://index.commoncrawl.org/collinfo.json"

# One URL pattern per ATS, plus how to turn a matched URL into a watchlist entry.
# Greenhouse/Lever/Ashby key on a single path segment; Workday needs the triple that
# only the full hostname + path carries.
_PATTERNS = {
    "greenhouse": (
        "boards.greenhouse.io/*",
        re.compile(r"^https?://boards\.greenhouse\.io/([A-Za-z0-9_-]+)"),
    ),
    "lever": (
        "jobs.lever.co/*",
        re.compile(r"^https?://jobs\.lever\.co/([A-Za-z0-9_-]+)"),
    ),
    "ashby": (
        "jobs.ashbyhq.com/*",
        re.compile(r"^https?://jobs\.ashbyhq\.com/([A-Za-z0-9_.-]+)"),
    ),
    "workday": (
        "*.myworkdayjobs.com/*",
        re.compile(
            r"^https?://([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/"
            r"(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_-]+)"
        ),
    ),
}

# ATSs that can tell us WHO OWNS a board, and how to ask. This is the difference
# between "this board is alive" and "this board is THIS company's" — see
# verify_identity(). Only Greenhouse exposes it; Ashby returns just {'apiVersion'}
# and Lever's payload carries the slug, not the org name.
_IDENTITY_URL = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}",
}

# Path segments that are NEVER a company board. Kept deliberately minimal: the probe
# is the real gate, so over-filtering here only loses real companies. Learned the
# hard way — 'search' was on this list until it turned out to be 3M's actual Workday
# site slug, and 'careers' is ASM Global's. Anything ambiguous belongs to the probe.
_NOT_SLUGS = {
    "embed",
    "robots",
    "robots.txt",
    "favicon.ico",
    "sitemap.xml",
    "api",
    "static",
    "assets",
    "index.html",
    "en-us",
}


def latest_collection() -> str:
    """Newest Common Crawl collection id (e.g. 'CC-MAIN-2026-25')."""
    cfg = config.active()
    req = urllib.request.Request(CDX_COLLINFO, headers={"User-Agent": cfg.user_agent})
    with urllib.request.urlopen(req, timeout=cfg.timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))[0]["id"]


def mine(ats: str, collection: str | None = None, limit: int = 4000) -> list[dict]:
    """Stage 1 — pull candidate watchlist entries for one ATS out of the CDX index.

    Returns UNVERIFIED candidates; every one still has to survive `probe`.
    """
    if ats not in _PATTERNS:
        raise ValueError(f"no CDX pattern for ats={ats!r}")
    pattern, rx = _PATTERNS[ats]
    collection = collection or latest_collection()
    cfg = config.active()
    url = (
        f"https://index.commoncrawl.org/{collection}-index"
        f"?url={urllib.parse.quote(pattern, safe='')}&output=json&limit={limit}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": cfg.user_agent})
    with urllib.request.urlopen(req, timeout=max(cfg.timeout, 90)) as r:
        body = r.read().decode("utf-8", "replace")

    seen, out = set(), []
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        m = rx.match(rec.get("url", ""))
        if not m:
            continue
        if ats == "workday":
            tenant, host, site = m.groups()
            if site.lower() in _NOT_SLUGS:
                continue
            key = (tenant, host, site)
            entry = {"ats": ats, "slug": tenant, "host": host, "site": site}
        else:
            slug = m.group(1)
            if slug.lower() in _NOT_SLUGS:
                continue
            key = slug.lower()
            entry = {"ats": ats, "slug": slug}
        if key in seen:
            continue
        seen.add(key)
        out.append(entry)
    return out


def board_owner(ats: str, slug: str) -> str | None:
    """The company name the ATS itself reports for a board, or None if unavailable.

    Verified 2026-07-22: greenhouse `cloudflare` -> 'Cloudflare', `prizepicks` ->
    'PrizePicks', `leveltenenergy` -> 'LevelTen Energy'.
    """
    url = _IDENTITY_URL.get(ats)
    if not url:
        return None
    cfg = config.active()
    req = urllib.request.Request(
        url.format(slug=slug), headers={"User-Agent": cfg.user_agent}
    )
    try:
        with urllib.request.urlopen(req, timeout=cfg.timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace")).get("name")
    except (urllib.error.HTTPError, *NET_ERRORS):
        return None
    except Exception:  # noqa: BLE001
        return None


def verify_identity(ats: str, slug: str, company: str) -> bool:
    """Does this board actually BELONG to `company`?

    A live board is not the same as the RIGHT board. 'Capital One' normalizes to the
    candidate `capital`, and https://jobs.lever.co/capital is a real board with real
    jobs — owned by someone else entirely. Liveness cannot distinguish that; only the
    ATS's own claim about who it is can.

    Confirmed catches (2026-07-22, live Greenhouse): `veterans` for Veterans Health
    Administration is really owned by 'IntelliDyne Jobs for Veterans'; `remote` for
    the company 'Remote' is owned by 'General Assembly Remote Jobs' — one the
    conservative full-name heuristic had already let through. Run over the 30 names
    in a real shortlist it also rejected `general` for General Dynamics IT (owner
    'General Interest') and `parallel` for Parallel Partners (owner 'Parallel
    Systems'). Every one is a first-word collision that liveness alone admits.

    Note what this gate does NOT do, because the boundary is easy to misread. It
    only runs on a board that already returned live roles (see `probe`), so a dead
    slug — `capital`, `foundation` — never reaches it; the liveness probe drops
    those first. And it only runs where the ATS reports an owner, i.e. Greenhouse:
    for the motivating example `jobs.lever.co/capital` (a real board, 38 live roles,
    not Capital One's) this function returns True unconditionally. What protects the
    Lever/Ashby lane is `from_names` withholding the bare-first-word variant from an
    ATS whose ownership cannot be checked — not this gate.

    Matching is strict equality after normalization, deliberately. Relaxing it to a
    prefix rule would recover renames (slug `vaco` reports 'Vaco LLC' while the
    posting says 'Vaco by Highspring') but would re-admit exactly the first-word
    false positives this exists to stop. A missed company costs one company; a false
    one files a stranger's jobs under a real employer and is invisible afterwards.

    Returns True when the ATS exposes no identity endpoint, so callers that cannot
    verify (Ashby/Lever) fall back to conservative variants rather than silently
    rejecting everything.
    """
    if ats not in _IDENTITY_URL:
        return True
    owner = board_owner(ats, slug)
    if not owner:
        return False  # identity WAS available in principle and we couldn't confirm it
    return _norm_name(owner) == _norm_name(company) or _norm_name(owner).replace(
        " ", ""
    ) == _norm_name(company).replace(" ", "")


def probe(
    candidates: list[dict],
    workers: int = 8,
    require_identity: bool = False,
    outcomes: list | None = None,
) -> list[dict]:
    """Stage 2 — keep only candidates whose ATS API returns >=1 live role.

    This is the gate that makes bulk mining safe: an unreachable, churned, or
    misparsed slug simply returns nothing and is dropped. Each surviving entry is
    annotated with the role count that proved it.

    `require_identity` adds the second gate: for an ATS that reports its board owner,
    the reported name must match the candidate's `name`. Without it, liveness is the
    only test and a real-but-wrong board passes. Candidates carrying no `name` (pure
    CDX mining, where we never had a company name to begin with) skip the check.

    Pass a list as `outcomes` to receive EVERY candidate annotated with why it did or
    did not survive. The return value stays the verified subset either way, so
    existing callers are unaffected — but a caller that wants to record a refusal
    permanently can, and for that the distinction below is the whole point:

      TERMINAL — safe to stop asking:
        `refused`      401/403: the board exists and will not serve us.
        `wrong-owner`  the ATS named an owner and it wasn't this company.
        `unsupported`  no adapter for this ats value.
      RETRYABLE — a caller that blacklists on these WILL lose real companies:
        `throttled`    429. Transient by definition. Probing a few hundred Workday
                       tenants reliably trips this on boards that served roles
                       minutes earlier.
        `missing`      404. No board today; a company may adopt one tomorrow.
        `empty`        live board, zero open roles right now.
        `error`        network/parse failure, including a timeout.

    Caveat on `wrong-owner`: it currently also fires when the identity endpoint was
    unreachable, because `board_owner` cannot distinguish "not this company" from
    "no answer." Treat it as terminal only if you can tolerate that conflation.
    """

    def _one(c):
        fetch = DEPTH_ALL.get(c["ats"])
        if not fetch:
            return {**c, "outcome": "unsupported"}
        kwargs = {k: c[k] for k in ("host", "site") if k in c}
        try:
            postings = fetch(c["slug"], **kwargs)
        except urllib.error.HTTPError as e:
            # Three genuinely different failures, and conflating them is expensive:
            #   401/403 "refused"  — the board exists and will not serve us. Terminal.
            #   429     "throttled" — TRANSIENT. It means slow down, not go away. It
            #             must never be terminal: probing a few hundred Workday
            #             tenants reliably trips their rate limiter, and a caller
            #             that treats that as permanent would blacklist hundreds of
            #             perfectly good employers in one bad run. (Measured: after
            #             heavy probing, 40/40 real triples returned 429 — including
            #             3m/Search, which had served 200 roles minutes earlier.)
            #   404     "missing"  — no such board; retryable, a company may adopt one.
            if e.code in (401, 403):
                outcome = "refused"
            elif e.code == 429:
                outcome = "throttled"
            else:
                outcome = "missing"
            return {**c, "outcome": outcome}
        except NET_ERRORS:
            return {**c, "outcome": "error"}
        except Exception:  # noqa: BLE001 — a single bad board must not kill the run
            return {**c, "outcome": "error"}
        if not postings:
            return {**c, "outcome": "empty"}
        if require_identity and c.get("name"):
            if not verify_identity(c["ats"], c["slug"], c["name"]):
                return {**c, "outcome": "wrong-owner"}
        return {
            **c,
            "roles": len(postings),
            "outcome": "ok",
            "source": c.get("source", "commoncrawl"),
        }

    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(_one, candidates))
    if outcomes is not None:
        outcomes.extend(results)
    return [r for r in results if r.get("outcome") == "ok"]


# ── name -> slug: for companies CDX never surfaced ────────────────────────────
_STOPWORDS = re.compile(
    r"\b(inc|llc|l\.l\.c|ltd|limited|corp|corporation|co|company|the|group|holdings"
    r"|plc|gmbh|sa|nv|ag|pte|pty|llp|lp)\b",
    re.I,
)
_PUNCT = re.compile(r"[^a-z0-9 ]+")


def _norm_name(name: str) -> str:
    """Company name -> a comparable key: lowercase, depunctuated, legal suffix dropped.

    One definition, three users: slug generation below, identity comparison in
    verify_identity(), and jobfitr's ledger primary key. Keeping them identical is
    what stops 'Westhab Inc.' and 'Westhab' resolving to two different answers.
    """
    base = _PUNCT.sub(" ", (name or "").lower())
    base = _STOPWORDS.sub(" ", base)
    return " ".join(base.split())


def name_variants(name: str, aggressive: bool = False) -> list[str]:
    """Candidate slugs for a company name, most-likely first.

    Companies overwhelmingly slug their own name: 'Cloudflare' -> cloudflare,
    'DoubleVerify' -> doubleverify, 'First Resonance' -> firstresonance or
    first-resonance. Verified 2026-07-22 against live boards (cloudflare 264 roles,
    ramp 121, robinhood 118, doubleverify 40).

    `aggressive` adds the bare first word ('Capital One' -> `capital`). That variant
    is a correctness trap on its own: measured over 120 real store names it produced
    ~7 confident FALSE matches per true one, because `capital`, `veterans`, and
    `foundation` are all REAL boards owned by unrelated companies and a liveness probe
    cannot tell the difference. It is safe ONLY when the caller also verifies board
    ownership (see verify_identity), so callers must opt in deliberately.
    """
    base = _norm_name(name)
    if not base:
        return []
    cands = [base.replace(" ", ""), base.replace(" ", "-")]
    if aggressive:
        cands.append(base.split(" ")[0])
    return [v for v in dict.fromkeys(cands) if 2 < len(v) < 40]


def match_known(names: list[str], universe: list[dict]) -> list[dict]:
    """Stage 0 — match company names against an ALREADY-MINED slug universe.

    Strictly better than guessing when the universe covers the company: the slug is
    real by construction (CDX saw it), so the probe is a confirmation rather than a
    lottery. Exact match on a normalized variant only — fuzzy matching a company name
    to a slug invites false pairs ('agency' matching half the corpus), and a wrong
    slug silently attributes another company's jobs to this one.
    """
    by_slug: dict[tuple, dict] = {}
    for e in universe:
        by_slug.setdefault((e["ats"], e["slug"].lower()), e)
    out, seen = [], set()
    for name in names:
        for v in name_variants(name):
            for (ats, slug), entry in by_slug.items():
                if slug == v and (ats, slug) not in seen:
                    seen.add((ats, slug))
                    out.append({**entry, "name": name, "source": "name-match"})
                    break
    return out


def from_names(
    names: list[str],
    ats_list: list[str] | None = None,
    known: set | None = None,
    workers: int = 8,
    outcomes: list | None = None,
) -> list[dict]:
    """Generate candidate slugs from company names and keep the ones that resolve.

    The fallback lane for companies Common Crawl never indexed. Only single-key ATSs —
    Workday's site slug is unguessable from a name, so it is deliberately excluded
    (use CDX mining for Workday).

    Two gates, not one: the board must be LIVE, and — where the ATS will tell us — it
    must BELONG to the company. That second gate is what lets the aggressive
    first-word variant be used at all: it is generated only for an ATS whose ownership
    we can check, so a wrong guess is rejected by the authority instead of quietly
    entering the corpus.
    """
    ats_list = ats_list or ["greenhouse", "lever", "ashby"]
    known = known or set()
    candidates, seen = [], set()
    for name in names:
        for ats in ats_list:
            verifiable = ats in _IDENTITY_URL
            for v in name_variants(name, aggressive=verifiable):
                key = (ats, v)
                if key in known or key in seen:
                    continue
                seen.add(key)
                candidates.append(
                    {"ats": ats, "slug": v, "name": name, "source": "name-guess"}
                )
    verified = probe(
        candidates, workers=workers, require_identity=True, outcomes=outcomes
    )
    verified.sort(key=lambda e: -e["roles"])
    return verified


def discover(
    ats_list: list[str] | None = None,
    collection: str | None = None,
    limit: int = 4000,
    known: set | None = None,
    workers: int = 8,
) -> list[dict]:
    """Mine + probe across several ATSs. `known` skips slugs already on a watchlist.

    Returns verified entries, richest first, ready to review and append.
    """
    ats_list = ats_list or ["greenhouse", "lever", "ashby"]
    known = known or set()
    candidates = []
    for ats in ats_list:
        for c in mine(ats, collection=collection, limit=limit):
            key = (
                (c["ats"], c["slug"].lower(), c.get("site", "").lower())
                if c["ats"] == "workday"
                else (c["ats"], c["slug"].lower())
            )
            if key not in known:
                candidates.append(c)
    verified = probe(candidates, workers=workers)
    verified.sort(key=lambda e: -e["roles"])
    return verified


def known_keys(watchlist_path) -> set:
    """Existing (ats, slug[, site]) keys from a watchlist, for deduping candidates."""
    try:
        with open(watchlist_path, encoding="utf-8") as f:
            companies = json.load(f).get("companies", [])
    except (OSError, json.JSONDecodeError):
        return set()
    out = set()
    for c in companies:
        ats, slug = c.get("ats", ""), (c.get("slug") or "").lower()
        if ats == "workday":
            out.add((ats, slug, (c.get("site") or "").lower()))
        else:
            out.add((ats, slug))
    return out
