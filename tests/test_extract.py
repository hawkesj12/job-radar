"""Tests for `job_radar.extract`.

EVERY ASSERTION HERE IS AGAINST THE SHIPPED SELECTOR, never against a reimplementation
of it. Where a test needs to know which text the detector chose, it reads the
`*_events()` return rather than re-deriving the span -- the failure this module was
built to prevent (four harnesses, four different populations, one question).

The prose in these fixtures is real: every sponsorship and clearance string
below was taken from the 102,799-row harvest at `_reports/full_flat_raw.ndjson`
[full harvest 2026-08-20, git_head 515521d, 11 of 19 sources].
"""

from __future__ import annotations

import re

import pytest

from job_radar import extract

# ═══════════════════════════════════════════════════════════════════════════════
# THE SENTENCE SPLITTER
# A helper that decides scope is not a helper, it is the rule -- so it is tested
# directly, not only through the detectors that call it.
# ═══════════════════════════════════════════════════════════════════════════════


def test_abbreviation_does_not_end_a_sentence():
    """`U.S.` must not split the clause its negation lives in.

    THE MOTIVATING ROW, verbatim from the corpus. A naive `[.!?]` splitter cuts at
    `U.S.` and strands `unable to` in the previous fragment, so the sponsorship act is
    scoped to a sentence with no negation in it -- which is a SIGN FLIP, not a miss.
    1,528 rows (12.0% of the 12,708 carrying a sponsor word) have an abbreviation
    within +/-120 characters of the token.
    """
    t = "Fora is unable to sponsor or assist with U.S. work visas at this time."
    lo = t.index("visas")
    a, b = extract.sentence_bounds(t, lo, lo + 5)
    assert "unable to" in t[a:b], t[a:b]


@pytest.mark.parametrize(
    "text",
    [
        "We cannot sponsor visas, e.g. H-1B, for this role.",
        "Acme Inc. does not provide visa sponsorship.",
        "Benefits, perks, etc. aside, we are unable to sponsor work visas.",
    ],
)
def test_common_abbreviations_keep_their_sentence_whole(text):
    lo = text.lower().index("sponsor")
    a, b = extract.sentence_bounds(text, lo, lo + 7)
    assert extract._NEG.search(text[a:b]), text[a:b]


def test_a_real_full_stop_still_ends_a_sentence():
    """The splitter must not become permissive in the course of handling abbreviations."""
    t = "We sponsor visas. Remote work is not available for this position."
    lo = t.index("visas")
    a, b = extract.sentence_bounds(t, lo, lo + 5)
    assert "not available" not in t[a:b], t[a:b]


def test_newline_always_breaks():
    t = "We sponsor visas\nRelocation is not offered."
    lo = t.index("visas")
    a, b = extract.sentence_bounds(t, lo, lo + 5)
    assert "not offered" not in t[a:b]


# ═══════════════════════════════════════════════════════════════════════════════
# X3 -- SPONSORSHIP. THE SIGN-FLIP GATE.
# ═══════════════════════════════════════════════════════════════════════════════

_NOT_OFFERED = [
    "Please note that we do not provide immigration sponsorship for this position.",
    "This role is not eligible for visa sponsorship.",
    "No visa sponsorship is available for this position at this time.",
    "We are unable to provide visa sponsorship or support visa transfers.",
    "We are unable to sponsor or take over sponsorship of an employment visa at this time.",
    "Candidates must be legally authorized to work in the United States without the "
    "need for visa sponsorship.",
    "This position requires work authorization that does not now or in the future "
    "require visa sponsorship.",
    "Employer does not offer work visa sponsorship for this role.",
    "Unfortunately, we are unable to provide visa sponsorship for this position.",
    "At this time, we cannot sponsor applicants for work visas.",
]

_OFFERED = [
    "Visa sponsorship is available.",
    "We sponsor visas, obviously.",
    "We are happy to sponsor visas for the right candidate.",
]

_CONDITIONAL = [
    "Docker considers visa sponsorship on a case-by-case basis based on business needs.",
    "Visa sponsorship may be available for select positions based on business needs.",
    "Visa sponsorship may be available for select positions based on business needs. "
    "Sponsorship decisions are evaluated on a case-by-case basis, and eligibility "
    "should not be assumed for all opportunities.",
    "We support visa sponsorship where appropriate, including O-1, H-1B, J-1 and TN.",
    "Please note we are unable to sponsor visas for roles outside of engineering and product.",
]


@pytest.mark.parametrize("text", _NOT_OFFERED)
def test_negative_sponsorship_is_never_read_as_offered(text):
    """THE ONE ASSERTION THIS FIELD EXISTS FOR. A sign flip here sends a person to spend
    an hour on an application they were never eligible for; a miss leaves them exactly
    where they are today. `offered` must be unreachable from this list."""
    state, _basis = extract.sponsorship(text)
    assert state != "offered", (text, state)
    assert state == "not_offered", (text, state)


@pytest.mark.parametrize("text", _OFFERED)
def test_positive_sponsorship_reads_offered(text):
    assert extract.sponsorship(text)[0] == "offered", text


@pytest.mark.parametrize("text", _CONDITIONAL)
def test_hedged_sponsorship_reads_conditional_not_offered(text):
    """A HEDGE OUTRANKS A POLARITY, in both directions.

    Without this state every one of these is forced into `offered` or `not_offered`.
    `offered` is where the sign-flip cost lives, so a hedge that resolves upward is the
    expensive failure -- these outnumber genuine `offered` rows 33 to 27 on a 190-row
    stratified label, which is why the state exists at all.
    """
    state, _ = extract.sponsorship(text)
    assert state == "conditional", (text, state)


def test_an_implied_positive_is_a_known_miss():
    """PINNED, NOT FIXED. `"Visa sponsorship - we will do everything in our power to get
    you to NYC"` is an offer with no offering verb in it, and the cues that would catch
    it (`get you`, `we will`) are far too loose to point at sponsorship specifically.

    Recall is the cheap direction here: a missed `offered` leaves a reader exactly where
    they are today, while a wrong `offered` costs them an hour. This test exists so the
    silence is a recorded decision rather than an undiscovered gap -- if someone widens
    `_POS` later, this is the row to argue about.
    """
    t = "Visa sponsorship - we will do everything in our power to get you to NYC."
    assert extract.sponsorship(t)[0] is None


def test_a_positive_word_inside_a_negation_is_not_offered():
    """`"not able to OFFER visa sponsorship"` contains `offer`.

    Settled by measurement across two independent draws totalling 85 distinct rows: every
    one in which a positive-shaped cue shares a sentence with a negation is genuinely
    `not_offered`, with no counter-example. This is the single most likely sign flip.
    """
    t = "We are not able to offer visa sponsorship for this position."
    assert extract.sponsorship(t)[0] == "not_offered"


# EVERY ONE OF THESE WAS A LIVE SIGN FLIP, found by censusing all 87 distinct sentences
# the detector labelled `offered` on the corpus. Together they were 469 of 1,464 `offered`
# occurrences -- 32.0% of the class -- and each is here with the occurrence count it cost,
# because a regression in any of them is the defect this field exists to prevent.
_CENSUS_SIGN_FLIPS = [
    ("aren't", 274, "However, we aren't able to successfully sponsor visas for this role."),
    ("prohibited from", 185,
     "Curaleaf is prohibited from offering visa sponsorship for this position."),
    ("no + visa class", 2, "No H1-B visa sponsorship available for this role."),
    ("don't", 2, "Please note that we don't sponsor visas for the operations role."),
    ("not <adverb> able", 1, "We are not currently able to sponsor visas."),
    ("not normally able", 1, "We're not normally able to sponsor visas."),
    ("no new", 1, "No new H-1B visa sponsorship is available for this role."),
]


@pytest.mark.parametrize(
    "shape,cost,text", _CENSUS_SIGN_FLIPS, ids=[c[0] for c in _CENSUS_SIGN_FLIPS]
)
def test_census_sign_flips_stay_fixed(shape, cost, text):
    """`_POS` matched the bare verb while `_NEG` could not see the negation attached."""
    state, _ = extract.sponsorship(text)
    assert state != "offered", (shape, cost, state, extract.sponsorship_events(text))


def test_the_subject_verb_offer_shape_is_positive():
    """`"DensityAI SPONSORS QUALIFIED CANDIDATES for H-1B"` names the employer, the act
    and the visa classes, and matched no positive pattern -- every one expected either
    `we` or an `offer`/`provide`/`support` verb. It fell through and came back a refusal
    on 45 occurrences."""
    t = "DensityAI sponsors qualified candidates for H-1B, O-1, TN and E-3 visas."
    assert extract.sponsorship(t)[0] == "offered"


def test_neg_never_matches_a_negation_that_governs_nothing():
    """`including, but not limited to` carries `not` and attaches to a LIST.

    280 occurrences across 164 rows sit within +/-160 characters of a sponsor token, and
    one of them sits beside a genuine refusal -- so reading it would produce the right
    answer for the wrong reason, which is worse than a visible failure.

    THIS ASSERTS THE PROPERTY, NOT A WORKAROUND. A blanking pass was written for this and
    mutation-testing showed it was unreachable: `_NEG` is built from phrases, and none of
    them matches `not limited to`. The blanking was removed; this fails the moment
    anyone adds a bare `not` to `_NEG`, which is the change that would reintroduce the bug.
    """
    assert extract._NEG.search("including, but not limited to, visa sponsorship") is None
    assert extract._NEG.search("including but not limited to OPT and STEM OPT") is None
    t = ("We offer visa sponsorship for a range of classifications, including but not "
         "limited to H-1B and O-1.")
    assert extract.sponsorship(t)[0] == "offered", extract.sponsorship_events(t)


def test_wrong_sense_sponsor_is_not_a_sponsorship_statement():
    """`sponsor` is polysemous: 4,950 of 12,708 rows carrying it (39.0%) contain no
    immigration vocabulary at all. None of these is hiring policy."""
    for t in (
        "Company-sponsored medical, dental and vision benefits from day one.",
        "Act as an escalation point and executive sponsor for major incidents.",
        "In partnership with our sponsor banks, we offer credit cards.",
        "Artefact sponsors the exam and gives you time to prepare.",
        "Spearhead structured A/B tests across sponsored placements and creative content.",
    ):
        assert extract.sponsorship(t)[0] is None, t


def test_wrong_sense_suppression_is_per_occurrence_not_per_row():
    """A ROW-LEVEL FILTER WOULD DELETE A REAL ANSWER.

    Of 25 rows drawn for the wrong-sense shape, 8 ALSO carry a genuine policy elsewhere
    in the same body. Dropping the row because it contains `executive sponsorship`
    discards the `not_offered` that follows it. Evaluating each collocation separately
    is what makes the noise sense simply fail to match instead of poisoning its row.
    """
    t = (
        "Key project client success includes personal executive sponsorship engagement. "
        "At this time, we cannot sponsor applicants for work visas."
    )
    state, _ = extract.sponsorship(t)
    assert state == "not_offered", extract.sponsorship_events(t)


def test_two_disagreeing_policies_return_none():
    """A body that states two policies has no single true value, and picking whichever
    the scanner reached first is how a coin-flip acquires a column."""
    t = (
        "Unfortunately, we are unable to offer new H-1B visa sponsorship for this "
        "position. However, we are able to sponsor TN visas for eligible candidates."
    )
    assert extract.sponsorship(t)[0] is None, extract.sponsorship_events(t)


def test_events_emit_the_span_and_scope_that_decided_it():
    """THE INTROSPECTION CONTRACT. Every downstream measurement asks its questions of
    this object, so it has to carry the span, the scope and the deciding cues."""
    t = "Acme is unable to provide visa sponsorship for this role."
    (hit,) = extract.sponsorship_events(t)
    assert t[hit.start : hit.end] == hit.span
    assert t[hit.scope_start : hit.scope_end] == hit.scope
    assert hit.state == "not_offered"
    assert hit.basis == "sentence"
    assert hit.cues and all(c in hit.scope for c in hit.cues)


def test_a_negation_in_a_neighbouring_sentence_does_not_decide():
    """THE `adjacent` PATH WAS BUILT, MEASURED AND REMOVED, and this pins the removal.

    It decided 229 rows, all `not_offered`, and both scope-strings hand-checked out of it
    were wrong. The first case below is the one that mattered: a clean offer, turned into
    a refusal by a `do not` belonging to a different sentence, on 45 occurrences.
    """
    offer = ("DensityAI sponsors qualified candidates for H-1B, O-1, TN and E-3 visas. "
             "Please do not contact recruiters about this posting.")
    assert extract.sponsorship(offer)[0] == "offered", extract.sponsorship_events(offer)
    neutral = ("Candidates must disclose any current or future need for employment-based "
               "immigration sponsorship. Background checks do not affect this.")
    assert extract.sponsorship(neutral)[0] is None, extract.sponsorship_events(neutral)


# ═══════════════════════════════════════════════════════════════════════════════
# X5 -- CLEARANCE
# ═══════════════════════════════════════════════════════════════════════════════


def _req(text: str) -> dict:
    """A record whose whole body is one located `requirements` section."""
    return {
        "text": text,
        "sections": [
            {"type": "requirements", "header": "Requirements",
             "start": 0, "end": len(text)}
        ],
    }


def test_able_to_obtain_is_not_required():
    """THE THIRD STATE, AND THE REASON IT EXISTS. 2,191 of 5,585 clearance rows (39.2%)
    say a candidate must be ABLE TO OBTAIN a clearance rather than already hold one.
    Folding those into `required` tells an eligible US citizen the job is closed."""
    for t in (
        "Eligible to obtain and maintain a U.S. Secret security clearance.",
        "Must be able to obtain and maintain a Top Secret clearance.",
        "US Citizenship and ability to obtain and maintain a Top-Secret security clearance.",
        # CARRIES BOTH CUES, and it is the only one of these that tests the ORDERING.
        # Swapping `_REQUIRED` ahead of `_OBTAINABLE` survived a mutation run because the
        # three fixtures above contain no required-cue at all, so the precedence they were
        # written to guard was never exercised. This phrasing is common and unambiguous:
        # the clearance is required, and being ABLE TO OBTAIN it is what is asked of you.
        "Ability to obtain and maintain a Secret clearance is required.",
    ):
        assert extract.clearance(_req(t))[0] == "obtainable", t


def test_an_active_clearance_is_required():
    for t in (
        "Clearance Requirement: This position requires an active TS/SCI with a polygraph.",
        "Active final DoD Secret clearance required.",
        "Must possess a current Top Secret clearance.",
    ):
        assert extract.clearance(_req(t))[0] == "required", t


def test_a_preferred_heading_demotes_the_sentence_below_it():
    """1,295 of 2,002 preference cues near a clearance token (64.7%) are the enclosing
    SECTION'S heading, not the sentence -- so the header is consulted directly."""
    body = "Active Secret clearance with TS/SCI eligibility."
    p = {
        "text": body,
        "sections": [
            {
                "type": "requirements",
                "header": "Nice-to-Have Qualifications",
                "start": 0,
                "end": len(body),
            }
        ],
    }
    assert extract.clearance(p)[0] == "mentioned", extract.clearance_events(p)


def test_the_eeo_polygraph_poster_is_not_a_clearance():
    """Federal EEO boilerplate, present on thousands of postings. A bare token list
    reads it as a security requirement."""
    t = (
        "Rights under Federal Employment Laws: Family & Medical Leave Act, Equal "
        "Opportunity Employment, Employee Polygraph Protection Act."
    )
    assert extract.clearance(_req(t))[0] is None, extract.clearance_events(_req(t))


def test_a_lawyers_clearance_is_not_a_security_clearance():
    t = "Support proactive IP clearance and protection for new product names."
    assert extract.clearance(_req(t))[0] is None


def test_clearance_promotes_to_the_strongest_claim():
    t = (
        "Familiarity with government programs. This position requires an active "
        "TS/SCI clearance."
    )
    assert extract.clearance(_req(t))[0] == "required"


# ═══════════════════════════════════════════════════════════════════════════════
# THE CLOSED VOCABULARIES AND THE SEAM
# ═══════════════════════════════════════════════════════════════════════════════


def test_vocabularies_are_pinned_by_reading_the_literal():
    """A closed vocabulary is only closed if a test fails when someone widens it.

    These RELOCATE to `vocab.py` at integration; this test relocates with them and is
    replaced by the central `sources`-style enforcement that reads the module source.
    """
    assert extract.SPONSORSHIP_STATES == frozenset(
        {"offered", "conditional", "not_offered"}
    )
    assert extract.SPONSORSHIP_BASES == frozenset({"sentence"})
    assert extract.CLEARANCES == frozenset({"required", "obtainable", "mentioned"})
    assert extract.CLEARANCE_BASES == frozenset({"requirements", "body"})


def test_every_emitted_value_is_inside_its_vocabulary():
    """Enforced over the real fixtures above rather than a toy string, so a detector that
    invents a state fails here rather than in whatever consumer branches on it."""
    for t in _NOT_OFFERED + _OFFERED + _CONDITIONAL:
        state, basis = extract.sponsorship(t)
        assert state in extract.SPONSORSHIP_STATES
        assert basis in extract.SPONSORSHIP_BASES
    for h in extract.clearance_events(_req("Active TS/SCI clearance required.")):
        assert h.state in extract.CLEARANCES
        assert h.basis in extract.CLEARANCE_BASES


def test_enrich_sets_every_field_and_is_safe_on_an_empty_body():
    """`text` is None on whole sources (smartrecruiters sends no body), and `sections` is
    absent on any record that never reached `clean_with_sections`."""
    for p in ({}, {"text": None}, {"text": ""}, {"text": "hello", "sections": None}):
        out = extract.enrich(p)
        for f in (
            "sponsorship",
            "sponsorship_basis",
            "clearance",
            "clearance_basis",
        ):
            assert f in out and out[f] is None, (p, f)


def test_enrich_is_idempotent():
    """`_consume` can hand the same dict back on a merge path; running twice must not
    change an answer or accumulate a list."""
    p = _req(
        "We cannot provide visa sponsorship. Active TS/SCI clearance required."
    )
    once = dict(extract.enrich(p))
    twice = dict(extract.enrich(p))
    assert once == twice


def test_no_regex_in_the_module_can_scan_unbounded():
    """`sections._HDR` needed a {0,1000} bound because an unclosed tag made an `(.*?)`
    scan to end-of-string, O(n^2), 7.6s on a 158 KB body. Nothing upstream caps body
    length, so an unbounded `.*` or `.+` here is the same latent defect."""
    import inspect

    src = inspect.getsource(extract)
    src = re.sub(r"^\s*#.*$", "", src, flags=re.M)  # comments are prose
    src = re.sub(r'""".*?"""', "", src, flags=re.S)  # so are docstrings
    assert ".*" not in src and ".+" not in src, (
        "unbounded quantifier in a body-scanning regex"
    )
