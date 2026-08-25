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

from . import config, extract, vocab
from .dedup import entry_key, find_hit_key, norm
from .funnel import funnel
from .scoring import is_remote, relevant, remote_signal, score_and_signals
from .sources import (
    DEPTH_ACCEPTS_KEEP,
    DIRECT_APPLY_SOURCES,
    DEPTH_ALL,
    DEPTH_EXTRA_FIELDS,
    PREDICTED_PAY_SOURCES,
    _is_direct_apply,
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
# record: the real apply URL, the full description, the employer's own department. Google
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
_REQUIRED_TEXT = ("title", "company", "url", "source")


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
    # `department` is the ONE org/function column. It holds whatever the vendor
    # publishes for "what part of the company / what kind of work": the catalog's
    # `org_unit` where a source ships one (greenhouse `departments[].name`, ashby /
    # workable / smartrecruiters / rippling `department`, lever `categories.department`)
    # and its `function` where it ships that instead (adzuna `IT Jobs`, the muse, jobicy,
    # remotive, braintrust, himalayas, and USAJOBS' OPM series).
    #
    # It was TWO columns until 0.9.0 -- `team` and `category` -- and they measured as
    # complementary, never redundant: on 102,553 rows `[2026-08-24]` `team` filled
    # 100,463 and `category` 1,759, with **0 rows carrying both** and 331 carrying
    # neither. Three ATSs filled one, five aggregators filled the other, so each column
    # read ~98% empty on its own and neither was usable as a filter. Merging them is
    # exactly what `catalog/_SCHEMA.md` warns against for a PROFILE -- it keeps
    # `function` / `org_unit` / `employer_org` separate because five adapters once
    # poured all three into one field and produced 5,050 distinct values including
    # employer names. The zero overlap is what makes it safe HERE and nowhere else:
    # no row loses a value, because no row ever had two.
    #
    # THE EMPLOYER IS STILL NOT ALLOWED IN. That was the third meaning in the old
    # `department`, and USAJOBS still publishes `DepartmentName` = "Department of
    # Veterans Affairs". It reads `SubAgency` here; a test pins the string out.
    #
    # Values are the source's own words, NOT normalized and NOT an O*NET-SOC code.
    # Deriving a job family from an org unit was tried downstream and reverted: it
    # looked like +17.7% coverage and its single largest effect was filing 895 rows of
    # "Senior Software Engineer, Backend" under Science and Engineering, because that
    # employer's org unit is called "Engineering". Normalizing onto a taxonomy is a
    # consumer's judgment (`catalog/_SCHEMA.md`: "Fidelity, not opinion"), and this
    # library does not make it.
    "tags",  # list[str] | None -- skills the source itself extracted
    # who
    "department",  # the ONE org/function column -- see the block above.
    # NOT the employing organisation: USAJOBS publishes `DepartmentName` = "Department
    # of Health", which is an EMPLOYER, and the shared word is the bait _SCHEMA.md
    # warns about. That adapter reads `SubAgency` here; a test pins it.
    # where
    # list[dict] | None -- every place ONE posting names, each {raw, city, state,
    # country}. NO per-place `url`: entries carried one until 0.9.0, and it was the
    # posting's own url on all 9,585 of them in a 7,568-row harvest, 0 differing.
    # The key was not merely unpaid but a false claim -- it advertised a per-place
    # apply link that not one of the nineteen sources publishes, so a consumer could
    # reasonably have built a per-office apply flow on a value that never varied.
    # The one construction path that COULD differ made it worse, not better:
    # `fetch_workable` built the record url from three fallback terms and the entry
    # url from two, so with both vendor keys absent the entry was `None` while the
    # record had a working constructed link.
    "locations",
    "city",
    "state",
    "country",
    # THE ONE FIELD FOR WHETHER THE ROLE IS REMOTE. A `remote` bool sat beside this
    # until 0.9.0 and was exactly `None if remote_type is None else remote_type ==
    # "remote"` -- 7,568 of 7,568 rows in a local harvest, no exceptions. Two homes for
    # one fact, and the bool was the weaker home: it cannot express `hybrid`, so 1,679
    # hybrid rows carried `remote: false`, which is true and reads as on-site to
    # anything that only checks the flag. Nothing in the package ever read it --
    # `scoring.is_remote` reads THIS field and derives its own flag, and says so.
    #
    # Derive it, tri-state, and keep the tri-state: collapsing `None` to `False`
    # asserts "not remote" on every row nobody classified (3,399 of 7,568 here).
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
    "salary_basis",
    # WHAT the figure measures, vs `salary_basis`'s HOW it was extracted. `None` means
    # there is no figure at all; `unspecified` means there IS one and no label sat near
    # it. Those are different facts and the contract keeps them apart.
    "salary_kind",  # base | ote | equity | unspecified | None -- vocab.SALARY_KINDS
    # terms
    "employment_type_raw",  # what the vendor actually said
    "seniority",
    "seniority_raw",  # what the vendor actually said, before case-folding
    "seniority_basis",  # stated | title | None
    # what KIND of body `text` is, when it is not an ordinary one -- vocab.TEXT_BASES.
    # `excerpt` = the SOURCE truncates it (Adzuna caps at 500 chars + an ellipsis, 275
    # of 275 rows). `synthesized` = there was no prose body and the adapter built one
    # from structured fields (Braintrust, 29 of 29, ~157 chars). `None` = not
    # characterized, which is the honest value for the other seventeen: there is no
    # `full`, because nobody has verified completeness for any of them and asserting it
    # would be exactly the plausible-looking guess `_coerce` exists to refuse.
    #
    # `text is None` already says "the source sent no body" (smartrecruiters, 250 of
    # 250), so that state needs no vocabulary entry. Without this field an excerpt
    # averaging 500 characters and a real body averaging 6,870 are indistinguishable
    # in the record, and 10.6% of production rows are excerpts.
    "text_basis",  # excerpt | synthesized | None -- vocab.TEXT_BASES
    # when
    "posted_basis",  # stated | relative | None
    "expires",
    "harvested_at",
    # provenance
    "direct_apply",  # bool -- the url reaches the EMPLOYER, not an aggregator
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
    # WHETHER A PERSON CAN APPLY AT ALL -- read from the posting's prose by
    # `extract.enrich`, never from a vendor field, because no source publishes either.
    # Both are THREE-state and in both cases two states was a SIGN FLIP: `conditional`
    # holds the hedged employer templates that would otherwise be forced into `offered`,
    # and `obtainable` separates "must be able to obtain" from "must already hold".
    # Ensured-present-and-`None` here and deliberately NOT coerced, like every other
    # contract field -- `None` means the posting did not say, on 94% of rows.
    "sponsorship",         # offered | conditional | not_offered | None
    "sponsorship_basis",   # vocab.SPONSORSHIP_BASES | None
    "clearance",           # required | obtainable | mentioned | None
    "clearance_basis",     # vocab.CLEARANCE_BASES | None
)

# Fields whose ABSENCE is meaningful and must survive as None rather than "".
# `posted: ""` used to mean "we could not parse a date", which is the same lie as
# `remote: False` for unknown -- a consumer cannot tell it from a job with no date.
# These move out of the str-coerced tier. The four genuinely required fields
# (title, url, company, source) stay strings: _consume drops any row missing a url,
# and a posting with no title is not a posting.
# `text` deliberately keeps its name. `body` reads better, but it is a released,
# README-documented field with call sites in the only consumer, and renaming it buys
# a nicer word and nothing else. If it ever moves, it moves at 1.0; `department`,
# which used to be the other half of that sentence, went at 0.9.0 instead.
_NULLABLE_TEXT = (
    "posted", "salary", "text", "location", "employment_type",
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
# Types that are TWO NAMES FOR ONE ARRANGEMENT, not a disagreement. An employer's
# form offers Full-time/Part-time and has no per-diem option, so a nursing manager
# picks Part-time and writes `Per Diem` in the title; `(Fixed-Term Contract)` and
# TEMPORARY are likewise one arrangement under two labels. That is a VOCABULARY
# GRANULARITY MISMATCH between a form and a title -- the title is more specific than
# the form allowed -- and vetoing on it discarded 40 rows the metadata had right.
#
# Worth noting what this class is made of: it is 100% NON-TECH -- nursing and
# creative contract work -- and measures zero on the 94 tech boards this rule was
# designed against. That is the third such class in this lens, after the
# title-contradiction defect itself and the b2b/trainee/contract false positives.
_COMPATIBLE_TYPES = (
    frozenset({"PER_DIEM", "PART_TIME"}),
    frozenset({"CONTRACTOR", "TEMPORARY"}),
)

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
            both = spoken | types
            if not any(both <= group for group in _COMPATIBLE_TYPES):
                return
    p["employment_type"] = types.pop() if len(types) == 1 else None
    # Sorted so a conflicting pair records identically on every run -- a raw that
    # reorders between harvests would look like a changed value to a diffing consumer.
    p["employment_type_raw"] = " | ".join(sorted(found))


_SEP = re.compile(rf"\s*{vocab.LOCATION_SEPARATORS}\s*")


_BARE_NUMBER = re.compile(r"^[-+]?\d+(?:\.\d+)?$")


def _usable_part(part: str) -> bool:
    """True when a split PART can be established as a place.

    THE SEPARATOR DOES NOT MEAN THE SAME THING ON EVERY BOARD, which is why splitting
    is not enough on its own. Greenhouse uses `|` for separate offices ("San Francisco,
    CA | New York City, NY"); Ashby uses it as a HIERARCHY, coarsest-to-finest ("US |
    Illinois | Chicago" -- one office, 17 rows), and 139 rows in the consumer's store
    lead with a country for that reason. Some Greenhouse boards append COORDINATES
    ("Pennsauken, NJ 08109 | 39.950866919 | -75.048664024").

    Measured on that 67,481-row store: a blanket `[;|\u2022]` split emits 4,789
    `locations[]` entries of which 2,562, across 1,332 rows, are not a place under any
    reading -- a latitude receiving a `city`, a `state`, a `country` and an apply url,
    and becoming a place a job is offered in. That is manufacture, not truncation, in
    the field a consumer trusts most.

    So: split, then DROP what cannot be established, and fall back to the whole string
    when nothing survives (146 rows). Filtered, the same corpus emits 2,008 entries and
    2,781 junk ones are never created. The test is STRUCTURAL -- a bare number, an
    unbalanced parenthesis (splitting "Remote (United States | Canada)" leaves both
    halves broken, 31 rows), or nothing left after the arrangement words come out --
    with no place list, which would be an artifact of this US-heavy tech corpus.

    Same discipline as `util.clean_with_sections`, which emits NO span for a section it
    cannot locate rather than a guessed one: the failed lookup IS the self-check.
    """
    p = part.strip()
    if not p or _BARE_NUMBER.match(p):
        return False
    if p.count("(") != p.count(")"):
        return False
    if not vocab.strip_arrangement(p):
        return False
    return any(_read_place(p).values()) or "," in p


def _location_parts(raw: str) -> list[str]:
    """A vendor location string -> the places it names, junk parts dropped."""
    parts = [x.strip() for x in _SEP.split(raw or "") if x.strip()]
    if len(parts) < 2:
        return parts
    keep = [p for p in parts if _usable_part(p)]
    return keep or [raw.strip()]


def _read_place(part: str) -> dict:
    """One location PART -> {city, state, country}, arrangement words removed first.

    Lives here so the scalar fallback and the `locations[]` entries below read the
    same string through the same pipeline. They did not: the nested branch called
    `split_place` raw while the scalar stripped first, so one row carried
    city="Illinois" and locations[0].city="Remote - Illinois" -- the same field,
    two answers, 125 measured entries.
    """
    bare = vocab.strip_arrangement(part)
    place = vocab.split_place(bare)
    if not any(place.values()):
        place = vocab.split_place(part)
    return place


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
    # EDGE WHITESPACE COMES OFF THE TWO DISPLAYED FREE-TEXT FIELDS, and the asymmetry
    # it closes is the reason it is here rather than downstream: `shortlist._build_row`
    # has been stripping exactly `title` and `location` for releases, so the CSV has
    # been clean the whole time while the record and the NDJSON shipped the raw value.
    # A library consumer got the dirt; the CLI user never saw it. 9,874 titles (9.6%)
    # and 1,894 locations carry it [102,799-row harvest, 2026-08-20], and it is not all
    # ordinary spaces: 9,791 space, but 57 NBSP, 21 U+202F, 14 tab and 2 newline, none
    # of which a person eyeballing the value would see.
    #
    # `dedup_key` DOES NOT MOVE, which is what made this safe to do at all.
    # `normalize_title` and `normalize_location` both run
    # `re.sub(r"[^a-z0-9]+", " ", ...).strip()`, which already absorbs all five classes
    # -- verified over all 9,874 rows, 0 keys change. So no user-facing job id shifts.
    #
    # BEFORE the two loops below, not after, so a whitespace-only `location` reaches
    # the `_NULLABLE_TEXT` pass as "" and becomes None rather than a string that looks
    # present and holds nothing. (0 such rows in that corpus; the ordering is free.)
    #
    # `company` IS here as of 0.9.0, and the reason it was excluded is now FALSE.
    # That exclusion read: "on a DEPTH adapter it comes from the watchlist rather
    # than the vendor, so stripping it at this boundary would be fixing the wrong
    # layer." True when written; `harvest` now fills `company` from Greenhouse's
    # own `company_name` wherever the watchlist never held a real name, so the
    # vendor's dirt reaches this boundary and this IS the right layer.
    #
    # It is not rare. Measured live 2026-08-24 by two agents across three samples
    # and two different Greenhouse endpoints -- 2 of 12, 2 of 40, 1 of 40 boards
    # ship edge whitespace in `company_name`: ' Higher Logic', 'Home Chef  ',
    # ' ALO', 'Brandtech+ ', 'Horace Mann '. Roughly one board in ten.
    #
    # ACCEPTED SCOPE, so nobody re-derives it: `dedup_key` DOES NOT MOVE. `norm`
    # and `company_block` both collapse non-alphanumerics, so ' ALO' and 'ALO'
    # already key identically -- this reaches only the CSV, `emit` and the display.
    # `url` and `source` still carry edge whitespace on 0 rows.
    for k in ("title", "location", "company"):
        v = p.get(k)
        if isinstance(v, str):
            p[k] = v.strip()

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
    place = _read_place(next(iter(_location_parts(p.get("location") or "")), ""))
    # PER-FIELD, not all-or-nothing. This gate used to require all three to be None,
    # which is not what the paragraph above claims it does: lever sets `country` from
    # a real vendor field on 135 of 135 rows, so the gate never fired and its own
    # "New York, NY" was never read -- 0 of 135 lever rows carry a city. Measured
    # gain on a 7,545-row harvest: +1,966 state, +430 city, +333 country, 0 values
    # lost, verified again on 360 live lever postings (66 gain a city, 0 rejected).
    #
    # GATED ON COUNTRY AGREEMENT, and the gate is required rather than defensive:
    # smartrecruiters sends "bengaluru, in" with country IN, and split_place reads
    # the "in" tail as INDIANA. Ungated, that writes a US state onto 60 Indian rows.
    # A parse that contradicts the vendor's own country field is rejected whole.
    if (
        place["country"] is None
        or p.get("country") is None
        or place["country"] == p["country"]
    ):
        for k, v in place.items():
            if v is not None and p.get(k) is None:
                p[k] = v

    # `locations` -- every place ONE posting names. Greenhouse separates them with
    # `;` ("Berlin, Germany; Munich, Germany") and 143 of 810 postings on one measured
    # board do this. That is a single job id, so it stays a single row; the places
    # ride here instead of forcing a split. An adapter that already built a
    # structured list (usajobs PositionLocation[], rippling workLocations[]) keeps it.
    if p.get("locations") is None:
        parts = _location_parts(p.get("location") or "")
        if len(parts) > 1:
            # SAME KEYS as the single-place branch below. This used to emit only
            # {raw, url}, so 644 of 3,153 live elements had no city/state/country
            # key at all and a consumer doing `l["city"]` raised on a fifth of the
            # list. The parsed values are per-place, so each is read from its own
            # string rather than copied from the row's first place. `url` left the
            # entry in 0.9.0 -- see the `locations` comment above.
            p["locations"] = [{"raw": x, **_read_place(x)} for x in parts]
        elif parts:
            p["locations"] = [
                {
                    "raw": parts[0],
                    "city": p.get("city"),
                    "state": p.get("state"),
                    "country": p.get("country"),
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
    # The URL is now consulted too, and the OR is the whole design. Deciding per SOURCE
    # alone labelled the SAME HOST differently depending on which adapter found it:
    # `jobs.ashbyhq.com` is direct on 1,202 rows and not-direct on 10, the only
    # difference being that hn carried the second set. 92 rows across the corpus sit on
    # an ATS or the employer's own domain while reported not-direct -- every one of them
    # hn, because it is the only source carrying a link the poster typed.
    #
    # MONOTONE, NEVER REPLACING, and this is not a stylistic preference. Swapping the
    # source rule for the URL rule DEMOTES 2,638 rows in a 67,481-row production store
    # (eyecare-partners 286, esri 204, zipline 201, okta 80, buckner 34) against 85
    # gained -- 31:1, and they do not drop cleanly, they rot: the consumer's intake
    # rejects them on every harvest while the stale rows stay served. `_is_direct_apply`
    # is positive-evidence-only and blind to a 4-character company name, so it is a
    # rescue, not an oracle. jobfitr reached the same conclusion independently and wrote
    # it down at its `store.py` -- "WHY _is_direct_apply IS NOT THE ORACLE".
    if p.get("direct_apply") is None and p.get("source"):
        by_source = p["source"] in DEPTH_ALL or p["source"] in DIRECT_APPLY_SOURCES
        # A link the ADAPTER recovered is excluded: it must earn the flag rather than
        # inherit it from host shape. Of 5 such rows measured, 4 are 404/410.
        by_url = not p.get("_url_recovered") and _is_direct_apply(
            str(p.get("url") or ""), str(p.get("company") or "")
        )
        p["direct_apply"] = by_source or by_url

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


# The cues for `salary_kind`, most specific first. Order does NOT decide the answer --
# proximity does, below -- but a narrower pattern must not be shadowed by a broader one
# matching the same span, so `total compensation` is listed before the bare cues.
_KIND_CUES: tuple[tuple[str, re.Pattern[str]], ...] = (
    # SCOPE-RESTRICTED TO THE FIGURE'S OWN CLAUSE -- see `_same_clause`. Unrestricted,
    # this cue fires on 81 rows and is right on 15 of them: an equity mention on the
    # NEXT line of a benefits list ("...base salary range is $132,000-$178,000, plus
    # RSUs" / "Salary: $120,000-$175,000 / Valuable stock option plan") reads as the
    # figure BEING equity. Restricted, it fires on 26 and keeps all 15.
    ("equity", re.compile(
        r"\b(new[- ]hire equity|equity (grant|award|refresh)|rsus?|restricted stock"
        r"|stock (option|grant|award))\b", re.I)),
    # `<something> + commission / variable / incentive` IS on-target earnings, by
    # definition rather than by measurement: a range described as base plus commission
    # is what the term means. It is here because boards write OTE that way and the
    # nearest token INSIDE the parenthetical is `base` or `commission`, so without this
    # the row reads `base` or `bonus`. Sixteen of the ~24 residual errors on a
    # hand-labelled sample were this one form: `(TTC / OTE)`, `(base + commission)`,
    # `(Base Salary + Variable)`, `(Base + On-Target Commission)`. Parentheses are
    # OTE's primary carrier, which is also why only NEGATED parentheticals are blanked.
    #
    # ONE KNOWN ACCEPTED MISLABEL, named so it is a decision rather than a bug waiting
    # to be rediscovered. This branch decides 31 rows (27 -> `ote`, 4 -> `total_comp`)
    # and 30 are unambiguous. The exception offers BOTH readings in one line -- "Base
    # salary range [or Total On-Target Compensation]: $235,000 - $265,000" -- and this
    # picks `ote`. Left alone: a rule to split it would be a fourth rule bought for one
    # row.
    ("ote", re.compile(
        r"\b(ote|on[- ]target earnings|on[- ]target compensation|total target cash"
        r"|\w+\s*\+\s*(on[- ]target\s+)?(commission|variable|incentive))", re.I)),
    # Hourly cues resolve to `base` on purpose -- see vocab.SALARY_KINDS. The interval
    # is `salary_period`, which already carries it.
    #
    # A BARE QUANTITY WORD IS A LABEL. `Salary Range`, `Pay Range`, `Compensation Range`
    # name base pay as distinct from bonus, equity and OTE, so they are `base` -- not
    # `unspecified`. This was measured before it was decided: of 30,283 rows with a
    # locatable display string, 11,657 (38.5%) carry a QUALIFIED cue (`base salary`,
    # `annual base`) and 14,638 (48.3%) carry ONLY a bare one. The bare population is
    # the larger. With only qualified cues the detector refused 77 of 150 hand-labelled
    # rows that the rubric calls `base` -- not a precision failure (4 wrong assertions
    # in 150) but a recall collapse, because the rule and the rubric were measuring
    # different things.
    #
    # `base compensation` and `base hourly` are here because their ABSENCE caused false
    # positives rather than misses: `"...bonus, equity, and a generous benefits program.
    # Base Compensation Range $65,000"` returned `equity` without them.
    ("base", re.compile(
        r"\b(base (salary|pay|compensation|rate|hourly)|annual base"
        r"|hourly (rate|pay|wage)"
        r"|(salary|pay|compensation)\s+range"
        r"|(annual|estimated|national|expected|target)\s+(salary|pay|compensation))\b",
        re.I)),
)

# THE LABEL IS THE PHRASE INTRODUCING THE FIGURE, not the last cue word before it, and
# a sentence boundary is where that stops being true. Everything before the last full
# stop is prose about benefits, and a cue in it is a BENEFIT BEING DESCRIBED rather than
# a label. This is structural -- it is how job posts are written -- not a heuristic
# tuned to a handful of rows.
#
# THIS RULE WAS REMOVED AND RESTORED, and the reason it came back is the only part worth
# reading. It was dropped on an argument three of us made independently and all of us
# got wrong: once the cue table accepts a bare `Salary Range` as a `base` label, a cue
# sits on the NEAR side of the break, wins on proximity, and the truncation is redundant.
# Corpus-wide that looked right -- the widening absorbed most of what the rule did -- and
# on 150 blind-labelled rows the two configurations produced identical wrong assertions.
#
# THE ROWS THE RULE ACTUALLY SUPPRESSES HAVE NO NEAR-SIDE CUE AT ALL. That is what
# nobody checked. They carry a bare location, or nothing:
#
#   "...is just one component of <co>'s total compensation package. New York City:
#    $155,000-$185,000"
#   "Additional compensation such as Bonus, Commission, Equity and other benefits may
#    also apply. $140,000-$220,000"
#   "...eligible to be considered for an annual bonus. The range for Chicago metro area
#    is $111,000 - $131,000"
#
# Proximity cannot rescue a row with nothing to be proximate to. Labelled exhaustively
# and blind, 81 of 86 suppressed rows (94.2%) are genuinely `base` -- so without this
# rule those 81 carry a wrong `bonus` or `total_comp` label on a base salary, which is
# the precise defect this field exists to prevent. The rule creates exactly one wrong
# assertion of its own.
#
# THE COST IS REAL AND IS NOT HIDDEN: ~570 rows return `unspecified` instead of `base`,
# and on a hand-labelled sample about half of the prior-sentence rows carried a genuine
# governing label. Under this field's precision-over-recall ruling a refusal costs
# nothing and a wrong assertion does, so the trade is taken deliberately.
#
# AND THE DEFENCE IS NARROWER THAN THE COUNT SUGGESTS: those 86 rows come from 13
# employers, 48 of them one employer's repeated boilerplate and 19 more a second's. This
# guards a small number of SHAPES, not 86 independent decisions. Disclosed because a
# comment claiming ~80 independent saves would be overstating it.
_SENTENCE_END = ".!?"


def _last_sentence(window: str, before: int) -> str:
    """Blank everything up to the last sentence end preceding the figure."""
    cut = max(window.rfind(c, 0, before) for c in _SENTENCE_END)
    return " " * (cut + 1) + window[cut + 1 :] if cut >= 0 else window


def _snap(text: str, lo: int, hi: int) -> tuple[int, int]:
    """Widen a window to word boundaries.

    A FIXED-OFFSET SLICE CAN CUT A WORD IN HALF AND MANUFACTURE A `\b` THAT WAS NOT
    THERE. `remote` sliced mid-word becomes `ote`, and `OTE` is a three-letter
    case-insensitive token -- the worst possible shape for this. 6 of 813 OTE matches
    were that; the wider exposure is 3,073 rows whose window contains `ote` only as a
    substring (`quote`, `note`, `promote`, `denote`) against 763 carrying it as a word.
    Every cue already requires `\b`; this makes the WINDOW agree with them.
    """
    while lo > 0 and (text[lo - 1].isalnum() or text[lo - 1] == "_"):
        lo -= 1
    while hi < len(text) and (text[hi].isalnum() or text[hi] == "_"):
        hi += 1
    return lo, hi

# A NEGATED CUE IS THE ONE NEAREST THE FIGURE, ALWAYS, because that is what the sentence
# shape does: "Annual base salary range (excluding equity and bonus): $218,025-$256,500".
# Reading the closest cue there returns `bonus` on a row whose own words say base --
# actively wrong, and worse than refusing. 3,087 rows put a parenthesis or an exclusion
# between the label and the number.
_NEG_CUE = re.compile(r"exclud\w*|not includ\w*|in addition to", re.I)
_PAREN = re.compile(r"\([^)]*\)")
_EXCL_RUN = re.compile(r"(exclud\w*|not includ\w*)[^.:;]*", re.I)


def _blank(m: re.Match) -> str:
    """Replace a span with spaces, preserving every offset in the window."""
    return " " * len(m.group(0))


def _neutralize(window: str) -> str:
    """Blank the spans that NAME a quantity in order to EXCLUDE it.

    ONLY A NEGATED PARENTHETICAL IS BLANKED, and that distinction is worth 192 rows.
    Blanking every parenthetical also destroys the ones that CONTAIN the label --
    `Pay Range (Base Pay): $230,000-$275,000` reads `unspecified` under the blunt
    version and `base` under this one. Measured across the corpus, narrowing it this way
    moves 473 rows: 192 recover as `base`, 75 as `ote`, 69 as `bonus`.
    """
    window = _PAREN.sub(
        lambda m: _blank(m) if _NEG_CUE.search(m.group(0)) else m.group(0), window
    )
    return _EXCL_RUN.sub(_blank, window)


# A CLAUSE BREAK IS `.!?;` OR A NEWLINE -- AND DELIBERATELY NOT A COLON. A colon
# INTRODUCES the figure it labels; it is the punctuation that binds a label to its
# value, which is the exact relationship this restriction exists to detect. Measured
# on the 81 rows `equity` fires on:
#
#     .!?; + newline   keeps 26   all 15 true positives survive
#     .!?;: + newline  keeps 11   ZERO true positives -- every one is
#                                 "New hire equity: $32,000-$48,000", colon included
#     .!?  (sentence)  keeps 61   keeps the 15 and ~46 false positives with them
#
# Treating `:` as a break would have deleted the motivating case for this whole field.
#
# AND THIS IS A MECHANICAL APPROXIMATION OF A HUMAN JUDGEMENT, not a reproduction of
# one. A blind labelling put 15 of the 81 in the same CLAUSE as the figure and scored
# those 15 at 100%; no punctuation rule reproduces that split -- the four tried give
# 11, 26, 26 and 61. The restriction keeps 26 to keep the 15, so its precision is
# roughly 58-65%, NOT the 100% the labelling measured. Do not quote the labelling's
# rate for this code.
_CLAUSE_BREAK = ".!?;\n"

# AN ADDITIVE CONNECTOR IS ALSO A BOUNDARY, FOR `equity` ONLY, and the asymmetry is
# semantic rather than convenient. A label PRECEDES its value: `"New hire equity:
# $32,000-$48,000"`. A term joined to a figure by `+` or `plus` names a SEPARATE item,
# which is the identical construction that made `bonus` right 0 times in 50 and
# `total_comp` 3 of 22 -- third member, third appearance, one shape.
#
# WHY IT IS SCOPED TO `equity` AND MUST NOT BE APPLIED TO `ote`: the connector's meaning
# depends on what the named quantity IS. On-target earnings ARE base plus commission, so
# `"$231,000-$275,000+ OTE, Base + Commissions"` uses `+` to describe THE FIGURE'S OWN
# PARTS -- a true positive that this rule would destroy. Equity is never part of a
# salary, so `"$180-$220k + attractive RSU package"` uses `+` to name something OUTSIDE
# the figure. Same token, opposite meaning. Verified: `ote` is unchanged at 809 rows.
#
# MEASURED on the shipped classifier, instrumented rather than reimplemented:
#     clause breaks only        29 retained · 15 correct · 14 wrong   (52%)
#     + additive connectors     15 retained · 15 correct ·  0 wrong  (100%)
_ADDITIVE_JOIN = re.compile(r"[+&]|\bplus\b|\band\b", re.I)


def _same_clause(window: str, cue_lo: int, cue_hi: int, fig_lo: int, fig_hi: int) -> bool:
    """Is the cue in the same clause as the figure, and not merely joined to it?"""
    seg = window[min(cue_lo, fig_lo) : max(cue_hi, fig_hi)]
    if any(c in seg for c in _CLAUSE_BREAK):
        return False
    return not _ADDITIVE_JOIN.search(seg)


def salary_kind(text: str, needle: str) -> str:
    """WHAT QUANTITY the figure in `needle` measures -- never how it was extracted.

    Reads the same +/-90 window `_adjacent_evidence` uses for currency and period,
    because the label sits with the number: no new parsing, no second pass over the body.

    THE NEAREST CUE WINS, and both simpler rules were measured and rejected:

      * FIRST MATCH IN A LIST is decided by the list's order, not the posting.
        `Base Salary Range: $170,000-$300,000` came back `bonus`.
      * REFUSING WHENEVER TWO CUES APPEAR -- the rule `_adjacent_evidence` uses for
        currency -- throws away 1,783 rows whose window says `Base Salary Range:`
        immediately before the figure. Two currencies in a window is genuinely
        ambiguous; two cues usually is not, because one of them is being excluded.

    A GENUINE TIE STILL REFUSES: two DIFFERENT cues equidistant from the figure return
    `unspecified`. That is the currency rule kept exactly where it still applies.

    RETURNS `unspecified`, NEVER None, and never `base` from absence. `unspecified` means
    NO QUANTITY WORD IN THE WINDOW -- not a word judged insufficiently specific. See
    vocab.SALARY_KINDS for the rate and for why the honest floor under it is the 28.0%
    of rows whose display string cannot be found in their own body at all.
    """
    if not text or not needle:
        return "unspecified"
    i = text.find(needle)
    if i < 0:
        return "unspecified"  # no window exists; 28.0% of rows with a display string
    lo, hi = _snap(text, max(0, i - _ADJ_WINDOW), i + len(needle) + _ADJ_WINDOW)
    a_lo, a_hi = i - lo, i - lo + len(needle)
    window = _last_sentence(_neutralize(text[lo:hi]), a_lo)
    best: str | None = None
    best_d: int | None = None
    best_n = best_start = best_end = 0
    tied = False
    for kind, cue in _KIND_CUES:
        for m in cue.finditer(window):
            # `equity` alone is clause-scoped. The others are not: `bonus`'s failing
            # shape was SAME-clause (`"$175,000-$210,000 + Bonus."`), so a clause rule
            # would not have saved it -- which is why those two were removed instead.
            if kind == "equity" and not _same_clause(
                window, m.start(), m.end(), a_lo, a_hi
            ):
                continue
            # 0 when the cue overlaps the figure itself, else the gap to the near edge.
            d = (
                0
                if m.start() < a_hi and m.end() > a_lo
                else min(abs(m.start() - a_hi), abs(a_lo - m.end()))
            )
            n = m.end() - m.start()
            if best_d is None or d < best_d:
                best, best_d, best_n, tied = kind, d, n, False
                best_start, best_end = m.start(), m.end()
            elif d == best_d and kind != best:
                # A LONGER MATCH AT THE SAME DISTANCE IS THE SAME TEXT READ MORE
                # PRECISELY, not a competing signal. `(Base + On-Target Commission)`
                # matches `ote` across 27 characters and `bonus` across the 10 of
                # `Commission` inside it, both ending at the figure -- treating that as
                # an ambiguous tie refuses a row whose label is unambiguous. The cue
                # table is already ordered most-specific-first for exactly this reason;
                # this makes the tie-break honour that ordering instead of contradicting
                # it.
                #
                # ONLY WHEN THE SPANS OVERLAP. Two UNRELATED cues equidistant from the
                # figure -- `"bonus $100-$200 base pay"` -- are a genuine ambiguity and
                # still refuse, however long either one is. Length is evidence of
                # precision only when one match contains the other; between separate
                # phrases it is evidence of nothing.
                # CONTAINMENT, NOT MERE OVERLAP, and the difference is only ever
                # theoretical -- which is exactly why the code tests the INTENT. A
                # partial overlap that is not containment is unreachable here: equal
                # `d` forces an equal near edge, and an equal edge with different
                # starts IS containment; cues on opposite sides of the figure cannot
                # overlap at all. Measured: 0 instances in 30,283 rows against 62
                # containment pairs, and an overlap-gated variant differs on zero rows.
                #
                # THE EQUIVALENCE IS LOAD-BEARING ON `d` MEANING "gap to the near
                # edge", which is defined above and could be changed by someone who
                # has never read this. If it changes, an overlap test silently starts
                # preferring longer matches in the case this comment forbids. A future
                # reader tightening the code to match the comment would be making a
                # change they believe is a no-op without knowing it -- so the code
                # tests containment and the comment keeps the reason overlap also works
                # today. `vocab.LOCATION_SEPARATORS` vs `dedup.normalize_location` is
                # the same coupling, kept honest the same way.
                # EITHER DIRECTION. The cue table is walked most-specific-first, so
                # `ote` matches `Base + On-Target Commission` BEFORE `bonus` matches
                # the `Commission` inside it -- the incumbent contains the challenger,
                # not the other way round. A one-directional test refuses exactly the
                # row this branch exists for; that is not hypothetical, it is what the
                # first version of this line did and what the test above caught.
                contains = (m.start() <= best_start and m.end() >= best_end) or (
                    best_start <= m.start() and best_end >= m.end()
                )
                if contains and n > best_n:
                    best, best_n, best_start, best_end, tied = (
                        kind, n, m.start(), m.end(), False
                    )
                elif not contains or n == best_n:
                    tied = True
    return "unspecified" if best is None or tied else best


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
    # AN ESTIMATE IS NOT A COMMITMENT, and this guard is where that is enforced. The
    # function once wrote 109106.0 into `salary_min` with basis="parsed" on a row whose
    # 109106.69 was a model output -- caught by measuring, not by review.
    #
    # THE PROTECTION USED TO BE UPSTREAM AND ONLY UPSTREAM, and five places in this repo
    # said otherwise. `_adzuna_pay` emits `{"salary": ""}` for a predicted row, and the
    # `not display` return below was described -- here, in vocab.py, in sources.py and in
    # two tests -- as "the guard". It was not one. Measured on 1a1414d, before this
    # branch existed: `{"source": "adzuna", "salary": "$129,584–$129,584"}` parsed
    # straight through to `salary_min=129584.0, salary_basis='parsed',
    # salary_kind='base'`. That EN-DASH (U+2013) fake range is the exact shape 6,633 rows
    # carried [live prod, engine 0.8.2] -- so the one string that had actually reached a
    # production store was the one string with no defence in this function at all.
    #
    # WHAT THIS GUARD IS FOR, STATED SO NOBODY RE-DERIVES IT AND CALLS IT FALSE: that
    # string is NOT reachable from any shipped adapter at HEAD. `util.salary_range`
    # renders a point estimate as `$129,584` -- no dash -- so `salary_from_display`
    # refuses it, and `_adzuna_pay` emits nothing at all anyway. `$129,584–$129,584` is
    # the 0.8.2 rendering: it exists in the live consumer's store and in nothing this
    # tree can emit. So this is defence in depth against a RE-INTRODUCTION of that
    # rendering, and against a library caller handing `derive_salary` 0.8.2-shaped rows
    # out of its own store -- which is a real caller, not a hypothetical one, because
    # this is a public library function. It is not a live pipeline leak today.
    #
    # THE `salary_min`/`salary_max` HALF IS LOAD-BEARING, not belt-and-braces: a REAL
    # Adzuna range arrives with `vocab.salary(..., basis="stated")` already applied, and
    # it must keep behaving exactly as before, `salary_kind` included. Only a row whose
    # vendor sent NO numbers is quarantined -- which is precisely the predicted-or-absent
    # case, because those two are indistinguishable in the record (see
    # `sources.PREDICTED_PAY_SOURCES` for why the source name is the only available key).
    # THE KIND IS SET ABOVE THE FILL-ONLY GUARD, and that placement is the whole point.
    # Everything below returns early when a vendor already sent numbers -- but a
    # vendor-stated figure carrying an OTE or equity label is EXACTLY the defect this
    # field exists to catch, and those rows are the majority: putting this line below
    # putting this line below the guard meant only 411 of 42,072 rows with a salary could
    # be labelled AT ALL -- 222 of them got a kind and none was equity, against 81 equity
    # rows when the call sits above. That is how the misplacement was caught.
    #
    # WAS: "411 of 41,665 rows". Both halves were individually plausible and neither
    # described this quantity: 411 is the population that could REACH the call, not the
    # number labelled, and 41,665 is the `salary_min`-filled count from the fingerprint
    # HARNESS -- a different instrument measuring a different thing. A ratio whose
    # numerator and denominator come from different instruments reads as sound from
    # either end. It describes the figure the EMPLOYER wrote, so it
    # is independent of whether our parser can turn that string into numbers.
    if p.get("salary"):
        p["salary_kind"] = salary_kind(p.get("text") or "", p["salary"])
    if p.get("salary_min") is not None or p.get("salary_max") is not None:
        return p
    # BELOW THE FILL-ONLY RETURN ON PURPOSE, and the first draft of this guard had it
    # above. There it also fired on an Adzuna row that carried a display string with no
    # structured numbers -- not a prediction -- and stripped its `salary_kind`, because
    # the kind is assigned above the fill-only return and that placement is deliberate
    # (see the 411-of-42,072 measurement in the block above; it is how the original
    # misplacement was caught). Down here the guard is strictly narrower and the
    # `salary_min is None and salary_max is None` condition it used to carry is
    # redundant: every row with a vendor figure has already returned.
    #
    # BOTH THE FIX AND ITS COST ARE UNREACHABLE FROM THE ADAPTER, which is why the move
    # is justified by narrowness alone and not by a bug report. Enumerated over every
    # branch of `_adzuna_pay`: `util.salary_range` returns non-empty exactly when
    # `vocab.salary` fills at least one of min/max, so "display string AND no numbers"
    # -- the row the old placement stripped a kind from -- occurs on none of them.
    #
    # The cost is bounded by the same fact. `salary_kind` is assigned under
    # `if p.get("salary")`, and a real predicted row carries `salary == ""`, so the kind
    # is never set on one: measured, a predicted row as the adapter emits it comes out
    # `salary_kind=None`. The only row that pays the cost -- a prediction labelled
    # `base` with a null `salary_min` -- is that same hand-constructed row, reachable by
    # a library caller and by nothing in this pipeline. Recorded rather than left to be
    # discovered, and recorded WITH its reachability so neither fact is overstated.
    #
    # For WHY the key is the source name and nothing finer, see
    # `sources.PREDICTED_PAY_SOURCES`: a predicted Adzuna row and a genuine no-figure
    # Adzuna row are byte-identical in the record once `_coerce` has run.
    if p.get("source") in PREDICTED_PAY_SOURCES:
        return p
    # WHY THERE ARE NO salary_estimated_* COLUMNS, since the guard above is what stands
    # in their place. The fix at the time was a pair of estimate columns plus an early
    # return here when they were set. On the last release that SHIPPED them the
    # separation leaked anyway: all 6,633 rows carrying an estimate also rendered it as
    # "$129,584–$129,584" -- EN-DASH, U+2013, which is what the store holds; a
    # LIKE '%-%' on an ASCII hyphen returns 0 of 6,633 and reads as a refutation -- with
    # 0 carrying a commitment figure `[live prod, engine 0.8.2]`. That is the HISTORY,
    # not the reason they went: `fa0cee3` and `9907e55` closed the leak earlier in
    # 0.9.0. They went because nothing downstream ever read them.
    display = p.get("salary")
    if not display:
        # A CHEAP EARLY-OUT, NOT THE QUARANTINE. An earlier version of this comment
        # called it "the guard" and four other sites repeated the claim. It refuses
        # nothing this function would otherwise accept: deleting this line in an
        # isolated export left the guarding test GREEN (`1/710 tests collected`, the
        # test named), because `vocab.salary_from_display("")` returns all-None and the
        # next check returns on that. It is not a performance guard either: timed over
        # 20,000 real corpus bodies, `_adjacent_evidence(body, "")` costs 0.52 us/row,
        # about 32 ms across the 60,716 fall-through rows of a whole harvest. The line
        # is vestigial. It stays because Phase 1's section-scoped body scan is what
        # replaces it, and replacing it now would be an irreversible change made on a
        # provisional plan; the quarantine above is deliberately independent of it.
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
    #
    # WHOEVER SUPPLIED THE RAW OWNS THE PARSE. `remote_scope_raw is None` is the adapter
    # saying "I recorded no boundary evidence", which is the only state in which this
    # string is ours to read. An adapter that DID set the raw has already decided what its
    # own words mean -- including deciding they mean nothing -- and re-reading them here
    # second-guesses it with a prose rule built for a different kind of string.
    #
    # `None` alone could not express that. It meant both "unstated" and "derive me", so an
    # adapter had no way to say "there is no boundary here, do not invent one", and
    # google_jobs could not stop `Anywhere` from becoming a worldwide claim. That token is
    # Google's SEARCH MODE under `&ltype=1`, not the posting's words -- it sits on exactly
    # the 43 rows that carried `[]` and on none of the 9 that name a city, and 11 of the 43
    # state a US-only bound in their own title, body or URL ("Open-Source Machine Learning
    # Engineer - US Remote" recorded as stated-worldwide). Through
    # `scoring._region_allowed`, `[] -> return True` is the one unconditional bypass in the
    # scoring layer, so every one of them satisfied a filter that excludes them.
    #
    # Measured at 781e504: 43 rows lose the `[]`, 348 adapter-supplied boundaries are kept,
    # and ZERO rows lose a boundary the location legitimately stated.
    if (
        p.get("remote_scope_raw") is None
        and p.get("remote_areas") is None
        and p.get("remote_regions") is None
    ):
        areas, regions = vocab.remote_scope(location)
        if areas is not None:
            p["remote_areas"] = areas
        if regions is not None:
            p["remote_regions"] = regions
    if p.get("remote_scope_raw") is None and location:
        p["remote_scope_raw"] = location

    return p


# The order a PERSON reads a record in, applied once at the end of `harvest`.
#
# A record's key order carried no decision at all before this: it was whatever the
# adapter's dict literal happened to list, then whatever `_coerce` and `_consume`
# appended. JSON does not care, and neither does any consumer -- but the owner of
# this repo opened the harvest output, could not read it, and asked for the shape to
# be simplified. Measured on that file `[local 94-board harvest, 0.9.0]`: `company`
# came 21 fields in, BELOW the 6,870-character `text` body and its `sections` list;
# `title_root` sat 15 fields from the `title` it decomposes; `city`/`state`/`country`
# came after the `locations` list they summarize; the salary group was split in two.
#
# THE BODY GOES LAST, and that is the whole point rather than a tidy-up. `text` is
# 72% of the median record's bytes, so wherever it sits, everything after it is past
# a wall of prose -- which is why `company` was unfindable. Nothing here changes a
# value, a type, or which keys exist; it is presentation, and it is the one change in
# this pass that addresses what was actually asked for.
_READING_ORDER = (
    # what the role is
    "title", "title_root", "title_level", "title_qualifiers",
    # who is hiring, and for which group
    "company", "department", "tags",
    "seniority", "seniority_raw", "seniority_basis",
    # where the work is, then where a remote worker may sit
    "location", "city", "state", "country", "locations",
    "remote_type", "remote_basis",
    "remote_areas", "remote_regions", "remote_scope_raw",
    # when
    "posted", "posted_basis", "expires", "harvested_at",
    # money -- the commitment, then the model's guess, never interleaved
    "salary", "salary_min", "salary_max", "salary_currency", "salary_period",
    "salary_basis", "salary_kind",
    # terms
    "employment_type", "employment_type_raw",
    # whether a person can apply at all -- beside the other eligibility facts, and
    # nowhere near the end: this tuple's ORDER is the delivered reading order, so an
    # append displaces the body from the last slot a test pins it to.
    "sponsorship", "sponsorship_basis", "clearance", "clearance_basis",
    # how to apply
    "url", "direct_apply",
    # what we made of it
    "score", "signals",
    # where it came from
    "source", "sources", "source_extra", "dedup_key",
    # the body LAST: 72% of the record, and everything after it is unreadable
    "text", "text_basis", "sections",
)  # fmt: skip


def _shape(p: dict, cfg) -> None:
    """Apply the two OUTPUT-SHAPE levers to one record, in place.

    Runs in `harvest`, not in `emit`, and that is the point: `emit` is the one module
    the only known consumer imports nowhere, so a lever that lives only there is
    reachable from argparse and from nothing else. A library caller doing
    `engine.harvest(cfg)` gets both of these because `harvest` installs the cfg
    process-wide. `emit.records` applies the same two for the CLI's own reasons -- it
    also emits STORE rows, which never passed through this function.

    Neither lever changes a value and neither drops a row. `include_text=False`
    removes `text` and `text_basis`; `text_basis` goes because it characterizes a body
    that is no longer present. `omit_empty` removes keys whose value is None or "".

    `[]` AND `{}` SURVIVE `omit_empty`, and that is not an oversight. `remote_areas:
    []` means the posting STATED it is open anywhere, which is a fact it took work to
    establish and is NOT the same as `null` (it said nothing); `sections: []` means we
    read the body and found no headers. Dropping those would destroy exactly the
    two-state distinctions the contract exists to carry. Only None and "" -- which
    already mean "nothing here" -- are removed.
    """
    if not getattr(cfg, "include_text", True):
        p.pop("text", None)
        p.pop("text_basis", None)
    if getattr(cfg, "omit_empty", False):
        for k in [k for k, v in p.items() if v is None or v == ""]:
            del p[k]


def _reorder(p: dict) -> None:
    """Rewrite one record's keys into `_READING_ORDER`, in place.

    IN PLACE because `hits` holds these same objects; rebinding would leave the
    de-dup index pointing at the unordered originals.

    A KEY THIS TUPLE DOES NOT NAME IS KEPT, sorted, at the end -- never dropped.
    An allowlist here would mean adding a contract field and silently deleting it
    from every record until someone remembered this tuple, which is the same class
    of failure as `emit._nested`'s hand-written field list (see its docstring: a
    0.7.0 rename reached the contract and all nineteen adapters but not that
    function, and nothing failed). Ordering must never be able to lose data.
    """
    rest = sorted(k for k in p if k not in _ORDER_INDEX)
    ordered = [(k, p[k]) for k in _READING_ORDER if k in p]
    ordered += [(k, p[k]) for k in rest]
    p.clear()
    p.update(ordered)


_ORDER_INDEX = frozenset(_READING_ORDER)


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
        # AFTER THE LAST GATE, not beside `derive_remote`/`derive_salary`. Those two run
        # early because the remote gate reads what they set; nothing here feeds a gate,
        # so running it last does identical work on strictly fewer rows. Measured: 0 of
        # 30,000 rows differ between the two placements -- placement changes which rows
        # are REACHED, never what they return. Of rows passing the relevance gate, 76.9%
        # die at the remote gate and 2.7% more at age, so 20.4% reach this line under
        # `remote_only=True, max_age_days=60`. Under `remote_only=False` the remote gate
        # returns True unconditionally and this placement saves nothing -- a drop rate is
        # a property of the CONFIG, not of the corpus, which is why the config is named
        # beside the number.
        extract.enrich(p)
        p.setdefault("company", "")
        p.setdefault("source", "")
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
        # KEYED THE SAME WAY THE COMPANY IS STAMPED, which it was not until 0.9.0.
        # `_consume` looks this up as `meta.get(norm(p["company"]))`, and `company` is
        # stamped `c.get("name", slug or "?")` below -- so a NAMELESS entry was keyed on
        # `norm("")` and looked up on `norm(slug)`, and its `frontier`/`local` flags
        # silently stopped applying. Measured: a nameless `frontier: true` board scored
        # 11 instead of 1 with no `frontier-reach` signal, and `local` 11 instead of 21.
        # Two defaults for one key; the fix is to use the same one on both sides.
        #
        # PRE-EXISTING, not introduced by the fill, and exposure on disk is 0 -- no entry
        # in any shipped watchlist is nameless. That is exactly why nothing caught it,
        # and why the fill's own alias below did not cover it: `norm(name) in meta` is
        # False for an entry that has no name.
        # CASE-SENSITIVITY, measured rather than assumed: 293 of 727 watchlist entries
        # are casefold-equal to their slug but NOT byte-equal (`Anthropic`/`anthropic`,
        # `Stripe`/`stripe`) -- correct curated names, and the fill correctly leaves
        # every one of them alone. The inverse shape, byte-equal but semantically
        # distinct, is 0 of 727. A case-insensitive test here would sweep in all 293.
        meta[norm(c.get("name") or c.get("slug") or "")] = {
            "frontier": bool(c.get("frontier")),
            "local": bool(c.get("local")),
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
                    slug = c.get("slug")
                    name, ats = c.get("name", slug or "?"), c.get("ats")
                    # FILL-ONLY. A watchlist name byte-equal to the slug was never
                    # sourced from anywhere: `seed._seeded` assigns
                    # `"name": entry["slug"]` to every mined board. In the 7,360-board
                    # universe behind our reference corpus, 5,052 of 5,052 boards whose
                    # provenance is `cdx-discovery` are exactly that -- 100%, no
                    # exceptions. Those are the rows a user reads as
                    # `langanengineeringandenvironmentalservicesllc`.
                    #
                    # So fill from the adapter's own company ONLY there, never over a
                    # curated name. Vendor-always was measured and REJECTED: across 40
                    # boards with a curated name the vendor disagreed on 6, and the
                    # disagreement runs BOTH ways -- 'DoorDash' -> 'DoorDash USA' but
                    # also 'Canonical Ltd.' -> 'Canonical', so no prefer-longer or
                    # prefer-shorter tie-break is available either. `allwebleads` ->
                    # 'AWL' is the shape that settles it: a vendor's own name can be
                    # LESS legible than the slug, which no win rate offsets.
                    #
                    # The byte-equality test is deliberately LOOSE -- 3 of 94 curated
                    # boards are byte-equal too ('Cohere', 'Linear', 'Perplexity').
                    # That contamination is harmless in THIS direction, which is the
                    # non-obvious part: if the curated name is already right, the vendor
                    # reports the same string, so the fill is a no-op on exactly the
                    # false positives. A test too loose to IDENTIFY a defect can still
                    # be safe to ACT on.
                    curated = name if name != slug else None
                    for p in ps:
                        vendor = p.get("company")
                        p["company"] = curated or (
                            vendor
                            if isinstance(vendor, str) and vendor.strip()
                            else name
                        )
                        p["source"] = ats
                    # RE-KEY `meta`, or the fill silently turns off `frontier` and
                    # `local` for a board whose name it MOVES UNDER `norm()`.
                    # WAS: "every board it renames", which overclaimed twice. A
                    # CASE-ONLY fill needs no alias at all -- `norm('astranis') ==
                    # norm('Astranis')` -- and casing alone is 32 of 40 in the
                    # measured draw, so this is load-bearing on roughly 8 of 40.
                    # And it never covered a NAMELESS entry, because `norm(name) in
                    # meta` is False when there is no name; that is a separate,
                    # pre-existing defect fixed at the `meta` keying above.
                    # `meta` is built above as
                    # `meta[norm(c.get("name") or c.get("slug") or "")]` while `_consume` looks it up as
                    # `meta.get(norm(p["company"]))` -- so renaming the company moves
                    # the lookup off its own entry and the flag stops applying, with
                    # no error and no log line. Measured on a frontier board that the
                    # fill renames: score 19.0 with the alias missing vs 9.0 with it,
                    # a silent 10-point swing.
                    #
                    # An alias rather than a rewrite: the original key must keep
                    # working, because breadth postings for the same employer arrive
                    # under the watchlist's spelling and read the same `meta`.
                    if ps:
                        filled = norm(ps[0]["company"])
                        if filled and filled not in meta and norm(name) in meta:
                            # LOAD-BEARING ON ROUGHLY 8 OF 40 BOARDS, not on the whole
                            # fill population: a CASE-ONLY fill needs no alias at all,
                            # because `norm('astranis') == norm('Astranis')`, and casing
                            # alone is 32 of 40 in the measured draw. The flag only moves
                            # when `norm()` moves.
                            meta[filled] = meta[norm(name)]
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
        r.pop("_url_recovered", None)  # adapter-internal; read once by _coerce
        _shape(r, cfg)  # BEFORE _reorder, so a dropped key cannot be reordered back
        _reorder(r)
    return rows, discovered, errors
