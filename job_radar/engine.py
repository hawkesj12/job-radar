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

from . import config, vocab
from .dedup import entry_key, find_hit_key, norm
from .funnel import funnel
from .scoring import is_remote, relevant, remote_signal, score_and_signals
from .sources import (
    DEPTH_ACCEPTS_KEEP,
    DIRECT_APPLY_SOURCES,
    DEPTH_ALL,
    DEPTH_EXTRA_FIELDS,
    enabled_breadth,
    enabled_depth,
)
from .util import age_int, now_et

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
# `employment_type` is NOT here, and that was a real contract break while it was:
# `norm or ""` wrote the empty string on 680 of 2,747 live rows (24%) -- 100% of
# greenhouse, remoteok and teamtailor -- and "" is not a member of
# vocab.EMPLOYMENT_TYPES. It is the exact None-vs-empty lie the contract exists to
# remove, and it was invisible from the NDJSON because emit masks it with `or None`;
# only the flat dict a library consumer receives carried it.
_REQUIRED_TEXT = ("title", "company", "url", "source", "industry")


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
    # what the job is
    "title_root",  # the matchable role, decoration stripped -- vocab.decompose_title
    "title_level",  # I | II | III | IV
    "title_qualifiers",  # list[str] | None -- domain/geo decoration
    "category",  # the job family. NOT an O*NET-SOC code; free text from the source.
    "tags",  # list[str] | None -- skills the source itself extracted
    # who
    "parent_company",  # umbrella org, when the source distinguishes one
    "team",  # the group inside the company
    # where
    "locations",  # list[dict] | None -- every place, each with its own apply url
    "city",
    "state",
    "country",
    "remote_type",  # remote | hybrid | onsite | None
    # WHERE a remote worker may sit -- the eligibility boundary, which is a different fact
    # from where the work is (that is city/state/country). Three fields because they are
    # three different kinds of thing; one column holding all of them is the failure this
    # replaced. `remote_areas` is [] when a posting states it is unbounded and None when it
    # states nothing -- those are not the same and a consumer must be able to tell.
    #
    # Per schema.org's applicantLocationRequirements, which models this concept: it records
    # where applicants may APPLY FROM and is explicitly NOT a citizenship or work-visa claim.
    "remote_areas",  # list[str] | None -- ISO alpha-2 / 3166-2. [] = stated worldwide
    "remote_regions",  # list[str] | None -- closed tokens, never a country code
    "remote_scope_raw",  # str | None -- the vendor's own words, kept verbatim
    "remote_basis",  # stated | board | location | text | None -- vocab.REMOTE_BASES
    # money
    "salary_min",  # float | None -- what an employer COMMITTED to
    "salary_max",
    "salary_currency",
    "salary_period",  # year | month | week | day | hour | fixed | None
    "salary_basis",  # stated | text | None -- vocab.SALARY_BASES
    "salary_estimated_min",  # a MODEL's guess. Never the same column as a commitment.
    "salary_estimated_max",
    # terms
    "employment_type_raw",  # what the vendor actually said
    "seniority",
    "seniority_basis",  # stated | title | None
    # when
    "posted_basis",  # stated | relative | None
    "expires",
    "harvested_at",
    # provenance
    "direct_apply",  # bool -- the url reaches the EMPLOYER, not an aggregator
    "remote",  # bool | None -- DERIVED from remote_type; see _coerce
    # dict | None -- the third tier. Fields ONE source sends that no other can, kept
    # verbatim so nothing is lost while the core stays queryable. The promotion rule:
    # a field enters the core contract when TWO OR MORE sources can fill it; a
    # genuine one-source quirk lives here. Never index this; read it by key.
    "source_extra",
)

# Fields whose ABSENCE is meaningful and must survive as None rather than "".
# `posted: ""` used to mean "we could not parse a date", which is the same lie as
# `remote: False` for unknown -- a consumer cannot tell it from a job with no date.
# These move out of the str-coerced tier. The four genuinely required fields
# (title, url, company, source) stay strings: _consume drops any row missing a url,
# and a posting with no title is not a posting.
# `text` deliberately keeps its name. `body` reads better, but it is a released,
# README-documented field with call sites in the only consumer, and renaming it buys
# a nicer word and nothing else. If it ever moves it moves at 1.0 with `department`.
_NULLABLE_TEXT = (
    "posted", "salary", "text", "location", "department", "employment_type",
)  # fmt: skip


def _coerce(p: dict) -> dict:
    """Enforce the record contract at the one boundary every posting crosses.

    Everything that makes the record a contract rather than a pile of vendor JSON
    happens here, because this is the only place every posting from every adapter
    has to pass through. An adapter fills what its API sends; this makes the result
    uniform.

    Four jobs:

    1. REQUIRED fields are forced to `str`. A present-but-null title from any of
       ~500 third parties used to raise on the first `.lower()` and kill an entire
       harvest.
    2. OPTIONAL text is normalized to None, not "". `posted: ""` meant "we could not
       parse a date", which a consumer cannot tell from a job that has no date --
       the same lie as `remote: False` for unknown.
    3. Contract fields are ensured present and default to None, and are NEVER
       coerced: `str(None)` is "None" and `str(["a"])` is a repr, which is exactly
       the stringly-typed arrival the contract exists to prevent.
    4. Derived fields are computed from what the adapter did send -- the title
       decomposition, and `seniority` when the source stayed silent.
    """
    for k in _REQUIRED_TEXT:
        v = p.get(k)
        if not isinstance(v, str):
            p[k] = "" if v is None else str(v)
    for k in _NULLABLE_TEXT:
        v = p.get(k)
        if v is None or v == "":
            p[k] = None
        elif not isinstance(v, str):
            p[k] = str(v)
    for k in _CONTRACT_FIELDS:
        # `""` is NOT a legal contract value. These fields are typed `X | None`, and
        # an adapter can easily hand over an empty string by accident -- `to_date`
        # returns "" for an unparseable or absent date, so `expires` arrived as ""
        # on every posting whose deadline was null. An empty string that means
        # "absent" is the same lie the whole contract exists to remove.
        v = p.get(k)
        p[k] = None if v is None or v == "" else v
    # A source that sends a single tag as a bare string still satisfies "list[str] |
    # None" downstream if we normalize here rather than making every consumer guess.
    if isinstance(p.get("tags"), str):
        p["tags"] = [p["tags"]] if p["tags"] else None

    # DERIVED. The title decomposition runs for every adapter, because no source
    # sends a root -- it is ours to produce, and producing it once here beats every
    # consumer reimplementing it differently. `title` itself is never modified.
    parsed = vocab.decompose_title(p.get("title", ""))
    p["title_root"] = parsed["title_root"]
    p["title_level"] = p.get("title_level") or parsed["title_level"]
    p["title_qualifiers"] = p.get("title_qualifiers") or parsed["title_qualifiers"]
    # A STATED seniority always wins; the title is the fallback, and says so.
    if p.get("seniority"):
        p.setdefault("seniority_basis", "stated")
        p["seniority_basis"] = p["seniority_basis"] or "stated"
    elif parsed["seniority"]:
        p["seniority"] = parsed["seniority"]
        p["seniority_basis"] = "title"

    # `remote` is DERIVED from remote_type, not stored beside it. Two homes for one
    # fact is how a hybrid role ends up reported as `remote: False`, which reads as
    # on-site to anything that only checks the flag.
    rt = p.get("remote_type")
    p["remote"] = None if rt is None else rt == "remote"

    # `country` IS ALPHA-2 OR None, enforced here so no adapter can reintroduce a
    # second vocabulary. A live probe across all nineteen sources found three at once
    # in this one column: UPPER alpha-2 from most, LOWERCASE from smartrecruiters
    # ('de', 'us' -- 79/100 rows) and DISPLAY NAMES from workable ('United States',
    # 28/28, while its own locations[] said 'US' on the same row). Grouping by country
    # split every country into pieces.
    #
    # An unrecognised name becomes None rather than riding through: country_code
    # refuses to guess, and the vendor's original text is never lost -- it is still in
    # `location` and in `locations[].raw`.
    if p.get("country") is not None:
        p["country"] = vocab.country_code(p["country"])

    # US STATES ARE CODES, symmetric with the line above and for the same reason.
    # This column is deliberately two vocabularies -- a two-letter code inside the US,
    # the source's own subdivision name outside it, because "Greater London" and
    # "Attica" have no code to map to. But that rule was FALSE on 580 measured rows:
    # ashby sent "California" 518 times, workable "New York", adzuna "Michigan", so
    # `state='California'` and `state='CA'` named the same place in one column and a
    # US-state filter missed 566 of ashby's 737 rows. Every one of the 580 mapped
    # cleanly; none was a county or a metro that would map wrong.
    #
    # `or p["state"]` keeps an unrecognised US subdivision rather than nulling it --
    # this may only canonicalize, never discard. Non-US rows are untouched.
    if p.get("country") == "US" and p.get("state"):
        p["state"] = vocab.us_state_code(p["state"]) or p["state"]

    # GEOGRAPHY FALLBACK, once here instead of in six adapters. Two big depth
    # sources were discarding geography they already had: greenhouse filled
    # city/state/country on 0 of 396 live rows while split_place resolves 358 of the
    # `location` strings it emits ("Sydney, Australia", "Dublin, IE"), and rippling
    # 0 of 193 where split_place resolves 193.
    #
    # ONLY fills what the adapter left None, so a source that sends real structured
    # geography always wins -- this can add, never overwrite. split_place refuses
    # anything it cannot read with confidence, so an unparseable location stays None
    # rather than becoming a guess.
    #
    # STRIP THE ARRANGEMENT WORDS FIRST, THEN FALL BACK TO THE RAW STRING. This block used
    # to parse the raw location only, while `vocab.remote_scope` strips first -- so
    # "Atlanta, GA - Remote" resolved to nothing here even though the parser reads it fine
    # once "Remote" is out of the way. Measured: 3,479 rows go empty -> resolved.
    #
    # The fallback is not belt-and-braces, it is required. Stripping INSTEAD OF parsing raw
    # regresses "Remote, TX", "Remote, CO" and "Hybrid, NY": those resolve today because the
    # tail is a US state and the head is placeless, and stripping leaves a bare "TX" with no
    # comma, which split_place cannot read. Those are exactly the stated bare-state rows the
    # boundary field exists to carry, so losing them would be the worst possible trade.
    if p.get("city") is None and p.get("state") is None and p.get("country") is None:
        first = (p.get("location") or "").split(";")[0]
        bare = vocab.strip_arrangement(first)
        place = vocab.split_place(bare)
        if not any(place.values()):
            place = vocab.split_place(first)
        for k, v in place.items():
            if v is not None:
                p[k] = v

    # `locations` -- every place ONE posting names. Greenhouse separates them with
    # `;` ("Berlin, Germany; Munich, Germany") and 143 of 810 postings on one measured
    # board do this. That is a single job id, so it stays a single row; the places
    # ride here instead of forcing a split. An adapter that already built a
    # structured list (usajobs PositionLocation[], rippling workLocations[]) keeps it.
    if p.get("locations") is None:
        parts = [x.strip() for x in (p.get("location") or "").split(";") if x.strip()]
        if len(parts) > 1:
            # SAME KEYS as the single-place branch below. This used to emit only
            # {raw, url}, so 644 of 3,153 live elements had no city/state/country
            # key at all and a consumer doing `l["city"]` raised on a fifth of the
            # list. The parsed values are per-place, so each is read from its own
            # string rather than copied from the row's first place.
            p["locations"] = [
                {"raw": x, **vocab.split_place(x), "url": p.get("url")} for x in parts
            ]
        elif parts:
            p["locations"] = [
                {
                    "raw": parts[0],
                    "city": p.get("city"),
                    "state": p.get("state"),
                    "country": p.get("country"),
                    "url": p.get("url"),
                }
            ]

    # employment_type is NORMALIZED here, once, for all nineteen adapters rather
    # than in each of them. Nineteen vendors send nineteen spellings of the same
    # eight ideas -- "Full-time", "FULL_TIME", "Regular Full Time (Salary)",
    # "permanent" -- and a facet a consumer cannot filter on is not a facet. The
    # vendor's exact string is preserved in employment_type_raw; nothing is lost.
    #
    # NOTE this changes the VALUE SEMANTICS of a released field: `employment_type`
    # used to carry the vendor string and now carries the closed-vocabulary value.
    # That is a deliberate break, and the raw is one key away.
    if p.get("employment_type_raw") is None:
        incoming = p.get("employment_type")
        norm, raw = vocab.employment_type(incoming)
        p["employment_type"] = norm  # None when the source said nothing, never ""
        # `raw` means "what the VENDOR actually said", so it must not be back-filled
        # with a value the vendor never sent. An adapter that already mapped into the
        # closed vocabulary -- usajobs turns PositionSchedule Code "1" into FULL_TIME,
        # and .Name is empty on 47 of 50 measured rows -- was landing here with
        # employment_type=FULL_TIME and raw=None, and this wrote FULL_TIME into the
        # raw as though the vendor had said it. Leave it None: absent is honest, and
        # inventing a quotation is the one thing a provenance field cannot do.
        p["employment_type_raw"] = (
            None if str(incoming or "") in vocab.EMPLOYMENT_TYPES else raw
        )

    # `direct_apply` -- does this url reach the EMPLOYER, or an aggregator that will
    # bounce you onward? It is the product's whole differentiator, and _src_pref has
    # been computing exactly this distinction for the dedup tiebreak since 0.5.0 and
    # throwing it away. A DEPTH source IS the employer's applicant-tracking system,
    # so its link is direct by construction; an aggregator serves a redirect.
    # google_jobs decides for itself (see _best_apply_link) and is left alone here.
    if p.get("direct_apply") is None and p.get("source"):
        p["direct_apply"] = (
            p["source"] in DEPTH_ALL or p["source"] in DIRECT_APPLY_SOURCES
        )

    p["harvested_at"] = p.get("harvested_at") or now_et()
    return p


def derive_remote(p: dict) -> dict:
    """Fill the remote arrangement AND its boundary from the posting's own text.

    RECORDS what it decided, which is the point. The gate used to call
    scoring.remote_posting, take a bare bool and write nothing -- so a row admitted
    because its title said "Remote" came out with all three fields None, indistinguishable
    from a row nobody classified. Measured on a 31,790-row harvest that is 462
    title-decided rows plus 1,642 body-decided ones, and it is why a downstream consumer
    invented `remote_basis='derived'`, a value outside REMOTE_BASES.

    ONLY fills what the adapter left None, the same discipline as the geography fallback
    in _coerce: a source that sends a real structured signal always wins. The arrangement
    and the boundary are derived independently, because a source can state one and not the
    other -- himalayas sends a country restriction on every row and says nothing per-row
    about remoteness.

    SEPARATE FROM _coerce, and deliberately called AFTER the relevance gate. This scans the
    full job body, which is the single most expensive thing the per-posting path does --
    measured, 69% of postings are discarded by a title-only relevance test microseconds
    later, so doing it inside _coerce spent most of its cost on rows that were thrown away.
    Emitted rows are unaffected: everything that survives to the store passes relevance
    first, so it has been through here.
    """
    location = p.get("location", "") or ""
    if p.get("remote_type") is None:
        rtype, rbasis = remote_signal(
            p.get("title", "") or "", location, p.get("text", "") or ""
        )
        if rtype is not None:
            p["remote_type"] = rtype
            p["remote_basis"] = rbasis

    # The BOUNDARY is derived independently of the arrangement, because a source can state
    # one without the other -- himalayas sends a country array on every row while saying
    # nothing per-row about remoteness. Only fills gaps: an adapter that sent its own
    # structured list always wins, and `[]` is a real value that must not be overwritten,
    # so this tests `is None` rather than falsiness.
    if p.get("remote_areas") is None and p.get("remote_regions") is None:
        areas, regions = vocab.remote_scope(location)
        if areas is not None:
            p["remote_areas"] = areas
        if regions is not None:
            p["remote_regions"] = regions
    if p.get("remote_scope_raw") is None and location:
        p["remote_scope_raw"] = location

    rt = p.get("remote_type")
    p["remote"] = None if rt is None else rt == "remote"
    return p


def _consume(postings, hits, blocks, cfg, meta):
    for p in postings:
        _coerce(p)
        if not relevant(p.get("title", ""), cfg):
            continue
        derive_remote(
            p
        )  # after the relevance gate: it scans the body, see its docstring
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
            p["_nt"] = nt  # (`_ref` is stashed by find_hit_key itself)
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

    A caller-supplied `cfg` GOVERNS THE WHOLE RUN, including the parts that do not
    take it as an argument. That needed saying because it was not true until
    2026-08-05: `sources._depth()` (every harvest_depth ceiling), the SerpApi quota
    guard, and the adzuna/usajobs adapters all read the process-global
    `config.active()`, so a consumer that built a Config and passed it here got the
    GLOBAL depth settings silently — the config looked applied and was inert. jobfitr
    had reverse-engineered the workaround (calling `set_active` itself) without
    anything in the docs saying it was required.

    The cost of fixing it that way, stated plainly: `harvest` now installs `cfg`
    globally for its duration and restores the previous one afterwards. Two
    concurrent `harvest()` calls with DIFFERENT configs in one process will therefore
    interfere, and a caller doing that needs its own lock.
    """
    cfg = cfg or config.active()
    with config.activated(cfg):
        return _harvest(cfg, watchlist_path, companies)


def _harvest(cfg, watchlist_path, companies):
    """The body of `harvest`, split out only so the config installation above reads as
    one line rather than wrapping two hundred."""
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
        known_slugs.add(entry_key(c))
    known_companies = set(meta.keys())

    depth = enabled_depth(cfg)
    # `hits` = deduped roles keyed by dedup_key; `blocks` = company-block index
    # (block -> [key]) that keeps the fuzzy de-dup linear. Both persist across the
    # depth + breadth _consume passes; _consume mutates them ONLY on the main
    # thread (workers just fetch), so the shared state needs no lock.
    hits: dict = {}
    blocks: dict = {}
    errors: list = []
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
        r.pop("_ref", None)  # the stashed job_ref — same reasoning as the two above
    return rows, discovered, errors
