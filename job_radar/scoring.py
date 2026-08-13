"""The deterministic fit engine: relevance gate, remote gate, and the weighted
keyword score (BM25 length-normalized). No AI here -- same input, same output,
every time. The optional LLM re-rank (llm.py) layers on top of this."""

from __future__ import annotations

import re
from functools import lru_cache

from . import config, vocab
from .util import has

_TOKEN_RE = re.compile(r"[a-z0-9]+")


@lru_cache(maxsize=None)
def _kw_index(keys: tuple[str, ...]):
    """Split fit-weight keys into SINGLE-token keys (a lone [a-z0-9]+ run — present
    iff the token is in the text's token set, exactly equivalent to a whole-word
    regex) and MULTI-token keys (contain a space/hyphen — still need the regex),
    each multi paired with its first alnum token for a cheap prefilter. Memoized
    per keyword set (the keys are static per config)."""
    singles, multis = set(), []
    for k in keys:
        if _TOKEN_RE.fullmatch(k):
            singles.add(k)
        else:
            toks = _TOKEN_RE.findall(k)
            multis.append((k, toks[0] if toks else ""))
    return frozenset(singles), tuple(multis)


def _present(text: str, tokset: set, fw: dict, singles, multis) -> list:
    """[(weight, keyword)] for every fit-weight keyword present in `text` — exactly
    equivalent to `[(w, kw) for kw, w in fw.items() if has(kw, text)]`, but resolves
    single-token keywords by O(1) set membership and only runs the whole-word regex
    for a multi-token keyword when its first token is present (a prefilter that can
    only skip provably-absent keywords, so the result is unchanged)."""
    hits = [(fw[kw], kw) for kw in singles if kw in tokset]
    hits += [(fw[kw], kw) for kw, first in multis if first in tokset and has(kw, text)]
    return hits


# ── remote detection ────────────────────────────────────────────────────────
# A pure predicate shared with downstream consumers (jobfitr). Title/location
# match liberally; the BODY must hit _REMOTE_BODY_RE AND not be negated, which
# recovers Adzuna/USAJOBS roles that are genuinely remote but only say so in the
# description (their APIs carry no reliable remote flag).
#
# _REMOTE_BODY_RE does NOT establish ROLE-level remoteness, which this comment and
# remote_posting's docstring both used to claim. Two shapes account for the failure:
# employer-level policy read as role-level ("we are a fully remote global company", the
# identical sentence appearing on postings in Mexico, Brazil and Canada at once), and hybrid
# schedules ("3 days of remote work each week" matches the `remote work` branch, though this
# package already has a `hybrid` state for exactly that).
#
# The row counts this comment used to carry -- "of 6,070 rows derived remote from the body
# alone, 1,874 name a real place", split 512 boilerplate / 614 hybrid -- were WRONG and are
# removed rather than patched. 6,070 is a downstream consumer's count of its own `derived`
# rows, and only ~1,455 of them are head-silent: the majority were decided by the LOCATION,
# so "from the body alone" was false for most of the denominator, and no reading of the
# corpus reproduces the three splits. The shapes are real and reproducible; the numbers
# attached to them were not, and a number nobody can re-derive is worse than none.
#
# This alternation is deliberately UNCHANGED, and the fix went somewhere better. Tightening
# it has a real false-negative cost -- "this position allows remote work from anywhere in
# the US" is genuinely remote and dies with the `work` token -- so instead of making a bool
# stricter, `remote_signal()` below answers the same question with a TYPE and a BASIS:
# employer boilerplate and bare mentions become `None` (unknown) rather than True, and a
# split week becomes `hybrid`, which is a state this package already has. That demotes weak
# evidence without discarding it, which a boolean cannot do at any threshold.
#
# WHAT IS STILL TRUE, stated exactly: this alternation continues to govern ADMISSION. The
# engine calls _coerce (which records remote_signal's verdict) before is_remote, so a typed
# row is gated on its type -- but when remote_signal returns unknown, is_remote falls
# through to remote_posting, and employer boilerplate still passes. Such a row is admitted
# while recorded as unclassified, which is incoherent but is the LESS wrong of the two
# options available today: making unknown mean "not remote" would drop the 773 rows that
# merely mention remoteness with no claim, and some of those are genuinely remote. That
# recall cost has never been measured, and this file's rule is that a threshold ships with
# a row count. The gain here is that the ambiguity is now VISIBLE in the record instead of
# being laundered into `remote: True`.
_REMOTE_RE = re.compile(r"remote|anywhere|work from home|\bwfh\b", re.I)
_REMOTE_BODY_RE = re.compile(
    r"\b(?:fully|100%|completely|permanently)\s+remote\b"
    r"|\bremote[- ](?:first|friendly|eligible|position|role|opportunity|work|based)\b"
    r"|\b(?:this|the)\s+(?:is\s+a\s+)?remote\s+(?:position|role|job|opportunity)\b"
    r"|\bwork[- ]from[- ]home\b|\bwork\s+from\s+home\b"
    r"|\btelecommut\w*|\btelework\w*"
    r"|\bremote\s+(?:within|in|across|throughout|anywhere)\b",
    re.I,
)
_REMOTE_NEG_RE = re.compile(
    r"\bno[t]?\s+(?:a\s+)?remote\b|\bno\s+remote\b|\bnon[- ]?remote\b"
    r"|\bon[- ]?site\s+only\b|\bin[- ]office\s+only\b"
    r"|\bnot\s+(?:a\s+)?remote\s+(?:position|role|job)\b",
    re.I,
)


def remote_posting(title: str, location: str, body: str = "") -> bool:
    """True when a posting reads as remote. Title/location match liberally; the body
    must hit _REMOTE_BODY_RE and not be negated. Pure -- no config, safe to share
    with jobfitr's tag derivation.

    The body branch is a REMOTENESS-MENTIONED test, not a role-level one: employer
    policy ("remote-first", "remote-eligible") and split-week schedules ("3 days of
    remote work") both pass. Measured false-positive counts and why the fix is
    deferred are above _REMOTE_BODY_RE. A caller that needs role-level confidence
    should prefer a source's structured `remote_type` -- which is what is_remote()
    does, falling through to this only when the source sent nothing."""
    head = f"{title} {location}"
    if _REMOTE_NEG_RE.search(head):  # "Non-remote ...", "On-site only" -> not remote
        return False
    if _REMOTE_RE.search(head):
        return True
    if body and _REMOTE_BODY_RE.search(body) and not _REMOTE_NEG_RE.search(body):
        return True
    return False


# Body language that asserts THIS ROLE is remote, as opposed to mentioning remoteness.
# Measured over the 1,642 rows where the body actually decides: only 271 make a claim this
# shape. That is the whole gap between `remote_posting` (does the word appear) and
# `remote_signal` (did the posting say so).
_ROLE_REMOTE_RE = re.compile(
    r"\b(?:this|the)\s+(?:position|role|job)\s+(?:is|will\s+be)\s+(?:fully\s+)?remote\b"
    r"|\bthis\s+is\s+a\s+(?:fully\s+)?remote\s+(?:position|role|job|opportunity)\b"
    # `work from anywhere` only. A bare `from anywhere in` was matching COMPANY MISSION
    # COPY, not a remote policy: "reimagine the way people come together, from anywhere in
    # the world" (105 rows, one employer, every one located San Mateo CA) and "work
    # together in real time from anywhere in the world" (47 rows, another). 219 rows were
    # admitted on that phrase alone and at least 152 were marketing prose. They emitted
    # basis='text', identical to a genuine role-level assertion, so a consumer discounting
    # `text` had to throw away the good ones to lose the bad -- the one case where this
    # design's "every row carries a basis you can discount" defence actually failed.
    r"|\bwork\s+from\s+anywhere\b"
    r"|\byou\s+may\s+work\s+remotely\b|\bmay\s+work\s+remotely\s+from\b"
    r"|\b(?:100%|fully)\s+remote\s+(?:position|role|job|opportunity)\b"
    r"|\btelecommut\w*|\btelework\w*",
    re.I,
)

# A split week. Measured: 505 of the 1,642 body-decided rows say something this shape, and
# 346 of those sit on a row whose LOCATION carries a "(Remote)" tag -- so the tag is
# contradicted by the posting's own text on roughly 1 row in 8.
# In a TITLE or LOCATION, a bare `hybrid` IS the work arrangement -- that is what those
# fields are for, and "Palo Alto - Hybrid (Remote)" is the measured 41-row shape. The
# body-side pattern below has to be far stricter, so these are deliberately two patterns
# rather than one shared compromise: a single tightened pattern misses the head case
# (caught by a test, not by reading), and a single loose one calls a hybrid-vehicles JD an
# office job. Sensitivity should follow the field, because the fields carry different prose.
_HYBRID_HEAD_RE = re.compile(r"\bhybrid\b", re.I)

# `hybrid` is NOT matched bare here. It is an ordinary technical adjective and the corpus proves
# it: of 5,491 bodies a bare \bhybrid\b matched, the third-largest group was "hybrid
# vehicles" (194 rows of automotive JDs), with "hybrid environment" close behind, which is
# as often hybrid CLOUD as hybrid work. So the word must sit next to a work-arrangement
# noun, on either side -- "hybrid work model", "hybrid schedule", "hybrid role", and the
# label-style "Work arrangement: Hybrid" that ends a line with no following noun at all.
# `environment` is deliberately absent: under-labelling leaves the row unknown, while
# over-labelling calls a hybrid-cloud engineer's remote job an office job.
_HYBRID_WORDS = (
    r"work(?:ing|place|week)?|schedule|model|policy|role|position|job|setup|arrangement"
)
_HYBRID_RE = re.compile(
    rf"\bhybrid\s+(?:{_HYBRID_WORDS})\b"
    rf"|\b(?:{_HYBRID_WORDS}|type|location)\s*[:=-]?\s*hybrid\b"
    r"|\b\d+\s*(?:-\s*\d+\s*)?days?\s+(?:a|per)?\s*week\s+(?:in|at|from)\s+(?:the\s+)?office\b"
    r"|\b\d+\s*days?\s+in\s+(?:the\s+)?office\b"
    r"|\bdays?\s+in\s+(?:the\s+)?office\b"
    r"|\bin[- ]office\s+\d+\s*days?\b",
    re.I,
)


# The complete set of literals each body pattern CANNOT match without. Deriving these by
# hand is the risk: a first version omitted `anywhere` and silently lost 192 remote
# verdicts, because _ROLE_REMOTE_RE matches "from anywhere in the US" with no other literal
# present. If you edit a pattern above, re-derive its set here -- the equivalence test in
# tests/test_core.py is what catches you if you don't.
#
# The patterns themselves are NOT duplicated case-sensitively. _REMOTE_NEG_RE is shared
# with remote_posting(), whose behaviour is frozen public API, and two copies of one
# alternation is the drift this package has been bitten by three times.
_NEG_LITERALS = ("remote", "site", "office")
_HYBRID_LITERALS = ("hybrid", "office")
_ROLE_LITERALS = ("remote", "anywhere", "telecommut", "telework")


def _has(low: str, literals: tuple) -> bool:
    return any(w in low for w in literals)


def remote_signal(title: str, location: str, body: str = "") -> tuple:
    """A posting's work arrangement WITH its provenance and boundary.

    `(remote_type, remote_basis)`, each `None` when nothing was said.

    The BOUNDARY is not here. It moved to `engine.derive_remote`, because a source can
    state where a worker may sit without saying anything about remoteness -- himalayas
    sends a country restriction on every row and says nothing per-row about arrangement --
    so deriving them together forced one to wait on the other.
    The typed sibling of `remote_posting`, which stays a bare bool forever because it is
    released public API that jobfitr imports.

    Why this exists: `is_remote` called `remote_posting`, got a bool, and wrote NOTHING.
    So a row admitted because its title said "Remote" was byte-identical in the emitted
    record to a row nobody classified -- the 0.7.0 contract's own rule that every derived
    field carries a basis, broken by the gate itself. Measured on a 31,790-row harvest:
    462 rows are decided by the title alone and 1,642 by the body alone, and all 2,104
    came out as `remote_type=None, remote_basis=None`.

    Precedence, and each step is a measurement rather than a preference:

    1. A negation in the location, then in the title -> `onsite`.
    2. An explicit hybrid word in the location, then the title, beats a remote token in the
       SAME string. 41 rows read "Palo Alto - Hybrid (Remote)", and the old order could not
       see it because `_REMOTE_RE` matched and returned first.
    3. The location says remote -> `location`, with the boundary parsed out. If that
       boundary is None the location is weak evidence (a bare token, or an office name
       wearing a "(Remote)" suffix), so a body stating a split week overrides it -- the
       measured "City (Remote)" shape, contradicted by its own text on 1 row in 8.
    4. The title says remote and the location is silent -> `title`. 507 rows end up here.
    5. THE BODY, in this order: a split week -> `hybrid`, THEN an assertion that the role
       is remote -> `remote`. Hybrid first is deliberate: a posting that claims remote AND
       states a split week is hybrid, because the specific schedule beats the general
       claim.
    6. Nothing -> all `None`. Employer boilerplate ("we are a fully remote company") and a
       bare mention with no claim attached land here on purpose -- neither says THIS role
       is remote, and unknown must not become True.

    Steps 1-2 test the two fields SEPARATELY rather than a concatenated head, because the
    basis has to name the field the evidence came from; testing them together reported
    "location" for 106 rows decided by the title.

    (This list is numbered in EXECUTION order. An earlier version put the body's assertion
    branch before its split-week branch, which is the reverse of what the code does, and a
    reviewer found a body that returned step 5's answer while the docstring promised
    step 4's.)
    """
    title, location = title or "", location or ""
    # The basis names the field the evidence CAME FROM, so these test the two fields
    # separately rather than a concatenated head. Testing `f"{title} {location}"` and
    # reporting "location" was a lie about provenance on 106 measured rows whose deciding
    # word was in the title -- exactly the defect this function exists to abolish, and
    # `title` was already a legal basis when it happened.
    if _REMOTE_NEG_RE.search(location):
        return ("onsite", "location")
    if _REMOTE_NEG_RE.search(title):
        return ("onsite", "title")
    # (1) hybrid in the head wins over a remote token beside it
    if _HYBRID_HEAD_RE.search(location):
        return ("hybrid", "location")
    if _HYBRID_HEAD_RE.search(title):
        return ("hybrid", "title")
    # (2) the location is the strongest text evidence and the only one carrying a boundary
    if _REMOTE_RE.search(location):
        areas, regions = vocab.remote_scope(location)
        scope = areas or regions  # did the location state a boundary of ANY kind?
        # A location that yields NO boundary is weak evidence: it is either a bare token
        # or an office name wearing a "(Remote)" suffix, which is the measured 2,887-row
        # "City (Remote)" shape whose body states a split week on 346 of them. A specific
        # factual claim in the prose ("2 days in the office") beats a suffix on a city.
        # A location that DOES name a country or region is a real eligibility statement
        # and stands -- "Remote - Brazil" is not demoted by the word hybrid appearing
        # somewhere in a long description.
        if scope is None and body and _HYBRID_RE.search(body):
            return ("hybrid", "text")
        return ("remote", "location")
    # (3) title-only -- the sole evidence in existence on greenhouse and lever
    if _REMOTE_RE.search(title):
        return ("remote", "title")
    if body:
        # LOWERED ONCE, then every body pattern runs against that copy behind a cheap
        # literal gate. Measured over 203 MB of real bodies (31,790 rows): ungated 16.02s,
        # this 8.70s -- 1.84x, verdicts byte-identical on every row.
        #
        # The .lower() allocates a copy of every job description, which looks like the
        # expensive part and is not. The alternative -- one case-insensitive regex gate,
        # no copy, patterns left on the raw text -- measures 16.44s, NO BETTER THAN NO GATE
        # AT ALL. All of the win is `str.__contains__` on a lowered string being a fast C
        # substring scan that no case-insensitive regex can approach, and the copy is what
        # buys access to it. Measured, because the opposite is the intuitive guess.
        #
        # The gate words are load-bearing and easy to get wrong: a first version omitted
        # `anywhere` and silently lost 192 remote verdicts, because _ROLE_REMOTE_RE matches
        # "from anywhere in the US" with no other literal present. Each set below is the
        # complete set of literals its pattern cannot match without.
        low = body.lower()
        if _has(low, _NEG_LITERALS) and _REMOTE_NEG_RE.search(low):
            return (None, None)
        # (4) before (5): a posting that states a split week is hybrid even when it also
        # asserts remoteness -- the specific schedule beats the general claim.
        if _has(low, _HYBRID_LITERALS) and _HYBRID_RE.search(low):
            return ("hybrid", "text")
        if _has(low, _ROLE_LITERALS) and _ROLE_REMOTE_RE.search(low):
            return ("remote", "text")
    return (None, None)


def relevant(title: str, cfg=None) -> bool:
    cfg = cfg or config.active()
    t = title.lower()
    if any(has(x, t) for x in cfg.exclude_titles):
        return False
    return any(has(x, t) for x in cfg.title_signal)


@lru_cache(maxsize=8)
def _excluded_re(tokens: tuple) -> re.Pattern | None:
    """Compile an exclude_locations list into ONE word-boundary alternation.

    `x in b` was a bare substring test, which is wrong for country names and measurably
    so: on a 31,790-row harvest it dropped 202 rows by collision, and the two worst were
    US employers. "india" matches indianA -- 73 rows including "Anderson, Indiana,
    United States" -- and "apac" matches cAPACity, so a Capacity Planning role in Austin
    was excluded as Asia-Pacific. `brazil`, `japan` and `europe` each cost a handful more.

    A boundary is `(?<![a-z])tok(?![a-z])` rather than `\\b`, because several real tokens
    are not word-shaped: "(eu)" is in the shipped example config and `\\b(eu)\\b` does not
    mean what it looks like next to parentheses. Tokens are regex-escaped for the same
    reason -- these come from a user's YAML, not from us.

    Cached on the token tuple: the list is per-config and stable for a whole run, so this
    compiles once rather than per posting.
    """
    if not tokens:
        return None
    alts = "|".join(re.escape(t.lower()) for t in tokens if t)
    if not alts:
        return None
    return re.compile(rf"(?<![a-z])(?:{alts})(?![a-z])")


# The US-marker pattern comes from vocab, which also uses it in remote_scope -- one
# spelling table, not two. It is matched against the LOCATION only, never the title:
# eligibility is a fact about the place, and titles carry "US" incidentally ("US client",
# "US hours"), which would rescue genuinely foreign rows.
_US_LOCATION_RE = vocab.US_LOCATION_RE


def _location_excluded(location: str, blob: str, tokens: tuple) -> bool:
    """True when a posting should be dropped as not-workable-from-the-US.

    An excluded token is IGNORED when the location also names the US, because a posting
    can list more than one eligible country and dropping it loses a job the user can
    actually take. Measured on a 31,790-row harvest: "Remote (United States | Canada)"
    (32 rows), "Americas (USA or Canada)" (16), "Remote - US & Canada" (7) and several
    multi-city US lists that happen to include Toronto were all being discarded on the
    `canada` token alone. The same shape rescues global-eligibility postings that
    enumerate a dozen countries including the United States.

    This predates the derived token list -- `canada` was in the old hand-written 34 --
    so it is a long-standing false negative that only became visible when the filter was
    measured rather than read.
    """
    rx = _excluded_re(tokens)
    if not (rx and rx.search(blob)):
        return False
    return not _US_LOCATION_RE.search(location)


def is_remote(p: dict, cfg=None) -> bool:
    """The remote GATE (config-aware), as opposed to `remote_posting` (a pure text
    predicate).

    A structured `remote` flag, where the source actually sends one, BEATS reading the
    prose. That ordering is what makes the 0.7.0 contract field load-bearing rather
    than decorative: several sources have always sent a real boolean -- Adzuna's
    `area == ["US"]`, SmartRecruiters' `location.remote`, Arbeitnow's `remote`,
    Ashby's `isRemote` -- and this gate threw every one of them away and re-derived
    remoteness from text that frequently does not mention it.

    The Adzuna case is the sharp one. Its nationwide rows carry the bare location
    string "US", which no text rule can read as remote, so genuinely-remote federal-
    scale postings were dropped by the very filter meant to find them. Mapping the
    signal into `remote` fixes nothing unless the gate reads it.

    `None` still falls through to the text rule -- unknown is not False.
    """
    cfg = cfg or config.active()
    if not cfg.remote_only:
        return True
    # remote_type, not `remote`: the bool is DERIVED from this in engine._coerce and
    # is not populated yet when this gate runs. Reading the enum also makes the gate
    # hybrid-aware for free -- a hybrid role is not remote, and now says so.
    rt = p.get("remote_type")
    flag = None if rt is None else rt == "remote"
    if flag is None:
        if not remote_posting(
            p.get("title", ""), p.get("location", ""), p.get("text", "")
        ):
            return False
    elif not flag:
        return False
    loc = p.get("location", "") or ""
    b = f"{p.get('title', '')} {loc}".lower()
    # THE ROW'S OWN PARSED COUNTRY BEATS A SUBSTRING IN ITS RAW STRING, which is this
    # package's standing rule ("a structured signal beats re-deriving it from prose")
    # applied to the one gate that was still ignoring it.
    #
    # The non-US filter matches country NAMES against raw text, and a good many country
    # names are also real US towns: Turkey TX, Peru IN, Greece NY, China ME, Italy TX,
    # Egypt TX, Norway MI, Poland OH, Denmark SC. For those the pipeline has already done
    # the work correctly one step earlier -- _coerce reads "Turkey, TX" as country=US,
    # state=TX -- and then this gate threw the row away on the word "turkey". Six rows in
    # a 31,790-row harvest, but that harvest is 78% big-city tech; a small-town US harvest
    # is where this bites, and the class was widened by deriving the token list from the
    # full country vocabulary (it already existed for denmark/norway/poland/japan).
    #
    # Only `US` short-circuits, not any parsed country: this filter's whole job is "can a
    # US-based worker take it", so a parsed `country == "FR"` is a reason to drop, not keep.
    if p.get("country") != "US" and _location_excluded(
        loc, b, tuple(cfg.exclude_locations)
    ):
        return False
    return _region_allowed(p, cfg)


# Region scopes that INCLUDE the US, so a US-only searcher keeps them. "Americas" and
# "North America" both contain the United States -- excluding them would drop rows the
# user can take, the same mistake the US veto in _location_excluded fixes.
_US_INCLUSIVE_SCOPES = frozenset({"US", "ANY", "AMERICAS", "NORTH AMERICA"})


def _region_allowed(p: dict, cfg) -> bool:
    """Does this posting's remote BOUNDARY satisfy cfg.remote_regions?

    Separate from the remote gate above on purpose: "is this remote" and "may I sit where
    it is remote from" are different questions, and collapsing them is why a bare `Remote`
    and a `Remote - Brazil` were indistinguishable in a shortlist.

    `None` (the default) means no region filter at all -- every remote row passes, exactly
    as before this existed. An UNSTATED boundary is admitted only when the caller lists
    `UNSTATED`, because unknown is not "anywhere".
    """
    allowed = cfg.remote_regions
    if not allowed:
        return True
    allowed = {str(x).upper() for x in allowed}
    # No hybrid/onsite bypass here: is_remote returns False for those before this is ever
    # reached, so a branch for them would be unreachable and would only mislead a reader.
    areas = p.get("remote_areas")
    regions = p.get("remote_regions")

    if areas is None and regions is None:
        return "UNSTATED" in allowed
    # `[]` is STATED UNBOUNDED -- the posting says anywhere -- so it satisfies any policy.
    # This is why the field distinguishes it from None; collapsing the two would either
    # drop the most permissive rows in the feed or admit the ones nobody classified.
    if areas == []:
        return True

    for a in areas or []:
        a = str(a).upper()
        if a in allowed:
            return True
        # A US STATE IS INSIDE THE US, and the default stays permissive. `remote_regions:
        # [US]` is ambiguous between "the US market" and "somewhere I can sit from my own
        # address", and the token cannot tell them apart -- so `US` accepts `US-TX`, and a
        # user who means the strict reading names the subdivision. Making strict the
        # mandatory reading would be a bigger behaviour change than this release should
        # carry, on 14 measured rows.
        if a.startswith("US-") and a.split("-")[0] in allowed:
            return True
    # A region is resolved HERE, not in the record: whether EMEA or AMERICAS includes the
    # US is policy, and policy belongs to the caller who chose the filter. Storing it in
    # the row was the original mistake.
    return any(
        str(r).upper() in allowed
        or (str(r).upper() in _US_INCLUSIVE_SCOPES and allowed & _US_INCLUSIVE_SCOPES)
        for r in regions or []
    )



def score_and_signals(p: dict, n: int = 7, cfg=None) -> tuple[int, str]:
    """Score a posting AND derive its top signal labels in ONE pass over
    `fit_weights` (each keyword is counted independently, so overlapping keywords
    like 'ai' and 'ai engineer' both contribute). `score()` and `top_signals()`
    are thin wrappers so the public API is unchanged; the engine calls this to
    avoid walking `fit_weights` over the blob twice."""
    cfg = cfg or config.active()
    fw = cfg.fit_weights
    singles, multis = _kw_index(tuple(fw))
    blob = f"{p.get('title', '')} {p.get('location', '')} {p.get('text', '')}".lower()
    blob_tokens = _TOKEN_RE.findall(blob)  # tokenize ONCE — reused for length + hits
    blob_hits = _present(blob, set(blob_tokens), fw, singles, multis)
    raw = sum(w for w, _ in blob_hits)
    # BM25-style length normalization: divide the body score by a saturating
    # length factor so a long JD can't accrue score just by being long, then cap.
    dl = len(blob_tokens)
    norm = (1 - cfg.score_len_b) + cfg.score_len_b * (dl / cfg.avg_jd_tokens)
    # Real BM25, not `raw / norm`. That earlier form is the k1 -> infinity limit,
    # which removes term-frequency saturation and lets `norm`'s 0.25 floor multiply a
    # short document's score by up to 4x. Since every keyword here contributes at
    # most once (presence-based, tf == 1), BM25's tf*(k1+1)/(tf + k1*norm) collapses
    # to the expression below, which damps the same length effect without the
    # runaway multiplier. See Config.score_k1 for the measurement that motivated it.
    k1 = cfg.score_k1
    body = min(
        raw * (k1 + 1) / (1 + k1 * norm) if norm > 0 else raw, cfg.blob_score_cap
    )
    tl = p.get("title", "").lower()
    # Title double-count, but CAPPED so a keyword-stuffed title can't run away.
    title_hits = _present(tl, set(_TOKEN_RE.findall(tl)), fw, singles, multis)
    body += min(sum(w for w, _ in title_hits), cfg.title_score_cap)
    body -= sum(w for kw, w in cfg.title_penalty.items() if has(kw, tl))
    # The agency penalty runs over company + the FULL description, so it was the
    # most expensive line in the program: 13 whole-word regex searches across ~4 KB
    # per posting, measured at 68% of total scoring CPU. `_present` above solves
    # exactly this for fit_weights and was never extended here.
    #
    # The token set MUST come from agency_blob itself, not from the display blob.
    # `_present` resolves a single-token keyword by pure set membership, so passing
    # the wider title+location+text tokens made an agency keyword in the TITLE or
    # LOCATION score as though it were in the body: a role called "Staffing
    # Engineer" was penalised as a staffing agency. Tokenizing agency_blob costs one
    # more pass over the text and is still far cheaper than 13 regex searches.
    agency_blob = f"{p.get('company', '')} {p.get('text', '')}".lower()
    agency_tokens = set(_TOKEN_RE.findall(agency_blob))
    ap_singles, ap_multis = _kw_index(tuple(cfg.agency_penalty))
    agency_hits = _present(
        agency_blob, agency_tokens, cfg.agency_penalty, ap_singles, ap_multis
    )
    # CAPPED, like the body and title scores above. Uncapped, a long JD accumulated
    # penalty without limit while its body score was normalized down.
    body -= min(sum(w for w, _ in agency_hits), cfg.agency_penalty_cap)
    sig = ", ".join(kw for _, kw in sorted(blob_hits, reverse=True)[:n])
    return round(body), sig


def score(p: dict, cfg=None) -> int:
    return score_and_signals(p, cfg=cfg)[0]


def top_signals(p: dict, n: int = 7, cfg=None) -> str:
    return score_and_signals(p, n=n, cfg=cfg)[1]
