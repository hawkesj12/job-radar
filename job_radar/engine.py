"""The harvest pipeline: poll depth (ATS) + breadth (aggregator) sources,
filter -> score -> dedup into one ranked list, and RETURN any newly-discovered ATS
slugs for the caller to persist. Returns scored postings; `shortlist` writes them.
The engine itself writes nothing."""

from __future__ import annotations

import itertools
import json
import re
import urllib.error
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from . import config
from .dedup import find_hit_key, norm
from .funnel import funnel
from .scoring import is_remote, relevant, score_and_signals
from .sources import (
    DEPTH_ACCEPTS_KEEP,
    DEPTH_ALL,
    DEPTH_EXTRA_FIELDS,
    enabled_breadth,
    enabled_depth,
)
from .util import age_int

# A valid ATS slug is the last path segment of a board URL — alphanumerics plus
# -, _, . only. Reject anything else so a hand-edited watchlist can't inject path
# traversal (`../`) or a query into the fixed API URLs the slug is spliced into.
_SLUG_RE = re.compile(r"^[A-Za-z0-9._-]+$")

# Source preference — NOT a fit-score input (the score stays source-agnostic:
# score_and_signals reads only the posting's content). It decides WHICH COPY of one
# role to keep, and among equal-score DIFFERENT roles which to surface first.
# Higher = more preferred.
#
# This used to be `{"google_jobs": 1}` and nothing else, which meant a company's own
# Greenhouse board ranked EQUAL to a RemoteOK redirect — the one distinction the
# product is built on, absent from the table that exists to express it. A DEPTH
# source IS the employer's applicant-tracking system, so its copy is the canonical
# record: the real apply URL, the full description, the accurate department. Google
# for Jobs sits in the middle because its apply_options resolve to direct-to-company
# links. Everything else is an aggregator serving a redirect.
_DEPTH_PREF, _GOOGLE_PREF, _AGGREGATOR_PREF = 2, 1, 0


def _src_pref(p) -> int:
    src = p.get("source", "")
    if src in DEPTH_ALL:
        return _DEPTH_PREF
    return _GOOGLE_PREF if src == "google_jobs" else _AGGREGATOR_PREF


# Every text field the pipeline treats as a string. A JSON `null` from any of ~500
# third parties arrives as None, and `.get(k, "")` does NOT protect against it — the
# default only fires when the key is ABSENT, so a present-but-null title yields None
# and the first `.lower()` raises. That crash escaped `harvest`'s per-source error
# handling entirely, because `_consume` runs OUTSIDE both try blocks: one malformed
# posting killed the whole run, discarded a completed network harvest, and skipped
# the "keep your existing shortlist" guard on the way out. Coerce once, here, at the
# only boundary every posting from every adapter has to cross.
_TEXT_FIELDS = (
    "title",
    "company",
    "location",
    "url",
    "posted",
    "text",
    "department",
    "employment_type",
    "salary",
    "industry",
    "source",
)


# The keys added in 0.7.0, and their UNKNOWN value. `None` is the default on every
# one of them, and that is the whole point of the contract: `None` means "the source
# did not say", which is a different fact from `False` and from `""`. A consumer must
# be able to write `WHERE remote IS NOT NULL` and mean it. Defaulting an unknown to a
# plausible value is how a downstream store ends up asserting things nobody measured.
#
# Deliberately NOT in _TEXT_FIELDS: coercing these to `str` would turn None into the
# string "None" and a list into its repr, which is exactly the stringly-typed arrival
# the contract exists to prevent. They are typed:
#   remote      bool | None
#   tags        list[str] | None
#   everything else  str | None
_CONTRACT_FIELDS = (
    "function",  # the JOB FAMILY ("Healthcare & Nursing Jobs")
    "org_unit",  # the company's own team ("Engineering - Pipeline")
    "employer_org",  # the employing organisation -- NEVER a category
    "city",
    "state",
    "country",
    "remote",  # bool | None
    "remote_basis",  # "source_field" | "location_rule" | "text" | None
    "tags",  # list[str] | None
    "seniority",  # the source's own level string, verbatim
)


def _coerce(p: dict) -> dict:
    """Enforce the record contract at the one boundary every posting crosses.

    Two jobs, and they are opposites on purpose. The legacy text fields are FORCED to
    `str` (a present-but-null title from any of ~500 third parties used to raise on the
    first `.lower()` and kill an entire harvest). The 0.7.0 contract fields are merely
    ENSURED PRESENT, defaulting to None, and are never coerced -- their types carry
    meaning that `str()` would destroy.
    """
    for k in _TEXT_FIELDS:
        v = p.get(k)
        if not isinstance(v, str):
            p[k] = "" if v is None else str(v)
    for k in _CONTRACT_FIELDS:
        p.setdefault(k, None)
    # A source that sends a single tag as a bare string still satisfies "list[str] |
    # None" downstream if we normalize here rather than making every consumer guess.
    if isinstance(p.get("tags"), str):
        p["tags"] = [p["tags"]] if p["tags"] else None
    return p


def _consume(postings, hits, blocks, cfg, meta):
    for p in postings:
        _coerce(p)
        if not relevant(p.get("title", ""), cfg):
            continue
        if not is_remote(p, cfg):
            continue
        age = age_int(p.get("posted", ""))
        if age is not None and age > cfg.max_age_days:
            continue
        if not p.get("url"):
            continue
        p.setdefault("company", "")
        p.setdefault("source", "")
        p.setdefault("industry", "")
        m = meta.get(norm(p["company"]))
        sc, sig = score_and_signals(p, cfg=cfg)  # one keyword scan for both
        tl = p["title"].lower()
        if m and m.get("frontier") and not any(d in tl for d in cfg.applied_door):
            sc -= cfg.frontier_penalty
            sig = "frontier-reach" + (", " + sig if sig else "")
        if m and m.get("local"):
            sc += cfg.local_bonus
            sig = "local" + (", " + sig if sig else "")
        if age is not None and age > cfg.stale_after_days:
            sc -= min(12, ((age - cfg.stale_after_days) // 10) * 2)
            sig = (sig + ", " if sig else "") + f"{age}d-old"
        if m and m.get("industry") and not p["industry"]:
            p["industry"] = m["industry"]
        p["score"] = sc
        p["signals"] = sig
        p["sources"] = {p["source"]} if p["source"] else set()

        # find_hit_key computes dedup_key/block/normalized-title once and returns
        # them, so the insert branch below reuses them instead of re-deriving.
        match, key, blk, nt = find_hit_key(p, hits, blocks, cfg)
        if match is None:
            p["_blk"] = blk  # block + normalized title, stashed for the fuzzy pass
            p["_nt"] = nt
            p["dedup_key"] = key  # stash so the store/CLI don't recompute it
            hits[key] = p
            if blk:
                blocks.setdefault(blk, []).append(key)
        else:
            cur = hits[match]
            srcs = cur["sources"] | p["sources"]
            # PROVENANCE first, then completeness, and score only as a last resort.
            #
            # Score used to lead here, and that inverted the product. These two rows
            # are the SAME JOB, so "which fits better" is not a meaningful question
            # between them — the only question is which record is the better copy of
            # it. Score-first answered it with a number that rewards brevity, so an
            # 80-word aggregator stub beat the employer's own 15,000-character
            # posting and the user got a RemoteOK redirect instead of the company's
            # ATS link. Measured on a live board: 14 of 20 merged roles.
            #
            # Score stays as the final key purely so the choice is deterministic when
            # provenance and length are identical.
            if (_src_pref(p), len(p.get("text", "")), p["score"]) > (
                _src_pref(cur),
                len(cur.get("text", "")),
                cur["score"],
            ):
                winner = p
            else:
                winner = cur
            winner["sources"] = srcs
            # The retained key stays `match`; carry the precomputed block/title so
            # future fuzzy compares use the WINNER's title (a new winner `p` was
            # never inserted, so derive its `_nt`; `_blk` is the shared block).
            if winner is p:
                winner["_blk"], winner["_nt"] = cur["_blk"], nt  # p's own title
            winner["dedup_key"] = match
            hits[match] = winner


def harvest(cfg=None, watchlist_path=None, companies=None):
    """Run a full scan. Returns (rows, discovered, errors).

    The company universe arrives one of two ways:
      - `companies` — a list of entries, passed in by a caller that owns its own
        store (jobfitr keeps its universe in SQLite, not a file).
      - `watchlist_path` — a JSON file, which is how the standalone CLI works.

    Taking DATA rather than a path is what keeps this a library: the engine no
    longer needs to know where a caller's companies live, or be able to read disk
    at all. `discovered` is likewise RETURNED, never written — persistence is the
    caller's business (see cli.cmd_scan, which appends to its watchlist.json).
    """
    cfg = cfg or config.active()
    watchlist_err = None
    if companies is None:
        companies = []
        if watchlist_path:
            try:
                with open(watchlist_path, encoding="utf-8") as f:
                    companies = json.loads(f.read()).get("companies", [])
            except (OSError, json.JSONDecodeError) as e:
                # A corrupt/unreadable watchlist must be LOUD -- silently dropping the
                # entire depth harvest (all your companies) is the one place this tool
                # would betray its own fail-fast rule. Surfaced via `errors` below.
                watchlist_err = f"watchlist {watchlist_path}: {type(e).__name__}"

    meta, known_slugs = {}, set()
    for c in companies:
        meta[norm(c.get("name", ""))] = {
            "frontier": bool(c.get("frontier")),
            "local": bool(c.get("local")),
            "industry": c.get("industry", ""),
        }
        known_slugs.add((c.get("ats"), (c.get("slug") or "").lower()))
    known_companies = set(meta.keys())

    depth = enabled_depth(cfg)
    # `hits` = deduped roles keyed by dedup_key; `blocks` = company-block index
    # (block -> [key]) that keeps the fuzzy de-dup linear. Both persist across the
    # depth + breadth _consume passes; _consume mutates them ONLY on the main
    # thread (workers just fetch), so the shared state needs no lock.
    hits, blocks, errors = {}, {}, []
    if watchlist_err:
        errors.append(watchlist_err)

    def _fetch_company(c):
        ats, slug = c.get("ats"), c.get("slug")
        name = c.get("name", slug or "?")
        fetch = depth.get(ats)
        if not fetch:
            return (c, None, f"{name}: source '{ats}' not enabled")
        if not slug or not _SLUG_RE.match(slug):
            return (c, None, f"{name}: invalid slug {slug!r}")
        # Most ATSs key on the slug alone; Workday needs host + site too. Pull only
        # the fields that adapter declared, and fail LOUD on a missing one rather
        # than fetching a wrong-but-valid URL.
        extra = {}
        for field in DEPTH_EXTRA_FIELDS.get(ats, ()):
            val = c.get(field)
            if not val or not _SLUG_RE.match(str(val)):
                return (c, None, f"{name} ({ats}): missing/invalid {field}={val!r}")
            extra[field] = val
        # Hand the relevance gate DOWN to the adapters that buy bodies one request at
        # a time (workday, rippling). They apply it to the list titles before the
        # detail pass, so the harvest stops paying for descriptions it is about to
        # discard here in _consume moments later. Same predicate, same result set,
        # roughly half the requests -- see fetch_workday's docstring for the numbers.
        if ats in DEPTH_ACCEPTS_KEEP:
            extra["keep"] = lambda t: relevant(t, cfg)
        try:
            return (c, fetch(slug, **extra), None)
        except urllib.error.HTTPError as e:
            return (c, None, f"{name} ({ats}:{slug}): HTTP {e.code}")
        except Exception as e:  # noqa: BLE001
            return (c, None, f"{name} ({ats}:{slug}): {type(e).__name__}")

    if companies:
        # A SLIDING WINDOW of in-flight futures, not `ex.map`. `map` submits every
        # company up front, so all ~500 result lists -- each a full set of job
        # descriptions -- are alive at once whether or not the consumer has caught
        # up: measured ~1.25 GB peak RSS at 500 companies, for work that only ever
        # needs 12 boards in memory. Here at most `window` results exist, and each
        # is released as soon as _consume has folded it into `hits`.
        #
        # Consumption stays SINGLE-THREADED on this thread, which is the property
        # that lets `hits`/`blocks` go without a lock (see their comment above).
        # Workers only fetch.
        window = 24  # 2x max_workers: enough to keep every worker fed, no more
        with ThreadPoolExecutor(max_workers=12) as ex:
            pending, queue = set(), iter(companies)
            for c in itertools.islice(queue, window):
                pending.add(ex.submit(_fetch_company, c))
            while pending:
                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                for c in itertools.islice(queue, len(done)):  # top the window back up
                    pending.add(ex.submit(_fetch_company, c))
                for fut in done:
                    c, ps, err = fut.result()
                    if err:
                        errors.append(err)
                        continue
                    name, ats = c.get("name", c.get("slug") or "?"), c.get("ats")
                    for p in ps:
                        p["company"], p["source"], p["industry"] = (
                            name,
                            ats,
                            c.get("industry", ""),
                        )
                    _consume(ps, hits, blocks, cfg, meta)

    # Breadth sources are independent third-party hosts — fetch them in
    # parallel (like depth) and consume single-threaded in a stable order. No
    # cross-host sleep: rate limits are per-host, and each source already sleeps
    # between its OWN repeated calls.
    def _fetch_breadth(item):
        name, fn = item
        try:
            return (name, fn(cfg.title_queries), None)
        except Exception as e:  # noqa: BLE001
            return (name, None, f"breadth:{name}: {type(e).__name__}")

    breadth_postings = []
    breadth = enabled_breadth(cfg)
    if breadth:
        with ThreadPoolExecutor(max_workers=min(len(breadth), 10)) as ex:
            for name, ps, err in ex.map(_fetch_breadth, breadth):
                if err:
                    errors.append(err)
                    continue
                breadth_postings += ps
                _consume(ps, hits, blocks, cfg, meta)

    # Slug discovery RETURNS candidates; it does not persist them. The engine used to
    # append straight into the caller's watchlist.json from here, which made a library
    # function silently write a file the caller owned — and left a store-backed caller
    # (jobfitr) with nowhere to put them. Whoever owns the universe decides.
    discovered = []
    if cfg.funnel_auto_grow:
        try:
            discovered = funnel(breadth_postings, known_companies, known_slugs, cfg)
        except Exception as e:  # noqa: BLE001 — discovery must never sink a scan
            errors.append(f"funnel: {type(e).__name__}")

    # Score desc, with source preference breaking exact-score ties (Google's
    # lower-noise, direct-link results edge out an equal-scoring aggregator row).
    rows = sorted(hits.values(), key=lambda p: (p["score"], _src_pref(p)), reverse=True)
    # Strip the de-dup scratch before handing rows to a caller. `_blk` (company block)
    # and `_nt` (normalized title) are stashed by _consume so the fuzzy pass does not
    # re-derive them per comparison; they are an implementation detail of THIS
    # function and were leaking into every consumer's record — jobfitr stores what
    # harvest returns, so two private keys were crossing a package boundary and would
    # have had to be supported forever once anyone read them.
    for r in rows:
        r.pop("_blk", None)
        r.pop("_nt", None)
    return rows, discovered, errors
