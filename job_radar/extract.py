"""Read a posting's prose and return a TYPED answer, with the span it was read from.

One home for "parse the body for one specific fact". Two fields live here at 0.9.0 --
`sponsorship` and `clearance`, the two that answer whether a person can apply at all --
and they share a scoping discipline that is the whole reason the module exists.

`experience_min_years` and `skills` were built against this same seam and CUT BEFORE
RELEASE, deliberately: both are additive and neither had been measured against a
labelled sample when the release closed. An unmeasured extractor is exactly the thing
that put an on-target-earnings band in `salary_min`, so they wait for 0.10.0 rather than
shipping dark.

EVERY PUBLIC CLASSIFIER IS A THIN WRAPPER OVER A `*_events()` FUNCTION THAT EMITS THE
SPAN IT SELECTED. That is not a debugging convenience, it is the fix for a measured
failure: on 2026-08-21 four separate harnesses asked "how many rows does this rule
act on" and returned 15, 26, 29 and a 269-row population that does not exist, because
each reimplemented the selector it was measuring. A detector picks the NEAREST matching
cue; a human reading the same posting reads the cue that GOVERNS the sentence, and a
boundary test applied to the human's occurrence reports behaviour the machine never
exhibits. Ask every downstream question of the object `*_events()` returned and that
class of error cannot occur. Never reimplement the selector to check it.

WHAT `None` MEANS HERE, on every field: the posting did not say, or said something this
module refuses to resolve. It is never "no" and never a plausible default -- the same
contract `engine._coerce` enforces for the rest of the record. Refusing costs a consumer
nothing; asserting the wrong thing costs a person an hour on an application they were
never eligible for.
"""

from __future__ import annotations

import re
from typing import NamedTuple

# ═══════════════════════════════════════════════════════════════════════════════
# CLOSED VOCABULARIES
# THESE RELOCATE TO `vocab.py` AT PHASE-3 INTEGRATION. They live here only because
# `extract.py` is built in an isolated worktree that cannot import names `vocab.py`
# does not carry yet -- importing them early turns this lane's gate red for a reason
# that has nothing to do with this code. The in-lane test below reads these literals;
# the orchestrator moves them and adds the central `sources`-style enforcing test at
# integration. THIS COMMENT IS THE THING THAT STOPS THE INTERIM HOME BECOMING
# PERMANENT -- delete it when they move, not before.
# ═══════════════════════════════════════════════════════════════════════════════

# `sponsorship` -- THREE states, not two. `conditional` is not a hedge on the schema, it
# is where every hedged employer template goes: on a 190-row stratified hand-label,
# conditional rows (33) OUTNUMBER `offered` (27), and they are repeating boilerplate
# ("considers sponsorship on a case-by-case basis", "may be available for select
# positions"). Without the state each one is forced into `offered` or `not_offered`, and
# `offered` is exactly where the sign-flip cost lives -- a wrong `offered` sends a person
# to spend an hour on an application they were never eligible for.
#
# `None` remains "the posting did not say", and stays the answer for ~91% of rows.
SPONSORSHIP_STATES = frozenset({"offered", "conditional", "not_offered"})

# `sponsorship_basis` -- HOW the polarity was decided, deliberately NOT where the text
# was found. The brief specified `{requirements, body}`; measured, only 12.9% of
# sponsorship occurrences sit in a typed `requirements` section, so that vocabulary would
# read `body` on ~87% of rows and carry no information. A basis that is constant is
# decoration, which the extraction plan forbids by name.
#
# `adjacent` WAS RULED IN AND IS NOT HERE, and that reverses half of a ruling on evidence
# that did not exist when the ruling was made -- flagged, not quietly dropped. The path
# it named decided 229 rows and was wrong on both of the scope-strings hand-checked out
# of it, including a clean offer read as a refusal. A vocabulary member nothing emits is
# the same smell that got two `salary_kind` members removed at 0.9.0, so it goes with the
# code rather than sitting here describing a path that no longer exists.
SPONSORSHIP_BASES = frozenset({"sentence"})

# `clearance` -- THREE states, and the third is a sign flip if you omit it. 2,191 of
# 5,585 clearance rows (39.2%) say a candidate must be ABLE TO OBTAIN a clearance rather
# than already hold one; folding those into `required` tells an eligible US citizen the
# job is closed to them.
CLEARANCES = frozenset({"required", "obtainable", "mentioned"})

# `clearance_basis` -- here the section vocabulary DOES do real work, unlike
# sponsorship's: 64.7% of the preference cues that decide a clearance's state are the
# enclosing `requirements` section's own heading rather than anything in the sentence.
CLEARANCE_BASES = frozenset({"requirements", "body"})


# ═══════════════════════════════════════════════════════════════════════════════
# SENTENCE SCOPING
# The unit every polarity decision is made in. Load-bearing, so it is tested
# directly rather than only through its callers.
# ═══════════════════════════════════════════════════════════════════════════════

# A SECOND SPLITTER, DELIBERATELY NOT `engine._CLAUSE_BREAK`, and the divergence is
# recorded here so it stays a decision rather than becoming drift -- the same posture
# `vocab.LOCATION_SEPARATORS` takes toward `dedup.normalize_location`.
#
# `engine._same_clause` tests a +/-90 window around a salary figure with the character
# set ".!?;\n" and no abbreviation handling. That is correct there and wrong here: this
# module scopes a POLARITY over a whole sentence, and an abbreviation that ends a
# sentence early truncates the clause the negation lives in. Measured on the sponsorship
# population [full harvest 2026-08-20, 515521d, 102,799 rows]: 1,528 rows -- 12.0% of the
# 12,708 carrying a sponsor word -- have `U.S.` / `e.g.` / `etc.` / `Inc.` within +/-120
# characters of the token, and a naive splitter cuts
# `"Fora is unable to sponsor or assist with U.S. work visas"` in half at `U.S.`,
# stranding `unable to` in one fragment and the sponsorship act in the next.
#
# Importing engine's helper is not an option in either direction: `engine` imports this
# module for the `_consume` hook, so a reverse import is a cycle.
_ABBREV = frozenset({
    "u.s", "u.s.a", "u.k", "e.g", "i.e", "etc", "inc", "ltd", "co", "corp", "llc",
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "approx", "dept",
    "est", "no", "ph.d", "b.s", "m.s", "a.m", "p.m", "min", "max", "yrs", "al",
})  # fmt: skip

_BREAK = re.compile(r"[.!?;\n]")
_WORD_BEFORE = re.compile(r"([A-Za-z][A-Za-z.]*)$")

# A sentence is capped, because nothing upstream caps body length and a body with no
# terminal punctuation would otherwise make the "sentence" the whole document -- which
# turns every cue scan into a whole-body scan. Real sentences in this corpus do not
# approach it; this is the same insurance `sections._HDR`'s {0,1000} bound is.
_SCOPE_CAP = 400


def _is_break(text: str, i: int) -> bool:
    """Is the punctuation at `i` a real sentence end, or an abbreviation's full stop?"""
    ch = text[i]
    if ch != ".":
        return True  # `!?;` and newline are never abbreviations
    m = _WORD_BEFORE.search(text[:i])
    if m and m.group(1).lower().strip(".") in _ABBREV:
        return False
    # A DOTTED ACRONYM ends in a letter-dot pair whose letter stands alone: `U.S.`,
    # `F.B.I.`. Testing the abbreviation list alone misses the long tail of these.
    if m and len(m.group(1)) >= 2 and m.group(1)[-2] == ".":
        return False
    # AND ITS *FIRST* DOT NEEDS THE SAME ANSWER, which neither rule above gives: at the
    # `.` after `U` in `U.S.`, the preceding word is the single letter `U`, which is in
    # no abbreviation list and has no dot before it. That dot ended the sentence and
    # stranded `unable to` in the previous fragment on the real corpus row
    # `"Fora is unable to sponsor or assist with U.S. work visas"` -- a SIGN FLIP, and
    # the same bug silently demoted an `obtainable` clearance to `mentioned`.
    # Requiring the next character to be a letter WITH NO SPACE is what stops this from
    # swallowing a genuine sentence end: `"...rated grade A. The next role"` has a space
    # and still breaks, while `U.S` does not.
    if (
        m
        and len(m.group(1).strip(".")) == 1
        and i + 1 < len(text)
        and text[i + 1].isalpha()
    ):
        return False
    return True


def sentence_bounds(text: str, lo: int, hi: int) -> tuple[int, int]:
    """The sentence containing [lo, hi), abbreviation-aware and length-capped."""
    start = max(0, lo - _SCOPE_CAP)
    for m in _BREAK.finditer(text, start, lo):
        if _is_break(text, m.start()):
            start = m.end()
    end = min(len(text), hi + _SCOPE_CAP)
    for m in _BREAK.finditer(text, hi, end):
        if _is_break(text, m.start()):
            end = m.start()
            break
    return max(start, lo - _SCOPE_CAP), end


def _typed_spans(p: dict, kind: str) -> list[tuple[int, int, str]]:
    """Located spans of `kind`, as (start, end, header).

    BOUNDS-CHECKED, NOT JUST KEY-CHECKED, for the reason `sections.section_text` gives:
    `text[start:end]` with `end` past the end does NOT raise, it returns a short slice.
    A span that does not fit its text is a disagreement, and a disagreement is dropped
    rather than half-read -- 36,270 such spans arrive from a consumer that truncated
    `text` without the spans that index it.
    """
    text = p.get("text") or ""
    out = []
    for s in p.get("sections") or []:
        if s.get("type") != kind:
            continue
        a, b = s.get("start"), s.get("end")
        if a is None or b is None or b > len(text) or a > b:
            continue
        out.append((a, b, s.get("header") or ""))
    return out


def _in_spans(spans: list[tuple[int, int, str]], i: int) -> str | None:
    for a, b, header in spans:
        if a <= i < b:
            return header
    return None



# ═══════════════════════════════════════════════════════════════════════════════
# X3 -- SPONSORSHIP
# The sign-flip field. `"we do not sponsor"` and `"we sponsor"` differ by two
# characters and mean opposite things.
# ═══════════════════════════════════════════════════════════════════════════════

# THE EVENT IS A COLLOCATION, NOT A KEYWORD, and this gate does most of the work.
# `sponsor` is heavily polysemous: of the 12,708 rows carrying the word, 4,950 (39.0%)
# contain NO immigration vocabulary anywhere in the body and are a different sense
# entirely -- `company-sponsored medical benefits`, `our sponsor banks`, `executive
# sponsor for major incidents`, `sponsored placements`, a clinical trial's `sponsors`,
# and an employer that `sponsors the exam`. None of those are hiring policy.
#
# THE WINDOW IS 20 CHARACTERS BECAUSE THE POPULATION IS BIMODAL, not because 20 read
# well. Nearest sponsor-to-anchor distance per row, cumulative [full harvest 2026-08-20]:
#
#     <= 20    6,323      <= 200    7,096
#     <= 40    6,839      <= 400    7,108
#     <= 60    6,999      <= 1000   7,213
#     <= 80    7,061      <= 4000   7,693   <- 585 rows, a SECOND mode
#
# 88.2% of anchored rows sit inside 40 characters and the curve is flat from 80 to 400.
# The 585 rows out past 1,000 are not distant statements, they are an unrelated
# `sponsor` and an unrelated `visa` in the same long body -- coincidence, not
# collocation. A single threshold over both modes would describe neither.
#
# 40 AND NOT 20, decided by the rows the change ADDS rather than by the cumulative curve.
# 20 was the first choice and it was wrong: `"we cannot sponsor applicants for work
# visas"` puts 21 characters between the two words, and that is an ordinary sentence
# rather than an edge case. At 20 it returned no statement at all.
#
# `\btn\b` and `\bopt\b` are DELIBERATELY ABSENT from the anchors although both are real
# visa classes: `TN` is also Tennessee and `\bopt\b` matches the `opt` of `opt-in` and
# `opt out`, because `-` is a word boundary. They appear only inside `_VISA_CLASS_RUN`,
# where a neighbouring class name establishes the sense.
_ANCHOR = r"""(?:
      visas? | immigration | h-?1-?b | o-?1 | j-?1 | green\s+card | work\s+permit
    | work\s+authoris\w* | work\s+authoriz\w* | employment\s+authoris\w*
    | employment\s+authoriz\w* | employment[-\s]based | permanent\s+resident\w*
)"""
_SPON = r"sponsor\w*"
_COLLOC = re.compile(
    rf"(?xi) (?: {_ANCHOR} [^.;!?\n]{{0,40}}? {_SPON} | {_SPON} [^.;!?\n]{{0,40}}? {_ANCHOR} )"
)

# `including, but not limited to` IS NOT NEUTRALISED, AND DOES NOT NEED TO BE. It was:
# the phrase carries `not`, attaches to a list rather than to the sponsorship act, and
# appears 280 times across 164 rows within +/-160 characters of a sponsor token -- one of
# them beside a genuine refusal, where it would have produced the right answer for the
# wrong reason. A blanking pass was written for it and then MUTATION-TESTED AWAY: disarmed
# in an isolated export, the guarding test still passed, because `_NEG` is built from
# phrases and not one of them matches `not limited to`. The blanking protected against
# nothing, so it is gone and `test_neg_never_matches_a_negation_that_governs_nothing`
# pins the property that actually does the work -- which fails the moment anyone adds a
# bare `not` to `_NEG`.

# A HEDGE OUTRANKS A POLARITY, and that ordering is the ruling this field turns on.
# 33 of 190 stratified rows are conditional -- MORE than the 27 labelled `offered` --
# and they are repeating employer templates, not edge cases. Without this state every
# one of them is forced into `offered` or `not_offered`, and `offered` is precisely
# where the sign-flip cost lives.
#
# Scope-restricting and case-by-case cues both land here. `at this time` does NOT: it is
# a temporal hedge on a statement that is otherwise flatly negative, and reading it as
# conditional would silence `"No visa sponsorship is available at this time"`.
_COND = re.compile(r"""(?xi)
      case[-\s]by[-\s]case | on\s+a\s+case | may\s+be\s+(?:available|possible|considered)
    | might\s+be\s+(?:available|possible) | not\s+be\s+assumed | cannot\s+(?:always\s+)?guarantee
    | can[’']?t\s+(?:always\s+)?guarantee | depend(?:s|ent|ing)\s+on | subject\s+to
    | where\s+appropriate | select\s+(?:positions?|roles?|candidates?)
    | certain\s+(?:positions?|roles?|\w*\s?visas?) | for\s+roles\s+(?:outside|within|in)
    | exceptional\s+candidates | evaluated\s+on | assessed?\s+on | reviewed\s+on
    | business\s+needs | consider(?:s|ed)?\s+(?:visa\s+)?sponsorship
    # SEVEN MORE HEDGE SHAPES, from the same census. Each was landing on `offered`, which
    # is the direction that costs a reader an hour: `"Kodiak MAY PROVIDE visa sponsorship"`
    # (35), `"able to offer... BUT DO REQUIRE that someone is based in Spain"` (15),
    # `"open to sponsoring... WHERE WE CAN"` (4), `"government policies ultimately dictate
    # our ABILITY TO SPONSOR"` (2), `"available for SELECTED ROLES"`, `"willing to sponsor
    # CERTAIN employment visas"`, `"aren't able to sponsor for EVERY ROLE and every
    # candidate"` (274, which is also a negation -- and lands here because a hedge
    # outranks a polarity).
    | may\s+(?:provide|offer|sponsor|support|consider|be)
    | where\s+(?:we\s+can|applicable|required|possible|appropriate)
    | when\s+(?:needed|required|applicable) | if\s+(?:needed|applicable|required)
    | select(?:ed)?\s+roles? | ability\s+to\s+sponsor
    | (?:for|to)\s+every\s+(?:role|candidate) | but\s+do\s+require
    | who\s+require\s+it
""")

# THE NEGATION LIST IS PHRASES, NEVER A BARE `not`. A bare token would read
# `"eligibility should not be assumed"` -- a CONDITIONAL template -- as a flat refusal,
# and that phrase appears in the single most common conditional shape in the corpus.
# Every member here attaches to the act of sponsoring or to the candidate's eligibility.
# FOUR NEGATION SHAPES WERE MISSING AND EVERY ONE OF THEM PRODUCED A SIGN FLIP. Measured
# by censusing all 87 distinct sentences the detector labelled `offered`: 469 of 1,464
# `offered` occurrences (32.0%) were actually refusals or hedges, because `_POS` matched
# the bare verb (`sponsor`, `offering`, `available`) while `_NEG` could not see the
# negation attached to it. In order of damage:
#
#   274  `"we aren't able to successfully sponsor visas"`   -- CONTRACTIONS. The list had
#                                                              `can't` and `won't` and no
#                                                              general form, so `aren't`,
#                                                              `don't`, `isn't` all read
#                                                              as positive.
#   185  `"Curaleaf is prohibited from offering sponsorship"` -- a negation carried by a
#                                                              VERB, with no `not` in it.
#     5  `"No new H-1B sponsorship is available"`           -- `no` + an intervening visa
#                                                              class. `no visa` and
#                                                              `no sponsor` were literal
#                                                              adjacencies and matched
#                                                              neither.
#     3  `"We are not currently able to sponsor visas"`     -- an ADVERB between `not` and
#                                                              `able`.
#
# The corpus uses both the ASCII apostrophe and U+2019, so both are matched everywhere.
_NEG = re.compile(r"""(?xi)
      not\s+(?:\w+\s+){0,2}able\s+to | unable\s+to | un(?:fortunately) | cannot
    | can\s+not | \b(?:are|is|was|were|do|does|did|ca|wo|would|could|should|has|have|had)
      n[’']t\b
    | will\s+not | do(?:es)?\s+not | did\s+not | no\s+longer
    | not\s+eligible | ineligible | not\s+offer | not\s+provid | not\s+sponsor
    | not\s+support | not\s+available | not\s+in\s+a\s+position
    | without\s+(?:the\s+)?(?:need|requirement)\s+for | without\s+sponsor
    | must\s+not\s+require | \bno\b[^.;!?\n]{0,25}?sponsor
    | (?:prohibited|barred|restricted|precluded|prevented)\s+from
    | lack\s+the\s+ability | not\s+consider | not\s+open\s+to
""")

_POS = re.compile(r"""(?xi)
      do(?:es)?\s+sponsor | we\s+sponsor | can\s+sponsor | will\s+sponsor
    | able\s+to\s+sponsor | happy\s+to\s+sponsor | open\s+to\s+sponsor
    | willing\s+to\s+sponsor | offer\w*\s+(?:\w+\s+){0,3}?sponsor
    | provid\w*\s+(?:\w+\s+){0,3}?sponsor | support\w*\s+(?:\w+\s+){0,3}?sponsor
    | sponsorship\s+(?:is\s+|are\s+)?(?:available|offered|provided|supported)
    | sponsorship\s+available | we\s+do\s+sponsor | sponsor\s+visas
    # THE SUBJECT-VERB SHAPE, missing until a census caught it: `"DensityAI SPONSORS
    # QUALIFIED CANDIDATES for H-1B, O-1, TN, E-3"` names the employer, the act and the
    # visa classes and matched none of the patterns above, all of which expect either
    # `we` or an `offer/provide/support` verb. It fell through to the `adjacent` scan and
    # came back a refusal.
    | sponsors?\s+(?:qualified\s+)?(?:candidates|applicants|employees|individuals|
                                       international|eligible)
    | welcome\s+applicants
""")


class SponsorshipHit(NamedTuple):
    """One selected collocation and the polarity decided for it.

    `span` is the exact text the detector matched; `scope` is the sentence the cues were
    read in. Both are emitted so a reviewer can ask questions of what the code chose
    rather than of what a reader would have chosen.
    """

    start: int
    end: int
    span: str
    scope_start: int
    scope_end: int
    scope: str
    state: str | None
    basis: str | None
    cues: tuple[str, ...]


def sponsorship_events(text: str) -> list[SponsorshipHit]:
    """Every sponsorship statement in `text`, with the polarity read for each.

    THE SUPPRESSION IS PER-OCCURRENCE AND MUST STAY THAT WAY. A row-level filter that
    drops postings containing `company-sponsored` or `executive sponsor` deletes real
    answers: of 25 rows drawn for that wrong-sense shape, 8 ALSO carry a genuine
    `not_offered` policy elsewhere in the same body
    (`"...personal executive sponsorship engagement. At this time, we cannot sponsor
    applicants for work visas."`). Evaluating each collocation separately is what makes
    the noise sense simply fail to match, instead of poisoning its row.

    PRECEDENCE IS CONDITIONAL > NEGATIVE > POSITIVE, read inside one sentence.
    Negative-over-positive is settled by measurement, not preference: across two
    independent draws totalling 85 distinct rows in which a positive-shaped cue shares a
    sentence with a negation, ALL 85 are genuinely `not_offered` and there is no
    counter-example -- `"we are not able to offer visa sponsorship"` contains `offer`.
    Conditional outranks both because a hedged statement is hedged in both directions.
    """
    if not text:
        return []
    hits: list[SponsorshipHit] = []
    for m in _COLLOC.finditer(text):
        s0, s1 = sentence_bounds(text, m.start(), m.end())
        scope = text[s0:s1]
        state = basis = None
        cues: tuple[str, ...] = ()
        for name, pat in (
            ("conditional", _COND),
            ("not_offered", _NEG),
            ("offered", _POS),
        ):
            found = pat.search(scope)
            if found:
                state, basis = name, "sentence"
                cues = tuple(x.group(0) for x in pat.finditer(scope))
                break
        # THE `adjacent` PATH WAS BUILT, MEASURED AND REMOVED. It read a negation from
        # the NEIGHBOURING sentence when the event's own sentence carried no cue, on the
        # argument that it could only ever demote. It demoted the wrong rows: it decided
        # 229 rows, every one `not_offered`, and both scope-strings hand-checked out of
        # it were wrong -- `"DensityAI sponsors qualified candidates for H-1B, O-1, TN,
        # E-3, and other employment-based visas"` (45 occurrences, a clean OFFER turned
        # into a refusal by a `do not` in a different sentence) and a neutral disclosure
        # clause (`"Candidates must disclose any current or future need for
        # employment-based immigration sponsorship"`, 198). A sentence that does not
        # contain the sponsorship act does not govern it, and 3.7% more coverage does not
        # pay for a sign flip in the direction that turns an eligible person away.
        #
        # So a sentence with no cue returns `None`, which is what `None` is for.
        hits.append(
            SponsorshipHit(
                m.start(), m.end(), m.group(0), s0, s1, text[s0:s1], state, basis, cues
            )
        )
    return hits


def sponsorship(text: str) -> tuple[str | None, str | None]:
    """(state, basis) for a whole posting -- a thin vote over `sponsorship_events`.

    ANY DISAGREEMENT RETURNS None. A body that says `"unable to offer new H-1B
    sponsorship"` and then `"we can support H-1B transfers and are able to sponsor TN
    visas"` is genuinely two policies, and no single value is true of it. Picking
    whichever the scanner reached first is how a coin-flip acquires a column.
    """
    states = {h.state for h in sponsorship_events(text) if h.state}
    if len(states) != 1:
        return None, None
    state = states.pop()
    basis = (
        "sentence"
        if any(
            h.state == state and h.basis == "sentence" for h in sponsorship_events(text)
        )
        else "adjacent"
    )
    return state, basis


# ═══════════════════════════════════════════════════════════════════════════════
# X5 -- CLEARANCE
# Three states, because two of them tell an eligible citizen the job is closed.
# ═══════════════════════════════════════════════════════════════════════════════

_LEVEL = r"""(?:
      ts\s*/\s*sci | top[-\s]secret | \bsci\b | \bsecret\b | public\s+trust
    | \bq\s+clearance\b | \bl\s+clearance\b | security\s+clearance | \bclearance\b
    | polygraph | \bcbp\b | \bdod\b
)"""
_CLEAR = re.compile(rf"(?xi){_LEVEL}")

# THE WRONG SENSES ARE BOILERPLATE AND A DIFFERENT INDUSTRY, and a bare token list
# reaches both. A broad probe matched 5,949 rows (5.8%) where the real population is
# nearer 4,400; the excess is the federal EEO poster `"Employee Polygraph Protection
# Act"`, a lawyer's `"IP clearance and protection for new product names"`, customs and
# medical clearance, and `FSP` read as an abbreviation a company defined for itself.
_CLEAR_NOISE = re.compile(r"""(?xi)
      polygraph\s+protection | employee\s+polygraph | \bip\s+clearance
    | clearance\s+and\s+protection | customs\s+clearance | medical\s+clearance
    | clearance\s+sale | credit\s+clearance | clearance\s+rack | secret\s+sauce
    | trade\s+secret | secret\s+shopper
""")

# `able to obtain` IS ITS OWN STATE, and folding it into `required` is a sign flip in
# the direction that closes a job to someone who could take it: 2,191 of 5,585 clearance
# rows (39.2%) say a candidate must be ABLE TO OBTAIN a clearance, not that they must
# already hold one. `"Must be able to obtain and maintain a U.S. Secret clearance"`
# carries `must` AND `able to obtain`, so OBTAINABLE IS TESTED FIRST -- the modal belongs
# to the obtaining, not to the holding.
_OBTAINABLE = re.compile(r"""(?xi)
      able\s+to\s+obtain | ability\s+to\s+obtain | eligible\s+to\s+obtain
    | eligibility\s+to\s+obtain | obtain\s+and\s+maintain | willing\s+to\s+obtain
    | can\s+obtain | qualify\s+for\s+a?\s*(?:security\s+)?clearance
    | clearance\s+eligib | eligible\s+for\s+a?\s*(?:security\s+)?clearance
""")
_REQUIRED = re.compile(r"""(?xi)
      \bactive\b | \bcurrent\b | must\s+(?:have|possess|hold|maintain)
    | required | requires | requirement | \bhold\s+an?\b | in\s+possession\s+of
    | \bexisting\b
""")
# A PREFERENCE IS USUALLY A HEADER, NOT A SENTENCE. 1,295 of 2,002 preference cues near
# a clearance token (64.7%) are the enclosing SECTION'S heading -- `Nice-to-Have
# Qualifications`, `Preferred Skills and Experience` -- and sit outside the sentence
# entirely. A fixed-width scan reads across the boundary into the next section's text
# and mislabels the section after it, so the header is consulted directly and the
# sentence scan stops where the section does.
_PREFERRED = re.compile(r"""(?xi)
    preferred | nice[-\s]to[-\s]have | \bbonus\b | \ba\s+plus\b | desired | ideally
    | \bhelpful\b | not\s+required | may\s+require
""")


class ClearanceHit(NamedTuple):
    start: int
    end: int
    span: str
    scope: str
    header: str | None
    state: str | None
    basis: str | None


def clearance_events(p: dict) -> list[ClearanceHit]:
    """Every clearance mention, with its state and the section header that framed it."""
    text = p.get("text") or ""
    if not text:
        return []
    req = _typed_spans(p, "requirements")
    out: list[ClearanceHit] = []
    for m in _CLEAR.finditer(text):
        s0, s1 = sentence_bounds(text, m.start(), m.end())
        scope = text[s0:s1]
        if _CLEAR_NOISE.search(scope):
            continue
        header = _in_spans(req, m.start())
        # `preferred` in the heading demotes whatever the sentence says, because the
        # heading is the employer's own framing of the whole list beneath it.
        if (header and _PREFERRED.search(header)) or _PREFERRED.search(scope):
            state = "mentioned"
        elif _OBTAINABLE.search(scope):
            state = "obtainable"
        elif _REQUIRED.search(scope):
            state = "required"
        else:
            state = "mentioned"
        out.append(
            ClearanceHit(
                m.start(),
                m.end(),
                m.group(0),
                scope,
                header,
                state,
                "requirements" if header is not None else "body",
            )
        )
    return out


def clearance(p: dict) -> tuple[str | None, str | None]:
    """(state, basis). The STRONGEST claim in the posting wins.

    PROMOTION IS ONE-WAY AND EXPLICIT. A posting mentioning a clearance three times is
    described by its strongest statement -- if any sentence says a clearance is required,
    the job requires one, whatever the other two say. The default is the weakest state,
    so an unrecognised phrasing lands on `mentioned` rather than being asserted upward.
    """
    hits = clearance_events(p)
    if not hits:
        return None, None
    for state in ("required", "obtainable", "mentioned"):
        chosen = [h for h in hits if h.state == state]
        if chosen:
            return state, chosen[0].basis
    return None, None


# ═══════════════════════════════════════════════════════════════════════════════
# THE SEAM
# ═══════════════════════════════════════════════════════════════════════════════


def enrich(p: dict) -> dict:
    """Read the body once and set every extracted field. Mutates and returns `p`.

    CALLED AFTER THE LAST GATE IN `engine._consume`, not beside `derive_remote` and
    `derive_salary`. Those two must run early because `is_remote` reads what they set;
    nothing here feeds a gate, so running it last does identical work on strictly fewer
    rows -- the remote, age and url gates all `continue` before it.

    THE POSTING'S OWN BODY IS THE ONLY INPUT, and that is worth stating because the
    sponsorship clause is END-LOADED: 65% of statements sit in the last three deciles of
    the body as trailing legal notes, and 477 occurrences across 411 rows sit past
    character 8,000 -- invisible to a consumer that truncates at 8k, visible to the
    engine, which reads the intact body. Extracting here is the difference.

    ON A MERGED POSTING the body is the WINNER's: `_consume`'s dedup merge is
    winner-takes-all on `(_src_pref, len(text), score)`, not field-level. If the losing
    copy carried the policy and the winner's body does not, these fields read `None`.
    """
    text = p.get("text") or ""
    p["sponsorship"], p["sponsorship_basis"] = sponsorship(text)
    p["clearance"], p["clearance_basis"] = clearance(p)
    return p
