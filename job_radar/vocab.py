"""The closed vocabularies, and the normalizers that map onto them.

One module, on purpose. These are the values a consumer is allowed to see in
`employment_type`, `salary_period` and `remote_type`, plus the raw-to-normal maps
that get there. Smeared across nineteen adapters they would drift within a week;
here a typo is a one-line diff and a test can assert the whole map lands inside the
legal set.

WHERE THE VOCABULARIES COME FROM. Not invented. `employment_type` is schema.org's
`employmentType` (https://schema.org/JobPosting), which every source in this corpus
already targets — they emit JSON-LD to be eligible for Google for Jobs, so the
strings they send are already trying to be these values. `salary_period` is
schema.org's `unitText`. Normalizing toward what the sources already speak beats a
bespoke vocabulary that nobody else uses.

NOTHING IS DESTROYED. Every normalizer has a partner `*_raw` field on the record
holding exactly what the vendor sent. An unrecognised value maps to `OTHER` and
keeps its raw; it is never dropped and never guessed at.
"""

from __future__ import annotations

import re

from .dedup import _QUAL_RE, _TITLE_NOISE

# ── employment type ─────────────────────────────────────────────────────────
EMPLOYMENT_TYPES = frozenset(
    {
        "FULL_TIME",
        "PART_TIME",
        "CONTRACTOR",
        "TEMPORARY",
        "INTERN",
        "VOLUNTEER",
        "PER_DIEM",
        "OTHER",
    }
)

# Raw vendor strings -> the closed set. Keys are lowercased and stripped of
# punctuation before lookup, so one entry covers "Full-time", "FULL_TIME",
# "full time" and "FullTime".
#
# `permanent` -> FULL_TIME is a JUDGMENT CALL and the one entry here most likely to
# be wrong: it is UK/EU usage where it contrasts with fixed-term, not with part-time.
# A permanent part-time role would land wrong. It is mapped anyway because the
# alternative (OTHER) is less useful far more often, and `employment_type_raw`
# preserves the truth either way.
_EMPLOYMENT_MAP = {
    "full time": "FULL_TIME",
    "fulltime": "FULL_TIME",
    "regular full time": "FULL_TIME",
    "regular full time salary": "FULL_TIME",
    "permanent": "FULL_TIME",
    "part time": "PART_TIME",
    "parttime": "PART_TIME",
    "regular part time": "PART_TIME",
    "contract": "CONTRACTOR",
    "contractor": "CONTRACTOR",
    "contract to hire": "CONTRACTOR",
    "freelance": "CONTRACTOR",
    "c2c": "CONTRACTOR",
    "b2b": "CONTRACTOR",
    "temporary": "TEMPORARY",
    "temp": "TEMPORARY",
    "seasonal": "TEMPORARY",
    "fixed term": "TEMPORARY",
    "internship": "INTERN",
    "intern": "INTERN",
    "apprenticeship": "INTERN",
    "trainee": "INTERN",
    "volunteer": "VOLUNTEER",
    "per diem": "PER_DIEM",
    "prn": "PER_DIEM",
}

_PUNCT = re.compile(r"[^a-z0-9 ]+")


def _flatten(v) -> str:
    """Vendor values arrive as a string OR a list of them (jobicy, arbeitnow).
    Take the first meaningful one; the raw field keeps the whole thing."""
    if isinstance(v, (list, tuple)):
        v = next((x for x in v if x), "")
    return str(v or "")


def employment_type(raw) -> tuple[str | None, str | None]:
    """Vendor string -> (normalized, raw). `None` means the source said nothing.

    Returns OTHER (never None) for a value we received but do not recognise: the
    source DID say something, and collapsing that to "did not say" is the same lie
    as `remote: False` for unknown.
    """
    raw_s = _flatten(raw).strip()
    if not raw_s:
        return None, None
    key = _PUNCT.sub(" ", raw_s.lower())
    key = " ".join(key.split())
    return _EMPLOYMENT_MAP.get(key, "OTHER"), raw_s


# ── salary period ───────────────────────────────────────────────────────────
SALARY_PERIODS = frozenset({"year", "month", "week", "day", "hour", "fixed"})

_PERIOD_MAP = {
    # generic
    "yr": "year", "year": "year", "yearly": "year", "annual": "year",
    "annually": "year", "annum": "year", "pa": "year", "per year": "year",
    "mo": "month", "month": "month", "monthly": "month",
    "wk": "week", "week": "week", "weekly": "week",
    "day": "day", "daily": "day", "per diem": "day",
    "hr": "hour", "hour": "hour", "hourly": "hour",
    "fixed": "fixed", "fixed price": "fixed", "project": "fixed",
    # PROBED 2026-08-05, exact vendor strings -- these are why the map exists
    "per year salary": "year",   # lever salaryRange.interval
    "per month salary": "month",  # lever
    "per week salary": "week",    # lever
    "per day wage": "day",        # lever
    "per hour wage": "hour",      # lever
    "per task": "fixed",          # braintrust payment_type
    "one time": "fixed",
    # usajobs RateIntervalCode. Only PA was seen live 2026-08-05 (nurse, 25 rows);
    # the rest are from OPM's published code list and are UNVERIFIED against a real
    # posting -- they map to a period we would otherwise have had to guess.
    # ("pa" already maps to year above -- the OPM code and the generic
    # per-annum abbreviation happen to be the same string.)
    "ph": "hour", "pd": "day", "pw": "week", "pm": "month",
    "pb": "fixed", "wc": "fixed",
}  # fmt: skip


def salary_period(raw) -> str | None:
    """Vendor period string -> the closed set, or None if unrecognised.

    None rather than a default, deliberately. A salary figure whose period we
    guessed is worse than one with no period at all: every aggregate built on it is
    silently wrong, and both `65` and `135000` are valid numbers.
    """
    s = _PUNCT.sub(" ", _flatten(raw).lower())
    return _PERIOD_MAP.get(" ".join(s.split()))


def salary(lo=None, hi=None, currency=None, period=None, basis="stated") -> dict:
    """Structured salary -> the five record fields. Returns all-None when there is
    no real figure.

    ZERO IS NOT A SALARY. RemoteOK sends `salary_min` and `salary_max` on all 100
    rows of its feed and both are `0` (probed 2026-08-05) -- the keys exist, the data
    does not. Mapping that straight through would assert a salary of zero on every
    row, which is the same class of lie as `remote: False` for unknown. A falsy
    figure is dropped to None.

    A period is never guessed. `65` and `135000` are both valid numbers, so a wrong
    period makes every aggregate built on it silently wrong -- worse than no period
    at all.
    """

    def _num(v):
        try:
            f = float(v)
        except (TypeError, ValueError):
            return None
        return f or None  # 0 -> None; see the docstring

    lo, hi = _num(lo), _num(hi)
    if lo is None and hi is None:
        return {
            "salary_min": None, "salary_max": None, "salary_currency": None,
            "salary_period": None, "salary_basis": None,
        }  # fmt: skip
    return {
        "salary_min": lo,
        "salary_max": hi if hi is not None else lo,
        "salary_currency": (str(currency).upper() if currency else None),
        "salary_period": salary_period(period),
        "salary_basis": basis,
    }


# Google for Jobs states its salary as free text with the PERIOD EMBEDDED --
# "47–55 an hour", "2,140 a week", "50 an hour" (probed 2026-08-05). Not annual, and
# not a number a generic currency regex would give a period to. This is the exact
# case that makes salary_period load-bearing: 47 and 2140 and 50 are all valid
# numbers and mean nothing without their unit.
# Anchored on the PERIOD and read right-to-left, which is the only shape that
# survives what Google actually sends. Three real strings broke the first version:
#
#   "US$6,117–US$8,342 a month"  the repeated currency prefix made an unanchored
#                                scan skip the low figure, so the MAXIMUM was written
#                                into salary_min -- a wrong number in the column that
#                                means "what the employer committed to as a floor",
#                                carrying basis="parsed" as though it were reliable.
#                                That is worse than dropping it.
#   "101K–132K a year"           the K suffix was unhandled; dropped entirely.
#   "81,873–104,388 a year"      the only one of the four that worked.
#
# So: find the unit, then take the one-or-two numbers immediately before it, allowing
# an arbitrary currency prefix on each and an optional K/M multiplier.
_G_NUM = r"(?:[^\d\s]{0,3}\s?)?(?P<%s>[\d,]+(?:\.\d+)?)\s*(?P<%s>[KkMm])?"
_G_SALARY = re.compile(
    _G_NUM % ("lo", "lomul")
    + r"\s*(?:[-–—]|to)?\s*(?:"
    + (_G_NUM % ("hi", "himul"))
    + r")?\s*"
    r"(?:an?|per)\s+(?P<per>hour|hr|week|wk|month|mo|year|yr|day)",
    re.I,
)
_MULT = {"k": 1_000, "m": 1_000_000}


def google_salary(raw) -> dict:
    """Google's `detected_extensions.salary` -> the structured fields.

    Returns all-None when the string does not parse, rather than a number with a
    guessed period. `basis` is "parsed" -- this is text Google assembled, not a field
    an employer filled in.
    """
    m = _G_SALARY.search(str(raw or ""))
    if not m:
        return salary()

    def _n(num, mul):
        try:
            v = float(str(num).replace(",", ""))
        except (TypeError, ValueError):
            return None
        return (v * _MULT[mul.lower()] if mul else v) or None

    lo = _n(m.group("lo"), m.group("lomul"))
    hi = _n(m.group("hi"), m.group("himul"))
    # ORDER IS NOT GUARANTEED by the regex alone. If the low group somehow captured
    # the larger figure, swapping is the honest repair -- a min above its max is a
    # nonsense record, and silently keeping it is how "$8,342 minimum" ships.
    if lo is not None and hi is not None and lo > hi:
        lo, hi = hi, lo
    return salary(lo, hi, currency="USD", period=m.group("per"), basis="parsed")


# OPM position-schedule codes -> the employment_type vocabulary. USAJOBS' own
# `PositionSchedule[].Name` is EMPTY on 47 of 50 rows measured, and on the three that
# have it, it is a shift pattern ("Monday through Friday, 8:00am to 4:30pm") that
# normalizes to OTHER. Not one row in 50 yielded FULL_TIME from the name. `.Code` is
# present on 50/50.
#
# Only code "1" appeared live; the rest are from OPM's published list and are
# UNVERIFIED, on the same footing as the RateIntervalCode entries above.
USAJOBS_SCHEDULE = {
    "1": "FULL_TIME",
    "2": "PART_TIME",
    "3": "PART_TIME",  # shift
    "4": "TEMPORARY",  # intermittent
    "5": "PER_DIEM",
    "6": "TEMPORARY",  # on-call / seasonal
}


# ── remote type ─────────────────────────────────────────────────────────────
REMOTE_TYPES = frozenset({"remote", "hybrid", "onsite"})

_REMOTE_MAP = {
    "remote": "remote", "fully remote": "remote", "remote first": "remote",
    "telecommute": "remote", "work from home": "remote", "wfh": "remote",
    "anywhere": "remote", "distributed": "remote",
    "hybrid": "hybrid", "flexible": "hybrid", "partially remote": "hybrid",
    "on site": "onsite", "onsite": "onsite", "in office": "onsite",
    "in person": "onsite", "office": "onsite", "no remote": "onsite",
}  # fmt: skip


def remote_type(raw) -> str | None:
    """Vendor work-arrangement string -> remote | hybrid | onsite | None.

    `hybrid` is why this is an enum rather than the boolean it used to be. It is
    already a real concept in this codebase (dedup._QUAL_NOISE lists it) and a bool
    cannot express it -- a hybrid role reported as `remote: False` reads as on-site
    to anything that only checks the flag.
    """
    s = _PUNCT.sub(" ", _flatten(raw).lower())
    return _REMOTE_MAP.get(" ".join(s.split()))


# ── title decomposition ─────────────────────────────────────────────────────
# The seniority words that can DECORATE a title. Every one of them can also BE a
# job -- "Director of Engineering", "Team Lead", "Chief of Staff" -- which is what
# the positional rule in decompose_title exists to protect.
_SENIORITY = {
    "jr": "junior", "junior": "junior", "sr": "senior", "senior": "senior",
    "staff": "staff", "principal": "principal", "lead": "lead",
    "associate": "associate", "head": "head", "chief": "chief", "vp": "vp",
    "director": "director", "intern": "intern",
}  # fmt: skip

# Roman/arabic level marks. Distinct from seniority on purpose: "Engineer II" and
# "Senior Engineer" are different axes and some employers use both at once.
_LEVEL = {
    "i": "I", "1": "I", "ii": "II", "2": "II",
    "iii": "III", "3": "III", "iv": "IV", "4": "IV",
}  # fmt: skip

_WORD_RE = re.compile(r"[A-Za-z0-9+#/&]+")


def decompose_title(title: str) -> dict:
    """`"Senior AI Engineer II - Remote"` -> root, seniority, level, qualifiers.

    THE RULE, and the only interesting thing here: **a seniority word is decoration
    only when it LEADS the title and something real follows it.** Otherwise it is
    the role. Without that test the naive version turns "Director of Engineering"
    into "of Engineering" and "Associate" into "", which is worse than not parsing
    at all -- a wrong root silently mis-matches every consumer that groups on it.

    Two guards enforce it:
      * a modifier is only stripped from the FRONT, and only when the next word is
        not "of" (the head-noun test: "VP of Sales" is a VP, "Senior Director" is a
        Director)
      * stripping never empties the root; it falls back to the whole title

    Verified against 21 real title shapes including the eight that break the naive
    version. `title` itself is never modified -- this only ever ADDS fields.

    Reads `dedup._TITLE_NOISE` and `dedup._QUAL_RE` rather than copying them.
    Those tables decide MERGES, so a divergent copy here would mean the root and
    the matcher disagreed about what a title says -- and a wrong merge deletes a
    job. Read them; never edit them for this function's benefit.
    """
    t = (title or "").strip()
    quals: list[str] = []
    for m in _QUAL_RE.finditer(t.lower()):
        piece = (m.group(1) or m.group(2) or "").strip()
        if piece:
            quals.extend(x.strip() for x in re.split(r"[(),]", piece) if x.strip())
    stem = _QUAL_RE.sub("", t)
    toks = _WORD_RE.findall(stem)

    seniority = level = None
    i = 0
    while i < len(toks) - 1 and toks[i].lower() in _SENIORITY:
        if toks[i + 1].lower() == "of":
            break  # head noun -- "Director of Engineering" IS a Director
        if seniority is None:
            seniority = _SENIORITY[toks[i].lower()]
        i += 1

    root_words = []
    for w in toks[i:]:
        lw = w.lower()
        if lw in _LEVEL and level is None:
            level = _LEVEL[lw]
            continue
        if lw in _TITLE_NOISE:
            continue
        root_words.append(w)
    root = " ".join(root_words).strip(" -–—,/")

    return {
        "title_root": root or t,  # never empty; falls back to the whole title
        "title_level": level,
        "title_qualifiers": [q for q in quals if q not in _TITLE_NOISE] or None,
        "seniority": seniority,
    }
