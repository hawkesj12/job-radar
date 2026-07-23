"""Bulk company discovery from the Common Crawl CDX index.

The depth lane's bottleneck was never fetching — it was SUPPLY. Every per-company
ATS fetch needs a slug you have to already know, and the breadth funnel only learns
a slug when an aggregator happens to link straight at an ATS, which is rare. So the
watchlist grew by hand, one company at a time.

Common Crawl inverts that. It has already crawled the web and published a queryable
URL index (CDX), so every company with a public job board is already in there as a
`boards.greenhouse.io/{slug}` (or equivalent) URL. We are not crawling anyone — we
query an index someone else built and published for exactly this purpose.

Two-stage, and the second stage is what makes it trustworthy:

  1. MINE  — pull candidate slugs out of CDX by URL pattern (one HTTP call per ATS).
  2. PROBE — hit each candidate against its real ATS API and keep it only if it
             returns >=1 live role.

The probe IS the verification: a dead or misparsed slug returns nothing and costs a
single cheap request (see sources.LIVENESS), so a bad guess can never reach the
watchlist. That is the same discipline the hand-curated list already recorded
("each live-probed, returned >=1 open role") — just run in bulk instead of by hand.

On yield: this module deliberately publishes no hit-rate percentage. `mine` caps
CDX ROWS rather than distinct companies, and CDX returns rows SURT-sorted, so any
rate measured from a capped query describes an alphabetically-truncated,
popularity-weighted slice — not the population. Earlier versions of this docstring
quoted such figures as if they were population rates, with no artifact behind them
to check. Run your own query and count if you need a number.
"""

from __future__ import annotations

import http.client
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from . import config
from .dedup import ats_from_url
from .sources import liveness_for
from .util import NET_ERRORS

CDX_COLLINFO = "https://index.commoncrawl.org/collinfo.json"


class DiscoveryError(RuntimeError):
    """Common Crawl was unreachable/overloaded — transient and retryable.

    Defined here rather than imported from seed because seed depends on this
    module, not the other way round; `seed.SeedError` is an alias of this name so
    `except seed.SeedError` keeps working for existing callers.
    """


# Errors that mean "Common Crawl is having a bad day", not "this code is wrong".
_CDX_ERRORS = (
    *NET_ERRORS,
    urllib.error.HTTPError,
    ConnectionError,
    http.client.HTTPException,
    KeyError,
    IndexError,
)

# The CDX url-pattern to query per ATS. Slug EXTRACTION is not here: the five
# single-key ATSs go through dedup.ats_from_url, the one parser that already knows
# greenhouse's `job-boards.`/`boards.eu.` hosts and already stops a slug at `&`
# (the embed-form bug). This module used to carry its own narrower copies of those
# regexes, and seed.py carried a third set that still had the `&` bug — three
# parsers disagreeing on the same URL. Now there is one, plus the Workday triple
# below, which ats_from_url has no reason to know about.
_PATTERNS = {
    "greenhouse": "boards.greenhouse.io/*",
    "lever": "jobs.lever.co/*",
    "ashby": "jobs.ashbyhq.com/*",
    "workable": "apply.workable.com/*",
    "smartrecruiters": "jobs.smartrecruiters.com/*",
    "workday": "*.myworkdayjobs.com/*",
}

# Workday is the one ATS a job URL cannot be reduced to a single slug: it needs
# tenant + numbered host shard + site slug. The locale segment is optional AND
# case-insensitive — `en-us` in the wild is as common as `en-US`, and matching only
# the latter meant the locale was captured as the site slug, then dropped by
# _NOT_SLUGS, losing the entire tenant.
_WORKDAY_RE = re.compile(
    r"^https?://([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/"
    r"(?:[A-Za-z]{2}-[A-Za-z]{2}/)?([A-Za-z0-9_-]+)",
    re.I,
)

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
    "job_app",
    "j",
    "jobs",
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
    try:
        with urllib.request.urlopen(req, timeout=cfg.timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))[0]["id"]
    except _CDX_ERRORS as e:
        raise DiscoveryError(
            f"Common Crawl index unavailable ({type(e).__name__})"
        ) from e


def _entry(ats: str, url: str) -> tuple | None:
    """One CDX url -> (dedup key, watchlist entry), or None if it isn't a board.

    Workday needs its own regex for the tenant/host/site triple; everything else
    defers to dedup.ats_from_url so this module cannot drift from the parser the
    rest of the package uses.
    """
    if ats == "workday":
        m = _WORKDAY_RE.match(url)
        if not m:
            return None
        tenant, host, site = m.group(1), m.group(2), m.group(3)
        if site.lower() in _NOT_SLUGS:
            return None
        return (tenant.lower(), host.lower(), site.lower()), {
            "ats": ats,
            "slug": tenant,
            "host": host.lower(),
            "site": site,
        }
    got = ats_from_url(url)
    # ats_from_url lowercases the URL before matching, so a slug comes back
    # lowercase; that is fine for every ATS here (boards are case-insensitive)
    # and it makes the dedup key and the stored slug agree.
    if not got or got[0] != ats:
        return None
    slug = got[1]
    if slug.lower() in _NOT_SLUGS:
        return None
    return slug.lower(), {"ats": ats, "slug": slug}


def mine(
    ats: str,
    collection: str | None = None,
    limit: int = 4000,
    cdx_url: str | None = None,
) -> list[dict]:
    """Stage 1 — pull candidate watchlist entries for one ATS out of the CDX index.

    Returns UNVERIFIED candidates; every one still has to survive `probe`.

    `limit` caps CDX ROWS, not distinct companies, and CDX returns rows in
    SURT-sorted order — so a capped query is an alphabetically-truncated slice
    weighted toward boards with many crawled URLs, not a random sample. Treat any
    yield rate computed from it as an upper bound on that slice, not a population
    rate.

    `cdx_url` lets a caller supply an already-resolved index endpoint (seed does,
    from its own collection lookup) instead of paying for a second one.
    """
    if ats not in _PATTERNS:
        raise ValueError(f"no CDX pattern for ats={ats!r}")
    pattern = _PATTERNS[ats]
    cfg = config.active()
    if cdx_url:
        base = cdx_url
    else:
        base = (
            f"https://index.commoncrawl.org/{collection or latest_collection()}-index"
        )
    url = f"{base}?url={urllib.parse.quote(pattern, safe='')}&output=json&limit={limit}"
    req = urllib.request.Request(url, headers={"User-Agent": cfg.user_agent})

    seen, out = set(), []
    try:
        # Stream rather than buffering the whole body: CDX is newline-delimited
        # JSON and a wide `limit` is megabytes.
        with urllib.request.urlopen(req, timeout=max(cfg.timeout, 90)) as r:
            for raw in r:
                line = raw.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                got = _entry(ats, rec.get("url", ""))
                if not got:
                    continue
                key, entry = got
                if key in seen:
                    continue
                seen.add(key)
                out.append(entry)
    except _CDX_ERRORS as e:
        raise DiscoveryError(
            f"Common Crawl CDX unavailable ({type(e).__name__})"
        ) from e
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
    annotated with the `roles` count that proved it.

    `roles` is the ATS's own reported total (a cheap one-shot liveness call), NOT
    the length of a full harvest — so for Workday it is the true open-role count
    rather than the 200 a capped fetch would return. Callers that sort by it, or
    display it, are seeing a bigger and more accurate number than before 0.4.0.

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
        # A LIVENESS call, not the full harvest adapter. Answering "does this board
        # exist" by downloading every job body cost up to 210 requests per Workday
        # candidate — which is what tripped the rate limiter the 429 branch below
        # exists to survive. Measured against a live tenant: 210 -> 1.
        probe_fn = liveness_for(c["ats"])
        if not probe_fn:
            return {**c, "outcome": "unsupported"}
        kwargs = {k: c[k] for k in ("host", "site") if k in c}
        try:
            n_roles = probe_fn(c["slug"], **kwargs)
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
        if not n_roles:
            return {**c, "outcome": "empty"}
        if require_identity and c.get("name"):
            if not verify_identity(c["ats"], c["slug"], c["name"]):
                return {**c, "outcome": "wrong-owner"}
        return {
            **c,
            "roles": n_roles,
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
# The LEGAL-entity subset of the stopwords above. Removing one of these and collapsing
# to a single token is SAFE — 'ACME LLC' -> 'acme' IS Acme's real slug, because a legal
# suffix is not part of the trade name. Collapsing after removing a TRADE word instead
# (`company`/`the`/`group`/`holdings`) is what's risky: 'Capital Group' -> 'capital' is
# a fragment, not the company. name_variants() uses this to tell the two apart.
_LEGAL_SUFFIX = re.compile(
    r"\b(inc|llc|l\.l\.c|ltd|limited|corp|corporation|co|plc|gmbh|sa|nv|ag"
    r"|pte|pty|llp|lp)\b",
    re.I,
)


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
    first-resonance. Verified 2026-07-22 against live boards.

    `aggressive` adds the bare first word ('Capital One' -> `capital`). That variant
    is a correctness trap on its own: a generic first word frequently IS a real board
    owned by an unrelated company — `veterans` is IntelliDyne's, and `jobs.lever.co/
    capital` is a live board that is not Capital One's — and a liveness probe cannot
    tell the difference. It is safe ONLY when the caller also verifies board
    ownership (see verify_identity), so callers must opt in deliberately.

    The same trap hides inside NORMALIZATION. _norm_name strips both legal suffixes
    AND trade words (`group`, `company`, `holdings`, `the`), so 'Capital Group'
    collapses to the single token 'capital' — and then the "conservative" flat and
    dashed variants ARE that bare generic word, with no `aggressive` opt-in. That
    produced a real false binding (Capital Group -> lever/capital, Capital.com's
    board). But collapsing after only a LEGAL suffix is removed is fine — 'ACME LLC'
    -> 'acme' is Acme's real slug. So the gate fires only when a TRADE word caused the
    collapse (removing just legal suffixes still leaves >1 word), treating that token
    like the bare-first-word variant: withheld unless `aggressive`.
    """
    depunct = _PUNCT.sub(" ", (name or "").lower())
    base = _norm_name(name)
    if not base:
        return []
    tokens = base.split(" ")
    sans_legal = _LEGAL_SUFFIX.sub(" ", depunct).split()
    # a TRADE word collapsed a multi-word name to one token (legal-only collapse is safe)
    risky_collapse = len(tokens) == 1 and len(sans_legal) > 1
    if risky_collapse and not aggressive:
        return []
    cands = [base.replace(" ", ""), base.replace(" ", "-")]
    if aggressive:
        cands.append(tokens[0])
    return [v for v in dict.fromkeys(cands) if 2 < len(v) < 40]


def match_known(names: list[str], universe: list[dict]) -> list[dict]:
    """Stage 0 — match company names against an ALREADY-MINED slug universe.

    Strictly better than guessing when the universe covers the company: the slug is
    real by construction (CDX saw it), so the probe is a confirmation rather than a
    lottery. Exact match on a normalized variant only — fuzzy matching a company name
    to a slug invites false pairs ('agency' matching half the corpus), and a wrong
    slug silently attributes another company's jobs to this one.
    """
    # Index by slug ONCE. This used to build `by_slug` and then throw the lookup
    # away, rescanning the whole universe for every variant of every name —
    # O(names x variants x universe). Measured 428.8ms -> 6.22ms at 5k x 5k, with
    # byte-identical output (pinned by a brute-force differential test).
    #
    # Ordering is load-bearing, so it is preserved exactly: keys stay in universe
    # insertion order, first-wins on a duplicate (ats, slug), and a variant that
    # only matches already-taken keys keeps scanning for a free one.
    by_slug: dict[tuple, dict] = {}
    by_variant: dict[str, list[tuple]] = {}
    for e in universe:
        key = (e["ats"], e["slug"].lower())
        if key in by_slug:
            continue
        by_slug[key] = e
        by_variant.setdefault(key[1], []).append(key)
    out, seen = [], set()
    for name in names:
        for v in name_variants(name):
            for key in by_variant.get(v, ()):
                if key not in seen:
                    seen.add(key)
                    out.append({**by_slug[key], "name": name, "source": "name-match"})
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
