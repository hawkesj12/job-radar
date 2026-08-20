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
    # `category` is the catalog's `function` -- the JOB FAMILY ("Data Science"), free
    # text from the source, NOT an O*NET-SOC code and NOT normalized. `team` (and its
    # deprecated alias `department`) is the catalog's `org_unit` -- the EMPLOYER'S OWN
    # group name ("Field Engineering"). `catalog/_SCHEMA.md` splits `function` /
    # `org_unit` / `employer_org` precisely because five adapters were pouring all
    # three into one column, and it records the cost: 5,050 distinct values including
    # employer names.
    #
    # SO A SOURCE THAT SHIPS ONLY AN ORG UNIT CORRECTLY LEAVES `category` None. That
    # is not an unfinished mapping, and it is worth stating because it has been filed
    # as a bug: greenhouse, ashby and lever send `departments[0].name` / `team`, which
    # are org units, so their `category` is None on 100% of rows -- while
    # smartrecruiters, an ATS on the same lane, fills it 100% because it ships a real
    # `function.label`. The dividing line is whether the VENDOR publishes a job
    # family, never whether the source is an ATS or an aggregator.
    #
    # Deriving `category` from `department` was tried downstream and reverted: it
    # looked like +17.7% coverage and its single largest effect was filing 895 rows of
    # "Senior Software Engineer, Backend" under Science and Engineering, because that
    # employer's org unit is called "Engineering". Normalizing these onto a taxonomy
    # is a consumer's judgment (`catalog/_SCHEMA.md`: "Fidelity, not opinion"), and
    # this library does not make it.
    "category",
    "tags",  # list[str] | None -- skills the source itself extracted
    # who
    "parent_company",  # umbrella org, when the source distinguishes one
    "team",  # the employer's own group -- the catalog's `org_unit`, see `category`
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
    # `stated` = a vendor's own structured field · `parsed` = read out of free text
    # (vocab.google_salary, vocab.salary_from_display). There has never been a `text`
    # token -- vocab.SALARY_BASES is frozenset({"stated", "parsed"}) and this comment
    # named a third for as long as it existed.
    "salary_basis",  # stated | parsed | None -- vocab.SALARY_BASES
    "salary_estimated_min",  # a MODEL's guess. Never the same column as a commitment.
    "salary_estimated_max",
    # terms
    "employment_type_raw",  # what the vendor actually said
    "seniority",
    "seniority_raw",  # what the vendor actually said, before case-folding
    "seniority_basis",  # stated | title | None
    # when
    "posted_basis",  # stated | relative | None
    "expires",
    "harvested_at",
    # provenance
    "direct_apply",  # bool -- the url reaches the EMPLOYER, not an aggregator
    "remote",  # bool | None -- DERIVED from remote_type; see _coerce
    # list[dict] | None -- the posting's own structure: [{type, header, start, end}]
    # where start/end index THIS record's `text`. `None` means there was no body to
    # look at; `[]` means we looked and the body carried no headers. Those are
    # different facts and a consumer must be able to tell them apart.
    #
    # THE FIRST CONTRACT FIELD DERIVED WHOLLY BY US -- no vendor sends it, which has
    # two consequences that otherwise read as violations of this file's own rules.
    # (1) There is no scalar-to-list guard like `tags` gets below: `tags` needs one
    # because a real source can send a bare string, and nothing can send a malformed
    # `sections`. (2) There is no `sections_basis`, though every other derived field
    # carries a basis -- because the retained raw `header` IS the basis. It is the
    # employer's own words next to our guess, so a misclassification stays auditable
    # and fixable in a later release without re-harvesting anything.
    "sections",
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


# Keys whose name asks "what TERM?" rather than "how many HOURS?". `permanent` is
# the one entry in vocab._EMPLOYMENT_MAP flagged by its own comment as most likely
# to be wrong -- it is UK/EU usage contrasting with fixed-term, not with part-time,
# so a permanent PART-time role lands wrong. Under a key literally named `Duration`
# or `Contract Type` the employer is answering the term question, which makes that
# flagged failure more likely rather than less. 36 rows in the live store; skipped
# rather than guessed. Every other value under those keys is still read.
_TERM_KEY = re.compile(r"duration|contract type|employee class|employment term", re.I)

# An employment word used as a DOMAIN, a market or a training grade rather than as a
# term of employment. `Contract Management Lead` is a permanent role ABOUT contracts;
# `B2B` is a market; `Manager Trainee` is a permanent trades job. Measured: without
# this, 54 of 163 title vetoes (33.1%) were rows like these, each a correct fill
# discarded. Every entry here is a NON-TECH role shape, which is why none of them
# appear in the 94-board tech corpus this rule was designed against.
_TITLE_DOMAIN = re.compile(
    r"contract(?:s|or)?\s+(?:management|administration|manager|administrator"
    r"|specialist|analyst|negotiat\w+|attorney|counsel|officer|support|lead)"
    r"|contractor\s+special|\bb2b\b|\bc2c\b|trainee",
    re.I,
)


def _employment_from_extra(p: dict) -> None:
    """Fill a missing `employment_type` from any vendor metadata value in source_extra.

    WHY THIS IS KEY-AGNOSTIC, which is the whole design. Greenhouse metadata keys are
    EMPLOYER-authored free text, so an alias list of key names cannot be written once
    and stay right: measured across the live store, **61 distinct keys** carry a value
    that resolves, including `Employment Classification (UKG)`, `TH: Employment Type`
    and `Full-Time/Part-Time Status`. No hand-written list reaches those. Meanwhile
    two of the four most obvious key names (`Job Type`, `Worker Type`) resolve to
    nothing at all -- their values are `Standard` and `Employee`. The KEY never
    carried the meaning; the VALUE does, so the value is what gets tested, against a
    vocabulary this module already owns.

    Measured effect: 1,569 of 4,852 greenhouse rows gain a type, 0 rows on any other
    source (they either already state one or ship no metadata bag). FILL-ONLY -- the
    caller checks, and a vendor's own field is always better evidence than its
    metadata bag. `Regular` (151 rows) and `Standard` (124) resolve to nothing and
    are correctly left alone; only a real map hit is accepted, never the OTHER
    fallback, because OTHER here would mean "some string existed", not "a type was
    stated".

    TWO DISAGREEING FIELDS ARE NOT A TYPE. 11 rows carry two different resolvable
    values at once (`Employment Type=Contractor` with `Time Type=Part Time`;
    `Time Type=Full Time` with `Worker Sub-Type=Temporary`) -- two forms filled
    inconsistently, so nobody stated one coherent fact and the answer is `None`. This
    is the same rule the hn parser needs for a posting whose location text states two
    arrangements, and it is deliberately ONE rule: a single field stating a span is a
    range, but two fields disagreeing is an absence. Both raws are kept so the
    disagreement is auditable rather than erased.
    """
    extra = p.get("source_extra")
    if not isinstance(extra, dict):
        return
    found: dict[str, str] = {}
    for key, value in extra.items():
        if isinstance(value, (list, tuple)):
            value = value[0] if len(value) == 1 else None
        if not isinstance(value, str) or not value.strip():
            continue
        norm, raw = vocab.employment_type(value)
        if norm is None or norm == "OTHER":
            continue
        if norm == "FULL_TIME" and _TERM_KEY.search(str(key)):
            continue
        found[raw or value] = norm
    if not found:
        return
    types = set(found.values())
    # THE TITLE IS A VETO, NEVER A COMPETING ANSWER, and the asymmetry is the whole
    # point. Hand-read of 150 sampled fills found the metadata asserting a type the
    # posting contradicts: `Store Lead - Part Time` carried `Full Time` metadata,
    # `Clinical Lab Scientist (Contract)` carried `Full-time`. 157 rows, 1.88% of the
    # live fill, and they land in shortlist.csv where a CLI user reads them.
    #
    # Reading the title as a RIVAL answer was tried and measured, and it fails the way
    # everything in this field fails -- on vocabulary that is fine in a vendor's
    # employment field and wrong in a title. Three distinct false-positive classes
    # surfaced in three attempts, every one of them a NON-TECH role invisible on the
    # 94 tech boards this corpus is built from:
    #   `b2b`     -> "Senior Associate, B2B Performance Marketing"  (a market, not a term)
    #   `trainee` -> "Manager Trainee", "Door Technician Trainee"   (permanent trades roles)
    #   `contract`-> "Contract Management Lead"                     (a domain noun)
    # A fourth attempt would likely find a fourth. So the title is not asked what the
    # type IS -- only whether it CONTRADICTS what the metadata claims, and a
    # contradiction withdraws the claim instead of replacing it.
    #
    # That makes the failure direction safe: a false positive here costs one unfilled
    # row (`None`, which honestly means "not established"), where a rival answer would
    # cost a wrong assertion. Filling 8,197 of 8,354 correctly beats filling 8,354 with
    # 157 known-wrong, and the 157 are exactly the rows a consumer would have believed.
    # TWO GUARDS, both measured on 8,526 live fills, both required.
    #
    # (1) DOMAIN USAGE. `Contract Management Lead`, `Contractor Special Security
    #     Officer`, `B2B Performance Marketing`, `Manager Trainee` -- the word names
    #     what the job is ABOUT, or a market, or a training grade. Without this guard
    #     **54 of 163 vetoes (33.1%) were these**, each one a correct fill thrown away.
    # (2) OVERLAP, NOT DIFFERENCE. `Customer Operations Intern - Part-time` speaks
    #     INTERN *and* PART_TIME while the metadata says INTERN. That is one axis
    #     corroborated and a second one added -- not a contradiction. The veto fires
    #     only when the title and the metadata share NO type at all.
    title = p.get("title")
    if isinstance(title, str) and title and not _TITLE_DOMAIN.search(title):
        spoken = {
            vocab.employment_type(m.group(1))[0]
            for m in vocab.TITLE_EMPLOYMENT_RE.finditer(title)
        } - {None}
        if spoken and types.isdisjoint(spoken):
            return
    p["employment_type"] = types.pop() if len(types) == 1 else None
    # Sorted so a conflicting pair records identically on every run -- a raw that
    # reorders between harvests would look like a changed value to a diffing consumer.
    p["employment_type_raw"] = " | ".join(sorted(found))


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
    #
    # The stated value is CASE-FOLDED here, once, for the same reason employment_type
    # is normalized here: one column was holding five vendor dialects at once, and a
    # facet a consumer cannot filter on is not a facet. Measured on a 7,545-row
    # harvest, `seniority='senior'` returned 2,229 rows and silently missed 319 more
    # spelled `Senior`, `Senior Level` or `Mid-Senior Level` -- 179 of those for
    # letter case alone. Case-folding is mechanical and fixes exactly those 179.
    #
    # IT DOES NOT MAP RUNGS, and vocab.seniority explains at length why not: there is
    # no published seniority enumeration to normalize toward, so an ordering would be
    # this library's opinion, and `catalog/_SCHEMA.md` leaves that to the consumer.
    # `Mid-Senior Level` therefore survives as `mid-senior level`, not as `senior`.
    #
    # `seniority_raw` mirrors `employment_type_raw` and is subject to the same rule:
    # it means "what the VENDOR said", so the title-derived branch below must never
    # set it -- nobody quoted anything there. An adapter that already supplied a raw
    # (themuse sends a canonical token AND a display string) keeps its own.
    if p.get("seniority"):
        p.setdefault("seniority_basis", "stated")
        p["seniority_basis"] = p["seniority_basis"] or "stated"
        folded, raw_sen = vocab.seniority(p["seniority"])
        p["seniority"] = folded
        if p.get("seniority_raw") is None:
            p["seniority_raw"] = raw_sen
        if folded is None:
            # "Not Applicable" / "Any": the vendor answered and declined to classify.
            # There is no level, so there is no basis for one. Deliberately does NOT
            # fall through to the title -- overriding an explicit refusal with our own
            # guess is the opinion this field is being kept clear of.
            p["seniority_basis"] = None
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
    if p.get("employment_type") is None:
        _employment_from_extra(p)

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


_CURRENCY_CODE = re.compile(
    r"\b(USD|CAD|EUR|GBP|AUD|NZD|SGD|HKD|INR|JPY|CHF|SEK|NOK|DKK|PLN|BRL|MXN|ZAR|AED|ILS)\b"
)
_PERIOD_WORD = re.compile(
    r"\b(hour|hourly|hr|week|weekly|month|monthly|year|annual|annually|yearly|day|daily)\b",
    re.I,
)
# 90 characters each side of the figure. Wide enough to catch `Annual Base Salary
# $135,575 - $175,450 USD` (the code trails the range) and narrow enough that the
# NEXT pay band in a multi-city posting does not bleed in -- measured ambiguity at
# this width is 8 rows in 3,500, all of them genuinely two-currency postings.
_HYPHEN_QTY = re.compile(
    r"\d+\s*-\s*(?:hour|hr|week|wk|month|mo|year|yr|day)s?\b", re.I
)
_ADJ_WINDOW = 90


def _adjacent_evidence(text: str, needle: str) -> tuple[str | None, str | None]:
    """The currency and period the employer STATED next to this figure, or None.

    WHY A WINDOW AND NOT THE WHOLE BODY: a posting that quotes a US band and a Canada
    band contains both `USD` and `CAD`, so a document-wide scan picks whichever comes
    first and calls it evidence. Anchored to where the display string actually sits,
    a sole code in the window is the employer labelling THIS figure.

    WHY NOT `country`, which is the obvious move and is measurably wrong: on the 3,500
    target rows, `country='CA'` rows whose body names one code say CAD on 107 and USD
    on 18 -- a Canadian-located job paying in USD is real. A country rule is wrong in
    both directions; the code beside the number is wrong in neither. And 823 of the
    3,500 have no country at all, where this still reads the answer off the page.
    [local 94-board harvest, 0.9.0, 2026-08-20]

    TWO of anything is a refusal, not a coin flip. Returns (currency, period), each
    None unless exactly one candidate appears in the window.
    """
    if not text or not needle:
        return None, None
    i = text.find(needle)
    if i < 0:
        return None, None
    w = text[max(0, i - _ADJ_WINDOW) : i + len(needle) + _ADJ_WINDOW]
    codes = set(_CURRENCY_CODE.findall(w.upper()))
    currency = codes.pop() if len(codes) == 1 else None
    # "90-day waiting period" / "4-day work week" / "12-month vesting" are not
    # pay periods. Dropping a digit-hyphen prefix removes the actual source of
    # the 55 bogus `day` assignments rather than only catching them downstream.
    periods = {
        vocab.salary_period(x) for x in _PERIOD_WORD.findall(_HYPHEN_QTY.sub(" ", w))
    }
    periods.discard(None)
    return currency, (periods.pop() if len(periods) == 1 else None)


def derive_salary(p: dict) -> dict:
    """Fill the structured salary from the display string the adapter already has.

    WHY THIS EXISTS: eleven of twelve adapters call `salary_from_text`, which returns
    a DISPLAY STRING and nothing else, and the only text->number parser in the
    codebase (`vocab.google_salary`) is wired to one adapter. Measured on
    `_reports/flat.ndjson`: `salary_basis` was `stated` on 981 rows, `parsed` on 12,
    and null on 6,567 -- while 3,500 rows carried a fully formed pay range in
    `salary` with every structured column null. 74.3% of every row that had a salary
    at all. `README.md:41` promised basis="parsed" meant "read out of free text";
    that promise was kept on twelve rows.

    FILL-ONLY, NEVER OVERWRITE. A vendor's own structured object is better evidence
    than our parse of its prose, and `salary_basis='stated'` must keep meaning what
    it says. Verified before building: 0 rows carry a `salary_max`, `salary_currency`,
    `salary_period` or `salary_basis` while `salary_min` is null, so the guard below
    has no partial-fill case to straddle in this corpus -- but it is written as
    fill-only anyway, because that is a property of the contract and not of one run.

    SEPARATE FROM `_coerce`, and called AFTER the relevance gate, for the reason
    `derive_remote` gives: this reads the full body to find the figure's neighbourhood,
    and 69% of postings are discarded by a title-only test microseconds later. Parsing
    the ~20-character display string alone would belong in `_coerce`; reading the body
    around it does not.
    """
    if p.get("salary_min") is not None or p.get("salary_max") is not None:
        return p
    # AN ESTIMATE IS NOT A COMMITMENT, and this guard is the whole reason
    # `_adzuna_pay` splits the columns in the first place. A predicted row has NULL
    # commitment columns by design, so the fill-only test above waves it straight
    # through -- and the display string is right there to parse. Caught by measuring,
    # not by review: this function wrote 109106.0 into `salary_min` with
    # basis="parsed" on a row whose 109106.69 was a model output. One forgotten
    # WHERE clause downstream and every average built on the corpus is poisoned,
    # which is the failure `_adzuna_pay`'s own comment records.
    if (
        p.get("salary_estimated_min") is not None
        or p.get("salary_estimated_max") is not None
    ):
        return p
    display = p.get("salary")
    if not display:
        return p
    currency, period = _adjacent_evidence(p.get("text") or "", display)
    got = vocab.salary_from_display(display, period=period, currency=currency)
    if got.get("salary_min") is None:
        return p  # the parser refused; leave the honest nulls alone
    for k, v in got.items():
        if p.get(k) is None:
            p[k] = v
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
        derive_salary(p)  # same reason, same body scan -- see its docstring
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
