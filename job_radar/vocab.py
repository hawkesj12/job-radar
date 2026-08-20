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

from .dedup import _QUAL_NOISE, _QUAL_RE, _TITLE_NOISE

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

# A trailing parenthetical QUALIFIES a type; it does not change it. Braintrust's
# adapter builds `f"contract ({contract_type})"` (sources.py), so the string that
# arrives here is "contract (long)" -- `_PUNCT` flattens it to "contract long",
# which is not in the map, so 29 of 29 braintrust rows normalized to OTHER while
# bare "contract" three lines up maps to CONTRACTOR. The adapter was defeating its
# own normalizer. Applied ONLY after a direct lookup misses, so it can never change
# a value that already resolved: measured over the corpus, 29 rows rescued, 0
# changed. The raw keeps the qualifier -- "contract (long)" is the truth.
_PAREN_TAIL = re.compile(r"\s*\([^)]*\)\s*$")


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
    hit = _EMPLOYMENT_MAP.get(key)
    if hit is None:
        # Retry once with a trailing parenthetical removed -- see _PAREN_TAIL. Only
        # on a miss, so a value that already resolved cannot be altered here.
        stripped = _PAREN_TAIL.sub("", raw_s).strip()
        if stripped != raw_s:
            key = " ".join(_PUNCT.sub(" ", stripped.lower()).split())
            hit = _EMPLOYMENT_MAP.get(key)
    return hit or "OTHER", raw_s


# ── seniority ───────────────────────────────────────────────────────────────
# THERE IS NO CLOSED VOCABULARY HERE, AND THAT IS DELIBERATE. Every other
# normalizer in this module maps onto a vocabulary someone else published --
# `employment_type` onto schema.org's `employmentType`, `salary_period` onto its
# `unitText` (see the module docstring: "Not invented"). No such enumeration exists
# for seniority, so a closed set of rungs would be an OPINION about how levels
# order, and `catalog/_SCHEMA.md` ("Fidelity, not opinion ... whether a consumer
# maps a category is that consumer's business") governs this field exactly as it
# governs `category`.
#
# The concrete harm that settled it, probed live 2026-08-20: SmartRecruiters'
# `experienceLevel` ships LinkedIn's published enumeration verbatim -- Internship,
# Entry level, Associate, Mid-Senior level, Director, Executive -- in which
# `Associate` ranks ABOVE `Entry level`. The one ladder available to copy collapses
# `Associate` to an entry rung, so adopting it would have contradicted a vendor's
# own published ordering on the source where it fires on 60% of rows.
#
# So this does exactly two mechanical things and no judgment:
#   - CASE-FOLDS. `Senior` and `senior` are the same word. Measured: a
#     `seniority='senior'` filter missed 319 rows, 179 of them for case alone.
#   - NULLS the two values that explicitly decline to classify.
# `Mid-Senior Level` stays `mid-senior level`, NOT `senior`. `Associate` stays
# `associate`, NOT `entry`. Those are the ladder, and the ladder belongs downstream.
_SENIORITY_DECLINED = frozenset({"not applicable", "any", "n/a"})


def seniority(raw) -> tuple[str | None, str | None]:
    """Vendor level string -> (case-folded, raw). `None` means no level was stated.

    Case-folding only -- see the block comment above for why there is no rung map.
    A vendor that answered "Not Applicable" DID answer, so the value is `None` (we
    have no level) while the raw preserves that they responded at all.
    """
    raw_s = _flatten(raw).strip()
    if not raw_s:
        return None, None
    folded = " ".join(raw_s.lower().split())
    return (None if folded in _SENIORITY_DECLINED else folded), raw_s


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
    # ashby summaryComponents[].interval. Measured on openai (n=594 salary
    # components): '1 YEAR' 592, '1 HOUR' 2 -- the leading count is part of the
    # string, so 'year' alone never matches and every Ashby salary would have been
    # dropped for an unrecognised period.
    "1 year": "year", "1 month": "month", "1 week": "week",
    "1 day": "day", "1 hour": "hour",
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
# Periods per year, for the implied-annual sanity check in salary_from_display.
_ANNUALIZE = {"hour": 2080, "day": 260, "week": 52, "month": 12, "year": 1}


def _distribute_multiplier(lo_n, lo_mul, hi_n, hi_mul):
    """`$200-260K` means 200,000-260,000. The K is written ONCE and distributes left.

    THE BUG THIS EXISTS TO STOP, because it is one character from shipping: `_G_NUM`
    binds the multiplier per-number, so the pattern reads `$200-260K` as lo=200 and
    hi=260,000 -- `salary_min` of 200 on a $200K job, wrong by 1,300x, in the column
    README:39 defines as what the employer COMMITTED to. Measured on 26 rows of
    `_reports/flat.ndjson` [local 94-board harvest, 0.9.0, 2026-08-20].

    It is latent in `google_salary` today and harmless ONLY because that pattern
    requires a trailing period phrase, which gates these strings out. Making the
    period optional -- which is the whole point of `salary_from_display` -- is
    precisely what arms it. Same class as the `US$6,117-US$8,342` failure recorded
    above, which wrote the MAXIMUM into `salary_min`.

    Guarded on `lo < 1000`: a bare `200` beside `260K` cannot be a real floor, while
    `150,000 - 250,000` needs no help and must not be touched.
    """
    if lo_n is None or hi_n is None:
        return lo_n, hi_n
    if not lo_mul and hi_mul and lo_n < 1000:
        return lo_n * _MULT[hi_mul.lower()], hi_n
    return lo_n, hi_n


# Same shape as _G_SALARY but the trailing period phrase is OPTIONAL, because an
# employer writes `$200,000 - $250,000` and Google writes `165K-195K a year`. That
# one difference is why 3,500 rows carried a fully formed pay range in `salary` with
# every structured column null: the only text parser in the codebase could not read
# a period-less range, so `README.md:41`'s promise that basis="parsed" means "read
# out of free text" was kept on exactly 12 rows corpus-wide.
_D_SALARY = re.compile(
    _G_NUM % ("lo", "lomul")
    + r"\s*(?:[-–—]|to)\s*(?:"
    + (_G_NUM % ("hi", "himul"))
    + r")\s*"
    # `a year` / `per hour` AND `/hr` — employers write both, and the slash form is
    # 30 of 4,703 display strings. 28 of those 30 were rescued by the adjacency
    # window because the body happened to spell it out nearby; reading it off the
    # string itself does not depend on that luck.
    r"(?:\s*(?:(?:an?|per)\s+|/\s*)(?P<per>hours?|hrs?|weeks?|wks?|months?|mos?|years?|yrs?|days?))?",
    re.I,
)
# A range wider than this is not a pay band, it is a parse that went wrong -- it
# catches `$150,000 - 250,000k` ($250 MILLION) and the malformed `$306 - $390,000`.
# 5x is deliberately loose: real bands top out near 2x, so this only fires on nonsense.
_D_MAX_RATIO = 5


def salary_from_display(raw, period=None, currency=None) -> dict:
    """A display string like `$200,000 - $250,000` -> the structured fields.

    The sibling of `google_salary`, for the eleven adapters that produce a display
    string and nothing else. RANGES ONLY -- both endpoints required. A lone figure is
    refused because `$120,000` and `$60` and `up to $200,000` are indistinguishable
    here without a period, and a floor written into `salary_min` from a ceiling is
    the failure mode this whole function is built to avoid.

    `period` and `currency` are passed IN by the caller, which read them from text
    ADJACENT to the figure. They are never inferred from the number's magnitude --
    see `salary()`. Magnitude is used only to REFUSE a stated period, which is
    disbelieving a witness rather than inventing one.

    Returns all-None on anything it will not vouch for. Every refusal below was
    measured on `_reports/flat.ndjson` [local 94-board harvest, 0.9.0, 2026-08-20];
    3,292 of 3,500 target rows (94.1%) trip none of them.
    """
    m = _D_SALARY.search(str(raw or ""))
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
    lo, hi = _distribute_multiplier(lo, m.group("lomul"), hi, m.group("himul"))
    if lo is None or hi is None:
        return salary()
    if lo > hi:  # the honest repair; see google_salary
        lo, hi = hi, lo
    if hi / lo > _D_MAX_RATIO:
        return salary()

    # The string's own period wins over the caller's -- `165K-195K a year` states it
    # inline, and that is stronger evidence than a word 90 characters away.
    period = _PERIOD_MAP.get((m.group("per") or "").lower()) or period
    # A figure under 1,000 with no period is genuinely undecidable: `$30-120` is an
    # hourly rate or a thousands-shorthand and nothing here can tell. Emitting it
    # with period=None would manufacture exactly the unusable rows this is fixing.
    if lo < 1000 and not period:
        return salary()
    # MAGNITUDE VETO on a STATED period -- refusing to believe a witness, which is a
    # different act from inventing one. Two real failures, both found by hand-reading
    # output rather than by review:
    #
    #   "Estimated Hourly Pay Range $140,000 - $220,000"  -- the employer's own label,
    #   and believing it writes `hour` on a six-figure salary.
    #
    #   "$186,400 - $233,000 USD PLEASE NOTE: our policy requires a 90-day waiting
    #   period"  -- `day` harvested out of "90-day", giving $186,400 PER DAY. 66 of
    #   872 period assignments (7.6%) were absurd this way: 55 day, 9 week, 2 month.
    #   An earlier version of this veto covered only `hour` and `year` and missed
    #   every one of them.
    #
    # Implied-annual is the general form; per-unit thresholds only ever cover the
    # units someone thought of. The ceiling applies to SUB-annual periods only, so a
    # genuine $1.5M/yr package is not vetoed for being large.
    implied = lo * _ANNUALIZE.get(period or "", 0)
    if period and period != "year" and implied > 1_000_000:
        period = None
    elif period == "year" and hi < 5_000:
        period = None
    return salary(lo, hi, currency=currency, period=period, basis="parsed")


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


# ISO 3166-1 alpha-2. Sources disagree on the vocabulary for the SAME field: Lever's
# top-level `country` is already alpha-2 ('SG', 155 of 295 on binance), while Ashby's
# addressCountry is a display name ('Singapore'). Wiring both through verbatim would
# put two vocabularies in one contract column, so a consumer grouping by country would
# see 'US' and 'United States' as different places.
#
# Bounded ON PURPOSE, and this is the part worth keeping: the map covers the names our
# sources actually emit (measured -- 15 distinct on openai's Ashby board) plus the
# obvious neighbours. An unrecognised NAME returns None rather than a guess, because
# guessing a code from a name we have never seen is how a wrong country enters a
# database and never leaves. The raw string is never lost -- it stays in `location`.
_COUNTRY_CODES = {
    "united states": "US", "united states of america": "US", "usa": "US", "u.s.": "US",
    "united kingdom": "GB", "uk": "GB", "great britain": "GB", "england": "GB",
    "canada": "CA", "mexico": "MX", "brazil": "BR", "argentina": "AR", "chile": "CL",
    "colombia": "CO", "peru": "PE", "ireland": "IE", "france": "FR", "germany": "DE",
    "spain": "ES", "portugal": "PT", "italy": "IT", "netherlands": "NL",
    "belgium": "BE", "switzerland": "CH", "austria": "AT", "sweden": "SE",
    "norway": "NO", "denmark": "DK", "finland": "FI", "poland": "PL",
    "czech republic": "CZ", "czechia": "CZ", "romania": "RO", "ukraine": "UA",
    "greece": "GR", "turkey": "TR", "israel": "IL", "united arab emirates": "AE",
    "saudi arabia": "SA", "south africa": "ZA", "nigeria": "NG", "kenya": "KE",
    "egypt": "EG", "india": "IN", "pakistan": "PK", "china": "CN", "japan": "JP",
    "south korea": "KR", "korea, republic of": "KR", "singapore": "SG",
    "hong kong": "HK", "taiwan": "TW", "malaysia": "MY", "indonesia": "ID",
    "thailand": "TH", "vietnam": "VN", "philippines": "PH", "australia": "AU",
    "new zealand": "NZ", "kazakhstan": "KZ",
    # Added 2026-08-13 by measurement, not by working through an ISO list. Both were
    # absent while appearing in a live harvest: `bulgaria` was in the old hand-written
    # non-US filter but NOT here, so deriving that filter from this map (see
    # NON_US_LOCATION_TOKENS) initially let 23 Bulgarian rows through -- and
    # country_code("Bulgaria") had been returning None all along. `dominican republic`
    # leaked 18 rows the same way.
    #
    # This map is deliberately INCOMPLETE and grows on evidence. Two consequences worth
    # knowing: a country absent here is a foreign row the non-US filter cannot see, and
    # "georgia" must NEVER be added -- it is a US state as well as a country, and
    # split_place's bare-name lookup is guarded on exactly that collision.
    "bulgaria": "BG", "dominican republic": "DO",
}  # fmt: skip


def country_code(raw) -> str | None:
    """A country name OR an alpha-2 code -> alpha-2, or None when unrecognised.

    An already-valid-looking two-letter code passes through uppercased without being
    checked against a list: sources that send codes send real ones, and rejecting an
    unlisted-but-valid code would discard data to enforce a list this module has no
    business being the authority on.

    The NAME MAP IS CONSULTED FIRST, because the passthrough was answering for tokens
    the map already had a better answer for. "UK" is the case: it is not an ISO alpha-2
    code (GB is), the map has said `uk -> GB` all along, and the passthrough returned
    "UK" anyway -- so one column the record contract declares alpha-2 held both spellings
    for the same country. (The harvest measured here stores no non-US country codes, so
    the row count for this one is unverified -- the BEHAVIOUR is what was tested.) Same
    defect as the
    `state='California'` vs `'CA'` split that engine._coerce canonicalizes, and it hid
    here because the passthrough looks like it only handles codes the map lacks.
    """
    s = _flatten(raw).strip()
    if not s:
        return None
    # Aliases the map knows win over the passthrough; a genuinely unlisted code still
    # rides through unvalidated, which is the behaviour the docstring above defends.
    known = _COUNTRY_CODES.get(s.lower())
    if known:
        return known
    if len(s) == 2 and s.isalpha():
        return s.upper()
    return None


# The 50 states + DC + the territories that appear in job feeds. A two-letter token
# after a comma is only a US state if it IS one -- "Taiwan, TW" would otherwise be
# read as a city in the state of TW.
_US_STATES = frozenset(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO "
    "MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY "
    "DC PR VI GU AS MP".split()
)

# The alpha-2 codes this module can actually vouch for -- derived from the country
# NAME table above so there is one source, not two lists to drift apart.
#
# `country_code()` deliberately passes any two letters through unvalidated, which is
# right for a source's dedicated country field. It is wrong as a gate: it would call
# "WA" the country Washington. This set is the gate, and the seven codes that are BOTH
# a US state and a country -- AR CA CO DE ID IL IN -- are exactly why split_place needs
# structural evidence before reading a two-letter token as a country.
_KNOWN_COUNTRIES = frozenset(_COUNTRY_CODES.values())

# Every country NAME above that is not the US -- the one source for a "this posting is
# not US-workable" filter. config.DEFAULT_NON_US was a SEPARATE hand-written list of 34
# names against these 58, and the ones it lacked leaked measurably: on a 31,790-row harvest
# 260 remote rows bounded to a country the user cannot work in got through, led by the
# Philippines (68), the UK spellings (44), South Africa, Pakistan, Thailand, China, Chile
# and South Korea. Same failure and same fix as _KNOWN_COUNTRIES above -- derive it, so
# adding a country to the map is sufficient and there is no second list to go stale.
#
# NAMES, not codes: the filter matches prose ("Remote - Philippines"), and two-letter
# codes are far too collision-prone for that even with word boundaries ("in", "us").
NON_US_COUNTRY_NAMES = tuple(
    sorted(name for name, code in _COUNTRY_CODES.items() if code != "US")
)

# Multi-country regions, blocs and hub cities -- the non-US markers that are NOT country
# names, so they cannot be derived and are enumerated. `european` sits beside `europe`
# deliberately: matching is word-bounded (scoring._excluded_re), so "European Union" is
# unreachable from "europe" alone.
# The continents remotive sends. "Americas, Europe, Asia, Africa, Oceania" is its canonical
# five-continent value -- the worked example in catalog/remotive.md -- and without these,
# Asia/Africa/Oceania were silently dropped on 11% of a live feed, making a five-continent
# role invisible to a searcher on three of them.
#
# ONE CONSTANT, SPLICED INTO BOTH TUPLES, because the alternative was tried and failed
# inside a single release: the same four names and an identical five-line comment were
# pasted into `_NON_US_REGIONS` and `_REMOTE_REGIONS`, which put `northern america` -- a
# region containing the US -- into the non-US exclusion list. The next continent someone
# adds goes in one tuple and not the other, which is the drift this file keeps recording
# elsewhere (`_NON_PLACE`, `ats_from_url`, the single source registry). `americas` is
# absent on purpose: it is US-inclusive, and the two tuples would disagree about it.
_CONTINENTS = ("asia", "africa", "oceania")

_NON_US_REGIONS = (
    "emea",
    "apac",
    "asia-pacific",
    "asia pacific",
    "latam",
    # Continents, shared with `_REMOTE_REGIONS` -- see `_CONTINENTS`. They sit beside the
    # `europe` / `apac` / `latam` already in this tuple, so the non-US markers are
    # consistent rather than arbitrary: "Europe Program Director" was always dropped by the
    # default exclusion and "Asia Program Director" was not, for no reason anyone chose.
    #
    # `northern america` is DELIBERATELY ABSENT, and that is why these come from a shared
    # constant instead of a hand-copied list: for one release they WERE hand-copied, and it
    # put that name here. It is the UN M49 region CONTAINING the United States, so it
    # dropped US-workable roles -- while the synonym `north america`, never in this list,
    # passed. Opposite verdicts on the same place, from the default config.
    *_CONTINENTS,
    "europe",
    "european",
    "(eu)",
    "dubai",
    "uae",
)

# The default `exclude_locations`. It LIVES HERE, not in config, because config <- vocab
# <- dedup <- config is a real import cycle that survives only while every participant
# binds the module and reads attributes at CALL time. Building this list in config meant a
# module-level `vocab.NON_US_COUNTRY_NAMES` read, which fails outright when vocab is
# imported first -- caught by importing vocab before config, not by reasoning about it.
NON_US_LOCATION_TOKENS = (*NON_US_COUNTRY_NAMES, *_NON_US_REGIONS)


_STATE_NAMES = {
    "alabama": "AL",
    "alaska": "AK",
    "arizona": "AZ",
    "arkansas": "AR",
    "california": "CA",
    "colorado": "CO",
    "connecticut": "CT",
    "delaware": "DE",
    "florida": "FL",
    "georgia": "GA",
    "hawaii": "HI",
    "idaho": "ID",
    "illinois": "IL",
    "indiana": "IN",
    "iowa": "IA",
    "kansas": "KS",
    "kentucky": "KY",
    "louisiana": "LA",
    "maine": "ME",
    "maryland": "MD",
    "massachusetts": "MA",
    "michigan": "MI",
    "minnesota": "MN",
    "mississippi": "MS",
    "missouri": "MO",
    "montana": "MT",
    "nebraska": "NE",
    "nevada": "NV",
    "new hampshire": "NH",
    "new jersey": "NJ",
    "new mexico": "NM",
    "new york": "NY",
    "north carolina": "NC",
    "north dakota": "ND",
    "ohio": "OH",
    "oklahoma": "OK",
    "oregon": "OR",
    "pennsylvania": "PA",
    "rhode island": "RI",
    "south carolina": "SC",
    "south dakota": "SD",
    "tennessee": "TN",
    "texas": "TX",
    "utah": "UT",
    "vermont": "VT",
    "virginia": "VA",
    "washington": "WA",
    "west virginia": "WV",
    "wisconsin": "WI",
    "wyoming": "WY",
    "district of columbia": "DC",
    "puerto rico": "PR",
    "guam": "GU",
    "virgin islands": "VI",
    "american samoa": "AS",
}


def us_state_code(raw) -> str | None:
    """A US state name OR code -> the two-letter code, else None.

    USAJOBS sends `CountrySubDivisionCode` as a full NAME ("Louisiana") despite the
    key saying Code, while every other adapter sends "LA". Without this the `state`
    column holds two vocabularies and grouping by it splits every state in two.
    """
    s = _flatten(raw).strip()
    if not s:
        return None
    if len(s) == 2 and s.upper() in _US_STATES:
        return s.upper()
    return _STATE_NAMES.get(s.lower())


# A head token that names a WORK ARRANGEMENT, not a place. Every branch of
# split_place() assigned `city` from the head without ever testing that the head was
# a place, so the arrangement became a city: "Remote, France" -> city "Remote",
# "Anywhere, Canada" -> "Anywhere", "Hybrid, Germany" -> "Hybrid", "WFH, India" ->
# "WFH", "Remote, TX" -> "Remote". That is the permanently-wrong row this module's
# docstring refuses to create, and it is why a two-letter-tail rule was rejected:
# accepting ", US" would have written a city named Remote onto ~295 measured rows.
#
# DERIVED from dedup._QUAL_NOISE (already imported above) rather than restating its
# eight words, so the shared vocabulary has one source. Two lists of the same words
# is the drift this package has been bitten by three times -- three URL parsers, two
# remote-vs-place tuples. The coupling is deliberate and its failure direction is
# benign: dropping a word from _QUAL_NOISE lets a bad city survive, it can never
# invent a place. sources._NON_PLACE stays separate on purpose -- it is a predicate
# over a whole CONFIG string for the keyed search APIs, not a head token.
_PLACELESS = _QUAL_NOISE | {
    "work from home",
    "work-from-home",
    "virtual",
    "distributed",
    "home based",
    "home-based",
}

# Intensifiers that decorate an arrangement without making it a place.
_PLACELESS_INTENSIFIERS = ("fully", "100%", "completely", "permanently", "partially")


def _head_is_a_list(head: str) -> bool:
    """True when a location head enumerates COUNTRIES rather than naming a city.

    "Australia, Canada, Germany, United Kingdom" is a list of eligible countries that
    `rpartition` leaves in the head slot, and writing it as `city` is the permanently-wrong
    row this function's own docstring refuses to create. 99 measured locations.

    The test is TWO OR MORE distinct country names, deliberately -- not "contains a comma".
    A comma test would null "Austin, Texas" and "New York, New York", which are ordinary
    city+state heads: 7,785 locations in a 31,790-row harvest have a comma inside the head,
    against the 99 that are genuinely country lists. Refusing those would trade 99 wrong
    values for thousands of right ones, which is the wrong direction and is why this is
    measured rather than reasoned.
    """
    return len({m.group(0) for m in _COUNTRY_NAME_RE.finditer(head.lower())}) >= 2


def _placeless_head(head: str) -> bool:
    """True when a location string's head names an arrangement instead of a place.

    UNDECORATED matches only. A trailing qualifier -- "Remote - US",
    "Remote (EMEA)" -- keeps its bad city, a bounded residual rather than a bug to
    widen carelessly: a whole-head pattern loose enough to catch those also nulls
    the real city in "Hybrid - Austin", and which way that trades needs a corpus.
    Under-fixing leaves a wrong value already in the column; over-fixing destroys a
    right one, so the narrow rule is the safe one until it can be measured.
    """
    h = " ".join(head.lower().split())
    first, _, rest = h.partition(" ")
    if first in _PLACELESS_INTENSIFIERS and rest:
        h = rest
    return h in _PLACELESS


def split_place(raw) -> dict:
    """A free-text location -> {city, state, country}, or None where it cannot be
    read with confidence.

    Deliberately narrow. It resolves exactly two shapes it can be sure of --
    "Waco, TX" (a real US state code) and "London, United Kingdom" (a country this
    module recognises) -- and returns None for everything else rather than guessing.

    The tempting version splits on the comma and calls the second half a state, which
    turns "Taiwan, Taipei" into the city of Taiwan in the state of Taipei. Several
    sources emit COUNTRY-first order, so comma position carries no reliable meaning.
    A None here costs a filter; a wrong city is a permanently wrong row.
    """
    s = _flatten(raw).strip()
    empty = {"city": None, "state": None, "country": None}
    if not s:
        return empty
    if "," not in s:
        # A whole string that IS a country name is not a guess -- it is the lookup
        # country_code() already performs, and the comma guard was returning before
        # ever asking. Measured downstream on a 31,790-row harvest: 1,591 rows are
        # exactly this shape, and 539 of them are the literal "United States", so
        # this was discarding US identification as well as foreign.
        #
        # Gated on the NAME map rather than country_code(), whose two-letter
        # passthrough would turn a bare "CA" or "ON" into a country -- the exact
        # failure the two-letter-tail refusal below exists to prevent.
        #
        # The _STATE_NAMES exclusion guards an invariant this lookup depends on and
        # nothing else enforces: no key in _COUNTRY_CODES is also a US state name.
        # That holds today, but the map is hand-curated and missing Georgia, Jordan
        # and Chad -- so without this, adding "georgia": "GE" for some unrelated
        # source would silently make the US state a foreign country. Only `country`
        # is filled; no city or state is invented from a single token.
        if s.lower() in _STATE_NAMES:
            return empty
        code = _COUNTRY_CODES.get(s.lower())
        return {"city": None, "state": None, "country": code} if code else empty
    head, _, tail = s.rpartition(",")
    head, tail = head.strip(), tail.strip()
    if not head or not tail:
        return empty
    # THREE parts settle the ambiguity the two-part case cannot. "Toronto, ON, CA"
    # is Canada, not California: the region slot is already occupied by ON, so a
    # two-letter token in position 3 is a country code. Measured on a 21,495-row
    # harvest, reading position 3 as a state made 25 foreign rows American --
    # "Toronto, ON, CA" (17), and it left 52 more with no geography at all because
    # the tail was a country code that is not also a US state ("Curitiba, PR, br").
    #
    # Gated on _KNOWN_COUNTRIES, never on country_code(), so "San Francisco, CA,
    # Seattle, WA" -- a multi-location string, not City/Region/Country -- falls
    # through to the two-part logic unchanged rather than inventing the country WA.
    #
    # Restricted to strings with ONE place in them. Several sources pack multiple
    # locations into one field ("New York, NY; San Francisco, CA"), where the trailing
    # two letters are the last city's STATE, not a country. Without this guard the
    # rule turned 519 such rows Canadian to fix 25 -- caught by re-running the old and
    # new functions over the same 21,495 captured strings, not by reasoning.
    if (
        ";" not in s
        and "|" not in s
        and "," in head
        and len(tail) == 2
        and tail.upper() in _KNOWN_COUNTRIES
    ):
        city, _, region = head.rpartition(",")
        city, region = city.strip(), region.strip()
        if city:
            country = tail.upper()
            # `state` stays US-only by construction; a non-US subdivision has no
            # canonical form here and a raw one would pollute the column.
            state = (
                region.upper()
                if country == "US" and region.upper() in _US_STATES
                else None
            )
            # RETURNS EVEN WHEN THE CITY IS DROPPED -- never fall through. "CA" is
            # in both _US_STATES and _KNOWN_COUNTRIES, so a nulled city that fell
            # out of this branch would hit the US-state test below and read
            # "Remote, ON, CA" as state CA / country US. That is exactly the 25
            # foreign-rows-made-American regression this branch was written to
            # prevent, so the country decided here is final.
            return {
                "city": None
                if _placeless_head(city) or _head_is_a_list(city)
                else city,
                "state": state,
                "country": country,
            }
    if tail.upper() in _US_STATES:
        # The state is real whether or not the head names a city, so a placeless
        # head loses only the city -- "Remote, TX" is a Texas row with no city.
        return {
            "city": None if _placeless_head(head) or _head_is_a_list(head) else head,
            "state": tail.upper(),
            "country": "US",
        }
    # A NAME only, never a bare two-letter tail. country_code() passes any two
    # letters through, which is right for a source's dedicated country field but
    # wrong here: "Toronto, ON" would resolve to the country "ON". A province and a
    # country code are indistinguishable at two characters, so this gives up
    # "Paris, FR" to avoid inventing a country for every Canadian city.
    code = country_code(tail) if len(tail) > 2 else None
    if code:
        # Same as above: the country the string names survives, the invented city
        # does not -- "Remote, France" is a French row with no city.
        return {
            "city": None if _placeless_head(head) or _head_is_a_list(head) else head,
            "state": None,
            "country": code,
        }
    return empty


# Multi-country regions and timezone bands a remote role can be bounded to. These are NOT
# countries and have no alpha-2, so `remote_regions` carries them verbatim-uppercased --
# the same two-vocabularies-in-one-column shape as `state`, and named here rather than
# silently decided. Measured: 338 of 7,712 remote-by-location rows are bounded this way.
_REMOTE_REGIONS = (
    "north america",
    # South/Latin/Central America are listed so they are SCOPED rather than merely not-US.
    # They were previously answered "US" outright, because US_LOCATION_RE carried a bare
    # `america`; naming them here means the region pass claims them before that check ever
    # runs, and a US-only filter excludes them because they are not US-inclusive.
    "south america",
    "latin america",
    "central america",
    "latam",
    *_CONTINENTS,
    # Only the SCOPE vocabulary gets this one. remotive really sends it, and as a stated
    # eligibility boundary it is real information -- but as a non-US marker it is false,
    # so it stays out of the shared constant. See `_CONTINENTS`.
    "northern america",
    "emea",
    "apac",
    "asia-pacific",
    "asia pacific",
    "americas",
    "europe",
    "european union",
)

# "Remote from anywhere" -- the ONLY thing that means unbounded. Measured on the same
# harvest: just 30 of 7,712 remote-by-location rows actually say this. A bare "Remote"
# does NOT --
# it means "remote, boundary unstated", usually within whatever country the employer can
# legally pay from. Collapsing unstated into ANY is the same error as reading a blank
# country as "placeless, therefore servable".
_REMOTE_ANYWHERE = re.compile(
    r"(?<![a-z])(?:anywhere|world[- ]?wide|globally|global|any location)(?![a-z])", re.I
)

_REMOTE_TZ = re.compile(r"(?<![a-z])(?:utc|gmt|est|pst|cst|cet)(?![a-z])", re.I)


def _alternation(words, guard_new: bool = True) -> re.Pattern:
    """One word-bounded alternation, longest name first.

    Replaces a per-call loop that rebuilt `rf"(?<![a-z]){re.escape(name)}(?![a-z])"` for
    each of 62 country names and re-sorted them on every invocation -- 71 pattern
    constructions per row. Compilation itself amortized through re's internal cache, but
    the escaping, sorting and dispatch did not: measured 49.6 us/call against 2.7 us for
    this, a 14.8x saving on the line item.

    `guard_new=False` FOR THE STATE MAP, and it is not a style knob -- it is the difference
    between a correct answer and the worst wrong one this module can give. See below.
    """
    # `(?<!new )` because "New Mexico" is a US state and the plain lookbehind accepts a
    # space -- `_longest_match` hid this (united states, 13 chars, beat mexico, 6), so
    # collecting ALL matches surfaced it: 63 corpus rows became (['MX','US']). Multi-word
    # names starting with "new" are unaffected: "new zealand" matches from its own start.
    #
    # THAT GUARD IS RIGHT FOR COUNTRIES AND WRONG FOR STATES, which is why it is optional.
    # The state scan it replaced never had it, and inheriting it suppressed a state name
    # after the literal word "new" -- so "Anywhere in New Washington" (a real town, as is
    # New Virginia, Iowa) lost the guard that was the only thing standing between it and
    # `_REMOTE_ANYWHERE`, and came back `[]`: STATED-WORLDWIDE for a posting that named a
    # place. An empty list satisfies every `allowed_scopes` policy, so that is the one
    # direction this contract exists never to be wrong in. 162 of 500 generated
    # "new <state>" strings changed emptiness; three changed the function's real answer.
    prefix = r"(?<![a-z])(?<!new )(?:" if guard_new else r"(?<![a-z])(?:"
    return re.compile(
        prefix
        + "|".join(re.escape(w) for w in sorted(words, key=len, reverse=True))
        + r")(?![a-z])"
    )


_REGION_RE = _alternation(_REMOTE_REGIONS)
_COUNTRY_NAME_RE = _alternation(_COUNTRY_CODES)
# The third loop, hoisted last and for the same reason as the first two. `remote_scope` was
# rebuilding `rf"(?<![a-z]){re.escape(n)}(?![a-z])"` for all 50 state names on EVERY posting
# -- 1.1M `re.escape` and `re.search` calls over a 20k-row profile, about 75% of the
# function's runtime.
#
# THE SET IT BUILDS IS NOT ALWAYS THE SAME SET, and that is the part to read before touching
# this. The old loop searched each name INDEPENDENTLY, so "Charleston, West Virginia" matched
# both "west virginia" and "virginia" and produced {WV, VA}. Longest-first `finditer` does not
# overlap, so it produces {WV} alone -- a real difference on 520 of 5,720 generated strings.
# That pair is the only containment relationship in `_STATE_NAMES`.
#
# It is inert because `guards` is read exactly once, as `if guards:` below -- truthiness, never
# membership -- so dropping a redundant member cannot change an answer. IF YOU EVER READ
# `guards` FOR ITS CONTENTS, that stops being true.
#
# EMPTINESS is the property that must be preserved, and the first version of this hoist broke
# it by inheriting `_alternation`'s `(?<!new )` -- hence `guard_new=False`, whose reasoning
# lives on `_alternation` itself. Verified rather than argued: identical output on 5,065
# strings A/B'd against released v0.8.1, including every "new <state>" and containment form.
_STATE_NAME_RE = _alternation(_STATE_NAMES, guard_new=False)


def _longest_match(rx: re.Pattern, low: str) -> str | None:
    """The LONGEST match anywhere in the string, not the leftmost.

    Preserves the semantics of the loop this replaced, which tried the longest name first
    and so preferred "united kingdom" over a "uk" appearing earlier. A plain alternation
    is leftmost-wins and would silently change that on multi-place strings.
    """
    best = max(rx.finditer(low), key=lambda m: len(m.group(0)), default=None)
    return best.group(0) if best else None


# The US, spelled every way a job feed spells it. Lives here so `remote_scope` and
# `scoring._location_excluded` share ONE pattern instead of two that drift.
#
# `us` is the reason this exists rather than a _COUNTRY_CODES lookup: the name map carries
# "united states", "usa" and "u.s." but NOT the bare "us", so hundreds of rows went
# unrecognised -- "Remote - US" (227), "Remote US" (119), "Remote, US" (105), "US Remote"
# (55), "US - Remote" (35). It is excluded from the map on purpose: as a country NAME, a
# bare "us" is the English pronoun and far too collision-prone for prose matching.
# `america` is NOT a member, and leaving it in was a real bug: it made
# remote_scope("South America") and ("Latin America") answer "US", and because
# scoring._location_excluded reads this same pattern as its US veto, a posting reading
# "Argentina, Chile, Colombia, Mexico, South America (Remote)" survived a US-only search.
# 44 rows in a 31,790-row harvest carry South/Latin/Central America. "United States of
# America" still matches in full, and "North America" is answered by the region pass that
# runs before this one.
US_LOCATION_RE = re.compile(
    r"(?<![a-z])(?:u\.?s\.?a?|us|united states(?: of america)?)(?![a-z])", re.I
)

# Strip the arrangement words so what remains can go through the ordinary place parser
# rather than a second address grammar living here. "Atlanta, GA - Remote" -> "Atlanta,
# GA", which split_place already reads as Georgia/US.
_REMOTE_STRIP = re.compile(
    r"(?<![a-z])(?:remote|hybrid|onsite|on-site|wfh|work from home|telecommute)(?![a-z])"
    r"|\(\s*\)|\[\s*\]",
    re.I,
)


def strip_arrangement(s: str) -> str:
    """Remove the work-arrangement words and any brackets they leave behind.

    RUNS TO A FIXED POINT, and a single pass is not enough: `re.sub` scans once, so the
    `\\(\\s*\\)` alternative never revisits parentheses that the SAME pass just emptied.
    `(Hybrid) Mansfield, MA` became `( ) Mansfield, MA`, and `split_place` then read the
    city as `( ) Mansfield` -- a plausible-looking wrong value written into a data field,
    which is the exact class of error the record contract exists to prevent. One row in a
    31,790-row harvest, but that harvest is 78% one source and the shape is common
    elsewhere; the cost of being wrong here is a permanently bad city.

    Bounded rather than `while True`: this converges in two passes on every real input, and
    an unbounded loop over user-supplied text is a hang waiting for a pathological string.
    """
    for _ in range(4):
        out = _REMOTE_STRIP.sub(" ", s)
        if out == s:
            break
        s = out
    return " ".join(s.split()).strip(" ,-–—|/")


def remote_scope(raw) -> tuple[list[str] | None, list[str] | None]:
    """A location string -> `(areas, regions)`, the STATED remote eligibility boundary.

    `areas` are ISO codes -- alpha-2 for a country, ISO 3166-2 for a US state. `regions` are
    multi-country tokens from `REMOTE_REGION_TOKENS` and NEVER a country code. Keeping them
    apart is the whole point: one column holding codes, subdivisions, region names and
    sentinels at once is a documented failure mode, and it forced a US-inclusive special case
    that only existed because a SET was being stored as a STRING.

    THREE STATES, and they are distinct on purpose:
      (None, None)   nothing was stated
      ([],   None)   stated UNBOUNDED -- the posting really says "anywhere"
      (["US"], None) enumerated

    STATED ONLY. A boundary inferred from an office city is not a boundary: measured on 73
    US-subdivision rows, 58 came from a city head ("Boston, MA / Remote" is where the office
    is, not where you must live) and only 14 stated one with no city at all ("Remote - TX").
    The same is true of countries -- "Sao Paulo, Brazil - Remote" infers BR from an address --
    so the rule is stated-vs-inferred, not subdivision-vs-country, or it is a carve-out rather
    than a principle. The office anchor keeps living in `city`/`state`/`country`.

    ALL matches, not the longest. "Remote - US & Canada" is two countries, and 13 measured
    rows name several US states; a scalar picked one arbitrarily and dropped the rest.
    """
    s = _flatten(raw).strip()
    if not s:
        return (None, None)
    low = " ".join(s.lower().split())

    # A REGION SITTING INSIDE A COUNTRY NAME IS NOT A STATED REGION. "South Africa" contains
    # "africa", so adding the continents made ZA the one country in 425 that also carried a
    # continent tag -- while France got no EUROPE. The tag is not FALSE (South Africa is in
    # Africa) but it is inferred, and this docstring's rule is STATED ONLY: a continent
    # deduced from a country is the same inference as a country deduced from an office city.
    # It is also arbitrary in a way that shows downstream, since `_region_allowed` uses
    # regions to ADMIT: `allowed_scopes: [AFRICA]` would keep South Africa while
    # `[EUROPE]` dropped France.
    #
    # Masked by SPAN rather than guarded by a `(?<!south )` lookbehind, because the
    # lookbehind fixes this one name and leaves the class open -- the same "harmless by
    # coincidence" reasoning that `(?<!new )` already rests on. If continent-from-country is
    # ever wanted, it belongs in `scoring._region_allowed`, where resolving a region is
    # policy owned by the caller who chose the filter, exactly as it says.
    country_spans = [m.span() for m in _COUNTRY_NAME_RE.finditer(low)]
    regions = sorted(
        {
            m.group(0).upper()
            for m in _REGION_RE.finditer(low)
            if not any(a <= m.start() and m.end() <= b for a, b in country_spans)
        }
        | ({"TIMEZONE"} if _REMOTE_TZ.search(s) else set())
    )

    # THE CITY TEST IS THE STATED-VS-INFERRED GATE, and it governs AREAS ONLY.
    #
    # A country found in a string that also has a city head came from an office address,
    # not a boundary: "Munich, Germany" and "Costa Mesa, California, United States" are
    # where the desk is. Measured, 6,779 of 15,371 area-carrying rows are this shape, so
    # applying the rule only to US subdivisions -- as the first version did -- made it the
    # carve-out this docstring says it must not be, and on those rows `remote_areas` meant
    # nothing more than `country`.
    #
    # Regions are NOT suppressed by a city, because a region name is never an office
    # address: "Bengaluru, Karnataka, India, APAC" states APAC while sitting on a city.
    bare = strip_arrangement(s)
    place = split_place(bare)
    has_city = bool(place["city"])

    # Scanned on the WHOLE string, regardless of has_city, because it answers a second
    # question: does this string name ANY place at all? That is what guards the unbounded
    # fallback below, and gating it on `areas` alone was wrong -- `areas` is also empty when
    # the country pass was SKIPPED by has_city, so "Anywhere in France, Belgium, Spain"
    # (11 rows) and "Any location, United States" (12) claimed stated-worldwide while
    # naming three countries and one. Asserting a posting is open to the world when it
    # named a bound is the worst direction this field can be wrong in.
    named = {_COUNTRY_CODES[m.group(0)] for m in _COUNTRY_NAME_RE.finditer(low)}
    if US_LOCATION_RE.search(s):
        named.add("US")
    # Spelled-out US state names GUARD the unbounded fallback but never become an area on
    # their own. A bare state name cannot be told from an office city -- "Anywhere in Texas"
    # states a bound while "New York (Remote)" is where the desk is, and both are just a
    # state name to a scanner. So a state name is enough to refuse "worldwide" and not
    # enough to assert a boundary: "Anywhere in Texas" comes out unstated (1 measured row),
    # which is the honest answer when the alternative is calling New York a restriction.
    guards = named | {_STATE_NAMES[m.group(0)] for m in _STATE_NAME_RE.finditer(low)}

    areas: set[str] = set()
    if not has_city:
        areas = set(named)
        # A bare US state with no city -- "Remote - TX". `US-TX`, not `TX`: seven codes are
        # both a US state and an ISO country (AR CA CO DE ID IL IN), so a bare code left the
        # column undecidable between California and Canada.
        if len(bare) == 2 and bare.upper() in _US_STATES:
            areas.add(f"US-{bare.upper()}")
        elif place["state"]:
            areas.add(f"US-{place['state']}")

    # BOTH are returned when both are present. An early return on regions discarded every
    # country in the string on 111 measured rows -- "Americas (USA or Canada) (Remote)" lost
    # US and CA -- which defeats the point of having built two fields.
    if areas or regions:
        return (sorted(areas) if areas else None, regions or None)

    # "Anywhere" is only UNBOUNDED when the string names no place at all. "Remote - Anywhere
    # in Brazil" means anywhere WITHIN Brazil; a posting that names a place and also says
    # anywhere is bounded, and we report unstated rather than inventing which bound applies.
    if guards:
        return (None, None)
    if _REMOTE_ANYWHERE.search(s):
        return ([], None)

    # Everything else is inferred or unreadable -- a lone city, an office address. Unstated.
    return (None, None)


# The closed token set for `remote_regions`. A multi-country region is NOT a place and has
# no ISO code -- EMEA, APAC and LATAM are business groupings whose membership only the
# employer can settle -- so they get their own field and their own vocabulary rather than
# sharing a column with country codes.
#
# THE INVARIANT: no member is a valid alpha-2 code. That is what makes the two fields
# non-overlapping by construction, and a test asserts it against the FULL ISO set in
# `iso3166`, not against this module's narrow 62-name map -- checking the narrow map is how
# the first version of this passed vacuously while shipping `TZ`, which is Tanzania.
# The timezone sentinel is spelled `TIMEZONE` for exactly that reason.
#
# These deliberately do NOT resolve to country lists. This package's own country map is 62
# names and cannot name 181 of the countries the sources send, so an enumeration would be a
# confidently wrong list in a filterable column -- strictly worse than the token it replaced.
# Whether a region includes the US is a POLICY question, answered in `scoring`, where being
# approximate is honest because the user chose the policy.
REMOTE_REGION_TOKENS = frozenset({r.upper() for r in _REMOTE_REGIONS} | {"TIMEZONE"})

# `remote_areas` is validated by FORMAT, not by membership: alpha-2, or ISO 3166-2 with the
# country prefix. A closed list would have to enumerate every subdivision on earth, and the
# format is what actually carries the guarantee -- a bare two-letter subdivision is
# unrepresentable, which is the collision (California vs Canada) that started this.
REMOTE_AREA_RE = re.compile(r"^[A-Z]{2}(-[A-Z0-9]{1,3})?$")

# The closed vocabulary for `remote_basis`. Kept here beside the other vocabularies
# rather than as a comment, so a value outside it is a one-line diff to spot.
#
# `board` was added 2026-08-05 after a panel review caught six adapters emitting
# `stated` for it. It is the difference between "the vendor's own field on THIS row
# says remote" and "every row on this board is remote because that is what the board
# IS" -- remotive, jobicy, remoteok, himalayas and braintrust are remote-only sites,
# and usajobs was conditioned on OUR OWN query parameter, not on anything the row
# said. The value is right in all six cases; the provenance label was not, and
# collapsing the two is exactly what this field exists to prevent. A consumer
# tightening a remote filter should be able to discount a board-scope inference
# without discarding a vendor's explicit flag.
# `title` was added 2026-08-13 for the same reason `board` was: a real provenance the
# vocabulary could not express. Measured on a 31,790-row harvest, 462 rows say remote in
# the TITLE with the location silent -- 422 greenhouse, 39 lever, 1 ashby, which is to say
# almost entirely the two big depth adapters that send no structured remote field, so the
# title is the ONLY evidence in existence. `remote_posting` already read them and the gate
# already admitted them; nothing recorded it, so they were byte-identical in the emitted
# record to a row nobody classified. `location` would be a lie about where we read it and
# `stated` implies a vendor field, so neither could stand in.
REMOTE_BASES = frozenset({"stated", "board", "location", "title", "text"})

# `seniority_basis` -- same discipline, unchanged vocabulary.
SENIORITY_BASES = frozenset({"stated", "title"})

# `posted_basis` -- see util.posted_from / sources.posted_from_relative.
POSTED_BASES = frozenset({"stated", "relative"})

# `salary_basis` -- `estimated` rides in salary_estimated_* rather than here, so a
# figure in salary_min is always one an employer committed to.
# `parsed` = read out of a vendor's free-text salary string (google_jobs' "47-55 an
# hour"); `stated` = the vendor sent real numeric fields. `estimated` is deliberately
# absent -- a model's guess rides in salary_estimated_* instead, so a figure in
# salary_min is always one an employer committed to.
SALARY_BASES = frozenset({"stated", "parsed"})


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

# Words a numeral QUALIFIES rather than levels: "Tier III", "Level 2", "Grade 4".
# The numeral is real but it is not the role's level, and the antecedent is not
# part of the role name either -- `Tier III Service Desk Engineer` is a Service
# Desk Engineer.
_LEVEL_ANTECEDENT = frozenset({"tier", "level", "grade", "band"})

# A hyphen/dash/pipe/colon acting as a DELIMITER -- whitespace on at least one
# side. See decompose_title for the measurement behind that test.
_DECOR_RE = re.compile(r"\s+[-–—|:]+\s*|\s*[-–—|:]+\s+")

# ── does this word name an OCCUPATION? ───────────────────────────────────────
# MORPHOLOGY FIRST, VOCABULARY SECOND. English names an occupation with an AGENT
# NOUN -- "one who does X" -- and that is a SUFFIX, not a word list: engineER,
# directOR, scientIST, technicIAN, consultANT, superintendENT, millWRIGHT,
# radioLOGIST. A rule written on the suffix generalises to industries nobody
# enumerated; a word list only ever knows the jobs its author thought of.
#
# WHY THAT MATTERS HERE SPECIFICALLY: this corpus is 94 TECH boards. A list curated
# against it silently under-performs on the lane it was not written for. Measured on
# 67,481 PRODUCTION titles [live prod 2026-08-20] -- share carrying a recognisable
# role noun:
#
#     source        list only   + morphology
#     adzuna          94.3%        98.4%     <- the non-tech lane
#     usajobs         90.2%        97.6%     <- the non-tech lane
#     greenhouse      99.2%        99.6%
#     ALL             98.5%        99.4%
#
# The suffix recovers Radiologist, Veterinarian, Underwriter, Millwright and
# Sommelier -- none of which any hand-written list here would have held -- while
# still rejecting Airbag, Ridehailing, Modeling, Guidance and G71.
#
# TWO EXCLUSIONS, both load-bearing: `Senior` ends in -or, and `Director` is a real
# role. Seniority words and title noise are removed BEFORE the suffix test, or
# `Senior, Applied AI Engineer` reads `Senior` as an occupation.
# WHICH SUFFIXES EARN THEIR PLACE, measured on 67,481 production titles rather than
# assumed. The precision of each was read off the words it actually catches:
#
#   -ist   panelist geologist receptionist phlebotomist pathologist radiologist  KEEP
#   -ian   optician veterinarian physician clinician dietitian technician        KEEP
#   -er    practitioner supplier owner provider ... but also customer center     keep
#   -or    contractor vendor creator investor ... but also behavior floor motor  keep
#   -wright millwright                                                           KEEP
#   -ant   restaurant plant participant infant tenant grant want propellant      DROP
#   -ent   development management client equipment content talent engagement     DROP
#
# `-ant` and `-ent` are dropped because they are overwhelmingly ordinary nouns, and
# a FALSE POSITIVE here is not harmless: it suppresses a fall-through that should
# have happened. `Plant 3 - Journeyman Millwright` rooted at `Plant` for exactly
# that reason. The genuine `-ant`/`-ent` occupations (consultant, accountant, agent,
# superintendent) live in the irregulars below, where they cost nothing.
#
# `-er` and `-or` are kept despite similar noise because they carry most of the
# occupational vocabulary in English, and their false positives (`center`, `power`)
# are rarely the ONLY word in a leading segment.
# TWO TIERS OF EVIDENCE, because a suffix is not a single quality of proof.
#
# STRONG suffixes are occupational almost without exception. WEAK ones (-er, -or)
# carry most of English's occupational vocabulary AND most of its ordinary nouns, so
# they are worth reading but not worth trusting on their own. Measured on 67,481
# production titles: words passing by a BARE -er/-or are the only word in a leading
# segment 206 times outside tech against 39 inside it -- 5.3x -- and roughly 80% are
# not occupations at all (`cyber` 115, `owner` 14, `power` 12, `center` 10,
# `career` 7, `worcester`, `prior`).
#
# Treating them as full proof cost 152 MASKED REGRESSIONS: `Call Center - Customer
# Service Agent` rooted at `Call Center`, `Prior Law Enforcement - Court Security
# Officer` at `Prior Law Enforcement`. Masked because the false positive SATISFIES a
# "does the root still name a role" check -- `center` passes it -- so the defect is
# invisible to the obvious metric. Only a two-tier comparison surfaces it.
#
# -man/-woman/-person/-hand/-wife/-mate are here because English builds plenty of
# occupations that way and none of them are common in software: foreman, lineman,
# longshoreman, herdsman, switchman, deckhand, midwife, journeyman, crewmate.
# Their absence is exactly the tech-corpus bias this whole function was rewritten to
# remove.
_STRONG_SUFFIX = ("ist", "ian", "smith", "wright", "keeper", "master", "logist",
                  "man", "woman", "person", "hand", "wife", "mate")  # fmt: skip
_WEAK_SUFFIX = ("er", "or")

#: how strongly a word claims to name an occupation. 0 no · 1 weak · 2 strong.
ROLE_NONE, ROLE_WEAK, ROLE_STRONG = 0, 1, 2


def _role_evidence(word: str) -> int:
    """How strongly does this word claim to name an occupation? See the tiers above.

    PLURALS AND SLASH-COMPOUNDS are read too, because employers write both and a
    suffix test that only knows the singular silently fails on them. Measured on 500
    genuinely non-tech titles [live Adzuna probe, 10 trades, 2026-08-20]: the only
    roots naming no occupation at all were `WELDERS`, `Electricians`,
    `Machinist/Tool & Die` and `Certified Medical Assistant/LPN` -- a plural `s` and
    a `/` that `_WORD_RE` keeps inside the token. Both are morphology, not
    vocabulary, so both are handled here rather than by adding words to a table.
    """
    lw = word.lower()
    # `machinist/tool` -> test each side; a compound counts if any part is a role.
    if "/" in lw:
        # A part that is a RANK contributes nothing: `Senior/Lead Cyber Security -
        # Incident Response Engineer` rooted at the rank compound because `lead` is
        # in the irregulars table, which is consulted before the seniority exclusion.
        # `Machinist/Tool` and `Technician/Electrician` are unaffected.
        return max(
            (
                _role_evidence(part)
                for part in lw.split("/")
                if part and part not in _SENIORITY
            ),
            default=ROLE_NONE,
        )
    # `welders` -> `welder`, `electricians` -> `electrician`. Only when it leaves a
    # real word behind, so `sales` and `operations` are not read as occupations.
    if len(lw) > 4 and lw.endswith("s") and not lw.endswith("ss"):
        singular = lw[:-1]
        if singular in _ROLE_NOUN or (
            len(singular) > 3
            and singular.endswith(_STRONG_SUFFIX)
            and singular not in _SENIORITY
            and singular not in _TITLE_NOISE
        ):
            return ROLE_STRONG
    if lw in _ROLE_NOUN:
        return ROLE_STRONG
    if lw in _SENIORITY or lw in _TITLE_NOISE:
        return ROLE_NONE
    if len(lw) > 3 and lw.endswith(_STRONG_SUFFIX):
        return ROLE_STRONG
    if len(lw) > 3 and lw.endswith(_WEAK_SUFFIX):
        return ROLE_WEAK
    return ROLE_NONE


def _best_role_evidence(words) -> int:
    return max((_role_evidence(w) for w in words), default=ROLE_NONE)


def _is_role_noun(word: str) -> bool:
    """Does this word name an occupation? Suffix first, irregulars second.

    Used for ONE thing: deciding a segment is not a role at all, so `Airbag - Senior
    Developer` does not root at `Airbag`. NOT the same question as "is this root any
    good" -- `Manager` IS an occupation, and `Manager, Payments` correctly roots at
    `Manager`. Falling through there would swap the role for its domain, which is
    strictly worse and was measured before being rejected.
    """
    return _role_evidence(word) != ROLE_NONE


# The IRREGULARS -- occupations English does not build from an agent suffix, so the
# rule above cannot reach them. An EXCEPTION TABLE, not the decider: a name missing
# from it degrades to the suffix test rather than to a wrong answer, which is the
# whole point of the morphology-first order.
_ROLE_NOUN = frozenset(
    {
        # the genuine -ant / -ent occupations, here because those two suffixes are
        # dropped from _AGENT_SUFFIX for being overwhelmingly ordinary nouns
        "consultant",
        "accountant",
        "attendant",
        "superintendent",
        "sergeant",
        "lieutenant",
        "assistant",
        "agent",
        "engineer",
        "engineering",
        "developer",
        "architect",
        "scientist",
        "analyst",
        "manager",
        "director",
        "lead",
        "head",
        "chief",
        "officer",
        "president",
        "vp",
        "designer",
        "researcher",
        "consultant",
        "specialist",
        "advisor",
        "advocate",
        "strategist",
        "administrator",
        "coordinator",
        "associate",
        "assistant",
        "technician",
        "programmer",
        "tester",
        "writer",
        "editor",
        "recruiter",
        "accountant",
        "controller",
        "auditor",
        "attorney",
        "counsel",
        "paralegal",
        "nurse",
        "physician",
        "therapist",
        "pharmacist",
        "technologist",
        "operator",
        "driver",
        "mechanic",
        "electrician",
        "welder",
        "machinist",
        "supervisor",
        "representative",
        "agent",
        "clerk",
        "cashier",
        "server",
        "cook",
        "chef",
        "teacher",
        "instructor",
        "tutor",
        "professor",
        "trainer",
        "coach",
        "intern",
        "apprentice",
        "fellow",
        "partner",
        "principal",
        "staff",
        "generalist",
        "planner",
        "buyer",
        "estimator",
        "inspector",
        "scheduler",
        "dispatcher",
        "artist",
        "animator",
        "producer",
        "marketer",
        "seller",
        "salesperson",
    }
)

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

    # THE DELIMITER DECIDED EVERYTHING, AND ONLY COMMAS WORKED. `_QUAL_RE` has no
    # alternative for a dash, pipe or colon, so decoration after one was never
    # captured -- and worse, the delimiter itself was deleted and the tail welded
    # into the root: `Forward Deployed Engineer - Tokyo` -> `Forward Deployed
    # Engineer Tokyo`, a role name no employer wrote and no search matches.
    # Measured [local 94-board harvest, 0.9.0, 2026-08-20]: comma-only titles 3,262,
    # decoration captured on 99.9%; dash-only titles 1,388, captured on 17.2%. The
    # same decoration, handled two opposite ways, decided by which key the employer
    # happened to press.
    #
    # WHITESPACE ON AT LEAST ONE SIDE is the whole test, and it is measured rather
    # than assumed: 1,605 titles have a spaced hyphen, 132 a spaced pipe, 80 a
    # `word- ` (right space only), 52 an en-dash, 35 a `word: `, 18 an em-dash, 7 a
    # ` -word` (left space only) -- against 222 with an INTRA-word hyphen
    # (`Full-Stack`, `Go-to-Market`) which must never split. Requiring spaces on both
    # sides would miss 122 of them; requiring none would shatter 222.
    #
    # This regex is LOCAL and deliberately not added to `dedup._QUAL_RE`: that table
    # feeds `_marks`, which decides merges, and a wrong merge deletes a job.
    segs = [s.strip(" -–—|:") for s in _DECOR_RE.split(stem)]
    segs = [s for s in segs if s]
    if len(segs) > 1:
        stem = segs[0]
        for tail in segs[1:]:
            quals.extend(
                x.strip().lower() for x in re.split(r"[(),]", tail) if x.strip()
            )

    # EVERY candidate segment, in the order the employer wrote them and in their
    # ORIGINAL case: the delimiter-split pieces, then whatever `_QUAL_RE` lifted out
    # (a trailing comma clause or a parenthetical). The role-noun rule below has to
    # see BOTH, because the two paths produce the same defect. `Airbag - Senior
    # Developer` roots at a product via the dash path; `Ridehailing, Site Reliability
    # Engineer` roots at a domain via the comma path -- and `Senior, Applied AI
    # Engineer` roots at the word `Senior`, which is not a role at all.
    cand = list(segs)
    for m in _QUAL_RE.finditer(t):
        piece = (m.group(1) or m.group(2) or "").strip()
        if piece:
            cand.extend(x.strip() for x in re.split(r"[(),]", piece) if x.strip())
    toks = _WORD_RE.findall(stem)
    # A SEGMENT THAT IS PURE DECORATION IS NOT A ROOT. `REMOTE -Principal Software
    # Developer- Agentic AI` leads with a noise word, and taking segment 0 blindly
    # would root the posting at `REMOTE`. The existing rule was "stripping never
    # empties the root, it falls back to the whole title"; this extends it one step
    # -- try the next segment BEFORE falling back, because a later segment is a real
    # phrase from the same title while the whole title is the unparsed string.
    if segs and not [w for w in toks if w.lower() not in _TITLE_NOISE]:
        for alt in segs[1:]:
            alt_toks = _WORD_RE.findall(alt)
            if [w for w in alt_toks if w.lower() not in _TITLE_NOISE]:
                toks = alt_toks
                break
    # A SEGMENT THAT NAMES NO ROLE IS NOT A ROOT EITHER. Employers put a requisition
    # number, a company, a product or a programme in front of the role:
    # `Airbag - Senior Developer`, `RQ11391 - Solutions Designer`, `G71 - Full Stack
    # Engineer`, `Dynamo AI — Forward Deployed Engineer`. Segment 0 is the DOMAIN and
    # the role is further right -- the mirror of the usual shape, and taking segment
    # 0 roots the posting at a req number. 28 rows [local 94-board harvest, 0.9.0,
    # 2026-08-20], every one of them wrong before this.
    #
    # THE TEST IS "CONTAINS NO ROLE NOUN AT ALL", never "contains a weak one". A
    # `Manager, Payments` posting roots at `Manager` and must keep doing so: falling
    # through on a GENERIC role swaps the role for its domain and is strictly worse.
    # That distinction is why this is safe and why the bare-generic-noun version of
    # the idea was measured, found to invert role and domain, and not built.
    elif len(cand) > 1 and _best_role_evidence(toks) < ROLE_STRONG:
        # STRICTLY BETTER EVIDENCE WINS -- the whole point of the two tiers.
        # `Call Center - Customer Service Agent`: segment 0 offers only `center`
        # (weak, -er) while a later one offers `agent` (strong), so the later one is
        # the role. Requiring "no evidence at all" in segment 0 left 152 production
        # titles rooted at `Call Center` / `Prior Law Enforcement` / `Power System
        # Studies` / `Worcester County South`.
        #
        # A STRONG segment 0 is never overridden: `Manager, Payments` roots at
        # `Manager`, because promoting there would swap the role for its domain --
        # measured, and the reason the bare-generic version of this was rejected.
        #
        # THE BEST CANDIDATE, NOT THE FIRST BETTER ONE. `MA- Worcester County South-
        # RN Case Manager` splits three ways: `MA` (none), `Worcester County South`
        # (weak) and `RN Case Manager` (strong). Taking the first improvement roots
        # it at a COUNTY.
        best: tuple[str, str] | None = None
        best_score = _best_role_evidence(toks)
        for alt in cand[1:]:
            # A candidate lifted out of a comma clause may carry its own decoration
            # (`Forward Deployed Engineering - EMEA`); split it the same way segment 0
            # was, or the tail is welded back into the root.
            alt_head = next(
                (s.strip(" -–—|:") for s in _DECOR_RE.split(alt) if s.strip(" -–—|:")),
                alt,
            )
            score = _best_role_evidence(_WORD_RE.findall(alt_head))
            if score > best_score:
                best, best_score = (alt, alt_head), score
            if score == ROLE_STRONG:
                break  # nothing beats strong; keep the leftmost one that has it
        if best is not None:
            alt, alt_head = best
            # The segment stepped over is decoration, not nothing -- `Airbag` and
            # `Ridehailing` are the role's DOMAIN and belong in `title_qualifiers`.
            # Dropping them silently lost a token on 24 titles.
            skipped = " ".join(toks).strip().lower()
            if skipped and skipped not in quals:
                quals.append(skipped)
            # ...and the segment PROMOTED to root is no longer a qualifier.
            promoted = alt.strip().lower()
            quals = [q for q in quals if q not in (promoted, alt_head.lower())]
            toks = _WORD_RE.findall(alt_head)

    seniority = level = None
    i = 0
    while i < len(toks) - 1 and toks[i].lower() in _SENIORITY:
        if toks[i + 1].lower() == "of":
            break  # head noun -- "Director of Engineering" IS a Director
        if seniority is None:
            seniority = _SENIORITY[toks[i].lower()]
        i += 1

    root_words: list[str] = []
    for j, w in enumerate(toks[i:]):
        lw = w.lower()
        # A LEVEL MARK FOLLOWS THE ROLE; A LEADING NUMERAL IS A COUNT. `2 Full Stack
        # AI Engineer, 1 GTM` is a headcount and read as level II; `Tier III Service
        # Desk Engineer` is a support tier, read as level III, and the root came back
        # mangled to `Tier Service Desk Engineer` with the III removed. Requiring at
        # least one preceding word costs nothing real -- no employer writes `II
        # Engineer` -- and refusing to set a field can never mis-select a value.
        # 4 rows [local 94-board harvest, 0.9.0, 2026-08-20].
        if lw in _LEVEL and level is None and j > 0:
            # `Tier III` / `Level 2` / `Grade 4`: the numeral qualifies the word
            # before it, and that word is not the role either.
            if root_words and root_words[-1].lower() in _LEVEL_ANTECEDENT:
                root_words.pop()
                continue
            level = _LEVEL[lw]
            continue
        if lw in _TITLE_NOISE:
            continue
        root_words.append(w)
    root = " ".join(root_words).strip(" -–—,/")
    # A LEVEL OR A SENIORITY WORD IS NOT A ROLE, and "never empty" was not a strong
    # enough guard. `Staff II, Computer Vision Engineer` -> the comma clause goes to
    # qualifiers, `Staff` is stripped as seniority, and the level guard above
    # deliberately refuses to consume a numeral it cannot place -- leaving `II` as a
    # NON-EMPTY root, so the `root or t` fallback never fired. 4 production rows
    # (`Associate II, CMC Operations`, `Lead II, Category Management`, ...), and the
    # class is a SHAPE -- any `<seniority> <level>, <role>` title reaches it.
    #
    # Introduced by the level-antecedent guard itself: before it, `II` was consumed
    # as a level and the root came back empty, which the fallback did catch.
    # LEVELS ONLY, and the distinction is load-bearing. `_SENIORITY` holds `director`,
    # `vp`, `head`, `chief`, `associate` and `lead` -- every one of them a REAL ROLE as
    # well as a rank. Blanking those sent `Director, Investor Relations` and `VP,
    # Product` back to the whole unparsed title, which is worse than the bare
    # `Director` they had before: caught by hand-reading 150 rows, ~16 of them.
    # A bare `II` is never a role; a bare `Director` is.
    if root_words and all(w.lower() in _LEVEL for w in root_words):
        root = ""

    return {
        "title_root": root or t,  # never empty; falls back to the whole title
        "title_level": level,
        "title_qualifiers": [q for q in quals if q not in _TITLE_NOISE] or None,
        "seniority": seniority,
    }
