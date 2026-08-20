"""A job posting's HTML -> labelled sections. The map from an employer's formatting to
a fixed vocabulary.

WHY THIS LIVES UPSTREAM. The structure exists only in the DECODED markup, and
`util.clean` removes it -- correctly, that is its job. Once a body is plain text,
`<strong>What you'll do</strong>` is just three words in a run of prose with nothing
marking them as a heading, and nothing short of re-fetching the posting gets it back.
So the split has to happen here, in the library that still holds the raw response,
rather than in a consumer that only ever receives `text`.

WHAT THE HEADERS ARE, AND ARE NOT. They are formatting, not schema -- 3,591 distinct
header strings (3,326 after the lowercase/strip normalization `classify` applies) over
30,145 header entries on the nine-board corpus, and 43% of those entries classify to
nothing at all. There is nothing to parse into columns directly. But
the header TYPES recur, and that is what this maps: the many names onto the few types.
Measured with THIS classifier across the 730-employer corpus, `responsibilities` appears
for 92.7% of employers and `requirements` for 92.5%, which is better fill than
`seniority` or `category` gets from the vendors themselves.

Every measurement in this file is one of two corpora and says which: the NINE-BOARD
corpus (2,697 raw Greenhouse bodies, nine employers, uncapped) or the 730-EMPLOYER
corpus (24,976 stored bodies, capped at 8,000 chars). Mixing them silently is how the
first draft of this docstring ended up quoting a pre-release baseline as a result.

WHAT IT DELIBERATELY DOES NOT DO. There is no `signal_text()` here and no KEEP/DROP
split, though the module this was ported from had both. Which sections matter is a
CONSUMER's opinion -- an embedding pipeline wants responsibilities and requirements, a
salary tool wants compensation, a compliance reader wants the EEO block. job-radar
reports what the source said and names how it decided, the same discipline as
`remote_basis` and `salary_basis`. Ranking them is downstream work.
"""

from __future__ import annotations

import re

# Curly apostrophes are NOT optional, and this is the single highest-leverage character
# class in the file. `what we're looking for` appears 792 times in the 730-employer
# corpus with U+2019 and ZERO times with an ASCII quote -- so a matcher written with `'`
# loses every one of them silently. Fixing this alone moved measured `responsibilities`
# coverage there from 37.7% to 51.8%. U+02BC is the third form, rare but real.
_APOS = "['’ʼ]"


def _p(*alts: str) -> re.Pattern:
    return re.compile("|".join(alts))


# ORDER IS SEMANTIC: first match wins, so specific sits before generic.
#
# `about_company` sits AFTER `requirements`, which is the one ordering that is not
# obvious and the one that was measured wrong. Its `^about [a-z]` pattern happily
# catches "About You" -- a requirements header, not a company blurb -- and did so on
# 2,085 entries across 82 employers. Verified corpus-wide after the move: exactly two
# header transitions occur (`about_company` -> `requirements`, and unclassified ->
# `requirements`), zero entries leave `responsibilities`, and no real company header
# such as "About Anthropic" moves, because `requirements` does not match it.
SECTION_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("fraud_warning", _p(
        r"no financial request", r"verify communication", r"unsolicited outreach",
        r"suspect fraud", r"recruit\w* (fraud|scam)", r"beware of", r"fraudulent",
        r"impersonat", r"never ask you (for|to)")),
    ("eeo_legal", _p(
        r"equal (employment )?opportunit", r"\beeo\b", r"diversity", r"inclusion",
        r"accommodat", r"e-verify", r"privacy", r"\bitar\b", r"background check",
        r"export control", r"sponsorship", r"visa", r"commitment to", r"disclaimer",
        r"reasonable adjust")),
    ("compensation", _p(
        r"compensation", r"pay range", r"pay transparen", r"\bsalary\b", r"total reward",
        rf"what you{_APOS}?ll earn", r"\bpay and\b", r"base pay", r"equity")),
    ("benefits", _p(
        r"benefit", r"what we offer", r"\bperks?\b", r"why join", r"why work",
        r"our offer", r"wellness", r"culture (&|and) reward", r"time off")),
    ("apply_cta", _p(
        r"apply (today|now)", r"how to (get started|apply)", r"next steps",
        r"click here", r"ready to (apply|join|make)", r"interview process",
        r"hiring process", r"application process")),
    ("metadata", _p(
        r"^reports to", r"^employment type", r"^job title", r"^job id", r"^req(uisition)? ",
        r"^department\b", r"^job type", r"^work (type|schedule)")),
    ("location_travel", _p(
        r"^location", r"\btravel\b", r"\bremote\b", r"on-?site", r"work environment",
        r"physical (demand|require)", r"^schedule", r"^hours", r"relocation")),
    ("requirements", _p(
        r"qualification", r"requirement", rf"what we{_APOS}?re looking for",
        r"what we (are|look) for", r"who you are", r"\bskills?\b", r"experience you",
        rf"what you{_APOS}?ll bring", r"what you (bring|need|have)", r"must[- ]have",
        r"nice[- ]to[- ]have", r"basic qual", r"preferred", r"about you", r"competenc",
        r"^experience$", r"^education", r"^required", r"bonus points", r"^you are\b",
        r"minimum qual",
        # A candidate's TOOLING is a requirement, not a section of its own. These
        # anchor to the start so "Technologies we use:" is a requirements header while
        # "technology" inside a company blurb is not.
        r"technical stack", r"tech stack", r"^clearance", r"security clearance",
        r"certification", r"^tools?\b", r"^technolog",
        # Five additions, each measured against a 730-employer corpus as CURRENTLY
        # UNCLASSIFIED rather than guessed at. Counts are entries / employers:
        r"^you (have|bring)\b",                    # 855 / 50
        r"what we are looking for",                # 332 / 50 -- the pattern above is
                                                   # `what we (are|look) for`, which
                                                   # requires "for" to follow directly
        rf"^(bonus|it{_APOS}?s a plus|added plus)",  # 179 / 60 -- `bonus points` was
                                                   # covered, `bonus if:` was not
        rf"who we{_APOS}?re looking for",          # 156 / 22 -- "who", not "what"
        rf"we{_APOS}?d love to hear from you")),   # 94 / 5 -- the weakest of the five
    ("about_company", _p(
        r"about (us|the (team|company|organi))", r"who we are", r"our (mission|story|values|team)",
        r"company overview", r"^why [a-z]+\?*$", r"life at ",
        # A bare `^about [a-z]` is too greedy and needs BOTH guards to be correct, which
        # is why this reads oddly. Ordering handles one case and the lookahead the other:
        # "About You" is a REQUIREMENTS header and is caught above, because requirements
        # now runs first (2,085 entries across 82 employers were misfiled here). "About
        # the Role" is a RESPONSIBILITIES header and ordering cannot save it, because
        # responsibilities runs LAST -- so it is excluded here explicitly and picked up
        # there. Removing either guard silently reclassifies thousands of sections.
        r"^about (?!the role|this role|the job|the position|the opportunity|the work)[a-z]")),
    ("responsibilities", _p(
        r"responsibilit", rf"what you{_APOS}?ll (do|be doing)", r"what you will do",
        r"^the role", r"about (the|this) role",
        r"about the (job|position|opportunity|work)",
        r"your role", r"your impact", r"the impact", r"day[- ]to[- ]day",
        r"in this role", r"^you will\b", r"job summary", r"position summary",
        r"role overview", r"^overview$", r"^summary$", r"what the job", r"duties",
        r"scope of", r"^the (job|opportunity)$", r"job description", r"key activities",
        r"a day in the life")),
]  # fmt: skip

_TAG = re.compile(r"</?[A-Za-z][^>]*>")
# A HEADER is a heading tag OR bold text, and the second half is the load-bearing one:
# 63.6% of the headers this finds come from `<strong>`/`<b>` rather than a heading tag
# (19,359 of 30,421 on the nine-board corpus). Per-BOARD it swings wildly -- databricks
# puts a real `<h*>` in 28% of its postings, four of the nine boards use one in 100% --
# so an extractor that reads only heading tags works perfectly on some employers and
# silently captures nothing on others. That variance is the reason both halves are here.
# The `{0,1000}` bound is not cosmetic. `(.*?)` under re.S makes every UNCLOSED
# `<strong>`/`<b>`/`<h*>` scan to end-of-string looking for a closer, fail, and hand the
# next opener the same doomed scan -- textbook O(n^2). Measured: 16,000 unclosed openers
# in a 158 KB body takes 7.6s, and nothing upstream caps body length. Real postings do
# not do this (the worst imbalance across 6,697 live bodies is ONE unclosed tag, costing
# the same 0.16ms as a well-formed body), so this is insurance rather than a live fix --
# the same argument, and the same posture, as `util._TAG` requiring a letter after the
# `<`. The bound is free because `classify` discards any header over 70 characters
# anyway: real headers run to 739 raw chars at the very most, and at 1000 the extracted
# headers are byte-identical on all 2,697 cached bodies while the pathological case
# drops from 7.6s to 0.117s.
_HDR = re.compile(
    r"<(h[1-6])[^>]*>(.{0,1000}?)</\1>|<(strong|b)[^>]*>(.{0,1000}?)</\3>", re.I | re.S
)
_ENT = (("&nbsp;", " "), ("&amp;", "&"), ("&#39;", "'"), ("&rsquo;", "’"),
        ("&quot;", '"'), ("&lt;", "<"), ("&gt;", ">"), ("&ndash;", "–"),
        ("&mdash;", "—"))  # fmt: skip

# WHERE A BLOCK BEGINS AND ENDS. `_HDR` finding a `<strong>` says nothing about whether
# it is a HEADING -- 0.9.0 promoted every one of them, including emphasis in the middle
# of a sentence, which put 5,753 sections across 2,056 rows under a header like
# "computer vision, deep learning, and generative AI" with a span opening on the word
# "to" [local 94-board harvest, 0.9.0]. A heading starts its block; mid-sentence
# emphasis does not. That distinction needs the block boundaries, so here they are.
#
# The closing-emphasis clause carries a LOOKAHEAD on purpose and is the narrowest thing
# that works. `</strong>` is a boundary ONLY when an opening `<strong>`/`<b>` follows it,
# because two adjacent bold runs are two headings -- a real construct, not a fixture
# artifact: 160 occurrences across 52 of 2,752 live bodies and 9 of 11 vendors, e.g.
# `<h2><strong>Key responsibilities</strong><strong><br></strong></h2>`. Making EVERY
# closing emphasis a boundary also works and costs more: it turns a bolded value into a
# header 174 times on that corpus where this clause does it 94 times, for the same
# section count.
_BOUND = re.compile(
    r"</?(?:p|div|li|ul|ol|h[1-6]|tr|table|section|article|blockquote|dl|dt|dd)\b[^>]*>"
    r"|<br\s*/?>"
    r"|</(?:strong|b|h[1-6])\s*>(?=\s*<(?:strong|b)\b)",
    re.I,
)
# What may sit beside a heading inside its own block without making it prose. A colon
# belongs to the heading; a sentence does not.
_ONLY_PUNCT = re.compile(r"^[\s:\-–—•*.,()\[\]/|]*$")
# BODY-12's whole fix: 275 sections corpus-wide were headed by ".", ":", "," or ">>".
# A header with no letter or digit in it is not a heading under any convention.
_ALNUM = re.compile(r"[0-9A-Za-zÀ-\uffff]")
# A list item is a LEAF -- content, not a container of sections. `<td>`/`<dd>` are
# deliberately absent: they appear in 0 of 910 bodies measured, and an unexercised
# branch in a rule that decides the record contract is worse than a narrower rule.
_LEAF = ("li",)


def _in_leaf(body: str, i: int) -> bool:
    """Is offset `i` inside a list item? Nearest unclosed `<li>` wins."""
    for tag in _LEAF:
        o = max(body.rfind("<" + tag, 0, i), body.rfind("<" + tag.upper(), 0, i))
        c = max(body.rfind("</" + tag, 0, i), body.rfind("</" + tag.upper(), 0, i))
        if o > c:
            return True
    return False


def _header_at(body: str, m: re.Match) -> tuple[str, bool]:
    """One `_HDR` match -> (header text, is it actually a heading).

    A heading is BLOCK-INITIAL: nothing but whitespace and punctuation between it and
    the start of its block. What FOLLOWS it is deliberately not tested, and that is the
    one clause worth defending -- requiring the emphasis to be the whole line reads
    better and is wrong. It deletes the label-value paragraph, which is the commonest
    real section shape here (`<p><strong>Visa sponsorship:</strong> We do sponsor
    visas!` on 487 of 487 postings at one employer) and took `eeo_legal` coverage from
    100% to 0% on three boards when tried. That variant scores BETTER on every
    span-quality metric, because deleting a section satisfies "does this span end
    mid-sentence"; the metric cannot see its own cost.

    Inside a list item the rule is stricter, and the discriminator is MORPHOLOGY rather
    than the container: a heading terminates its label with a colon. `<li><strong>
    Experience:</strong> 3+ years...` is a real heading; `<li><strong>7+ years</strong>
    of software engineering...` is a sentence cut in half. Excluding list items outright
    instead destroys 130 typed headers, 34 of them at a non-tech employer.
    """
    inner_start, inner_end = (
        (m.start(2), m.end(2)) if m.group(1) else (m.start(4), m.end(4))
    )
    inner = body[inner_start:inner_end]
    # The emphasis run may itself span blocks (`<strong><br><br>RESPONSIBILITIES:
    # </strong>` is real). Only its FIRST non-empty chunk can be the heading.
    chunks, last = [], 0
    for b in _BOUND.finditer(inner):
        chunks.append((last, b.start()))
        last = b.end()
    chunks.append((last, len(inner)))
    solid = [(a, z) for a, z in chunks if _detag(inner[a:z])]
    if not solid:
        return "", False
    a, z = solid[0]
    head = _detag(inner[a:z])
    if a > 0:
        before, anchor = "", inner_start + a  # a boundary inside the run resets it
    else:
        # NOT `finditer(body, 0, m.start())`. `endpos` TRUNCATES the string, so the
        # lookahead in `_BOUND`'s closing-emphasis clause cannot see the opener that
        # follows -- and that opener sits at exactly `m.start()`, i.e. the boundary is
        # invisible precisely where it decides the answer. This cost a full
        # reproduction to find and the suite stayed green throughout.
        lo = 0
        for b in _BOUND.finditer(body):
            if b.end() > m.start():
                break
            lo = b.end()
        before, anchor = _detag(body[lo : m.start()]), m.start()
    if not _ONLY_PUNCT.match(before):
        return head, False
    # A trailing punctuation-only run sitting just OUTSIDE the emphasis belongs to the
    # heading: `<strong>The Opportunity</strong>:` otherwise leaves the colon as the
    # first character of the span, which is 205 of the 208 remaining span-opens-on-
    # punctuation cases.
    tail = ""
    outside_colon = False
    if len(solid) == 1:
        nxt = _BOUND.search(body, m.end())
        edge = nxt.start() if nxt else len(body)
        cand = (_detag(inner[z:]) + " " + _detag(body[m.end() : edge])).strip()
        if cand and _ONLY_PUNCT.match(cand):
            tail = cand
        else:
            # A colon terminates the label whether the employer put it inside the tag
            # or outside it, and both spellings are live: `<li><strong>Experience:
            # </strong> 7+ years` and `<li><strong>Strategic Technical Partnership
            # </strong>: Be a technical thought partner`. Reading only the first made
            # the leaf rule below disagree with itself on 149 sections of one shape.
            #
            # It counts for the leaf test but is NOT absorbed into the header, and the
            # reason changed once inline tags stopped leaving a space behind them.
            # Before that, absorbing was impossible: the body read "...Partnership : Be
            # a..." so a header ending in ":" could not be found in its own text, `pos`
            # failed to advance, and the span opened on the colon regardless. That
            # blocker is now gone -- and absorbing it was tried here and REVERTED,
            # because it also swallows the colon in `<strong>PLEASE NOTE</strong>
            # <strong>: Due to federal requirements.</strong>`, where the run after the
            # colon is a real adjacent heading rather than this label's value. Telling
            # those apart needs more than the next character, so the colon stays in the
            # span and this stays a named limit.
            outside_colon = cand.startswith(":")
    if _in_leaf(body, anchor) and not (
        head.rstrip().endswith(":") or tail.startswith(":") or outside_colon
    ):
        return head, False
    if tail:
        head = re.sub(r"\s+([:.,])", r"\1", head + " " + tail).strip()
    if not _ALNUM.search(head):
        return head, False
    return head, True


def _detag(s: str) -> str:
    for a, b in _ENT:
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", _TAG.sub(" ", s or "")).strip()


def classify(header: str) -> str | None:
    """A header string -> one of the fixed types, or None when it is employer prose.

    `None` is the honest answer for much of the tail, and it is a real answer rather
    than a failure: 43% of the 30,145 header entries on the nine-board corpus classify
    to nothing
    ("Monthly family dinner night", "Turn your love of community into a career",
    "Building something special"). Those are marketing copy, not sections, and forcing
    them into a bucket asserts something the employer never said.

    The 70-character ceiling is what keeps a run-on sentence in bold from being read as
    a heading -- one employer's template bolds a whole paragraph.
    """
    h = _detag(header).lower().strip().strip(":").strip()
    if not h or len(h) > 70:
        return None
    for name, pat in SECTION_PATTERNS:
        if pat.search(h):
            return name
    return None


def split(body: str) -> list[tuple[str | None, str, str]]:
    """Decoded HTML -> [(type|None, header_text, section_HTML)] in document order.

    The third element is the RAW HTML slice, deliberately not cleaned here. Its caller
    (`util.clean_with_sections`) has to locate each section inside the cleaned whole
    body, which only works if both went through byte-identical processing -- so the
    caller owns the cleaning and this function owns only the boundaries. Cleaning here
    with a near-miss of that pipeline is what would make every offset lookup fail.

    Returns `[]` when the body carries no headers at all, and that empty list is a
    deliberate change from the module this was ported from, which returned the WHOLE
    BODY as one unclassified entry. That behaviour is invisible on Greenhouse (0.5% of
    the nine-board corpus has no header) and catastrophic everywhere else: eighteen of nineteen
    sources send plain text, so every one of their records would have carried a second
    complete copy of its own description.

    Text before the first header is emitted with an empty header. It is usually the
    intro, and a consumer that wants "the part before the boilerplate" needs it.
    """
    if not body:
        return []
    marks = []
    for m in _HDR.finditer(body):
        if m.group(1):  # a heading TAG is a heading, whatever surrounds it
            head = _detag(m.group(2) or "")
            if head and _ALNUM.search(head):
                marks.append((m.start(), m.end(), head))
            continue
        head, is_heading = _header_at(body, m)
        if not (head and is_heading):
            continue
        # A BOLD LABEL'S BOLD VALUE IS NOT A HEADING. Two adjacent bold runs are two
        # headings often enough to be the rule (`<h2><strong>Key responsibilities
        # </strong><strong><br></strong></h2>`), but an employer that bolds a label AND
        # its value produces `Equity grade:` followed by a section headed `2`, and
        # `Recruiter:` followed by one headed a recruiter's personal name. Two boards,
        # two employers, two unrelated block types (a compensation table and a metadata
        # header), so it is a shape rather than one template.
        #
        # This is not a third rule -- it is the colon rule at the second boundary. Inside
        # a leaf, a bold is a heading only if it terminates its label; at an adjacency, a
        # bold is a heading only if the bold BEFORE it did not. Measured: 94 -> 68 such
        # sections on 2,712 live bodies, with the typed-section count unchanged at 13,925
        # and every other defect metric flat, on both this corpus and a 910-body one that
        # shares no board with it.
        if (
            marks
            and marks[-1][2].rstrip().endswith(":")
            and not body[marks[-1][1] : m.start()].strip()
        ):
            continue
        marks.append((m.start(), m.end(), head))
    if not marks:
        return []
    out: list[tuple[str | None, str, str]] = []
    pre = body[: marks[0][0]]
    # `_detag` here and NOT `pre.strip()`: the text before the first header is raw HTML,
    # so a body that opens with `<p><strong>…` has a non-empty `pre` consisting entirely
    # of a tag. Testing the raw string emitted a spurious empty intro section on every
    # such body -- and since the caller cleans the slice itself, that entry arrived with
    # no text and a zero-length span, looking exactly like a real one.
    if _detag(pre):
        out.append((None, "", pre))
    for i, (_s, e, head) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(body)
        out.append((classify(head), head, body[e:end]))
    return out
