"""De-duplication: an exact company+normalized-title key, a fuzzy secondary pass
(same role re-titled across sources), and ATS-slug extraction from a job URL
(the discovery funnel's input).

The fuzzy pass is gated by DISQUALIFIERS (`same_role`, `job_ref`) before any
string ratio is consulted. A string ratio is positive evidence only -- nothing in
it can argue AGAINST a match -- and that is why "AI Engineer" used to merge with
"AI Engineer, Ads" while "AI Engineer, Payments" stayed separate: the outcome
tracked suffix LENGTH rather than meaning. Record linkage treats a match as a sum
of weighted evidence across fields, INCLUDING evidence that disagrees, so the
level and qualifier marks below can veto a merge outright.

Honest about what this is: a rule-based APPROXIMATION of that shape, not
Fellegi-Sunter. Real F-S fits m/u probabilities from labelled data; this borrows
the structure and still contains a hand-tuned threshold, which is the very thing
the field names as the failure mode. It is a strict improvement on a lone ratio,
not a principled cutpoint.

The bias is deliberate and asymmetric: a false MERGE deletes a role the user
wanted and hides the evidence (the loser's apply URL is discarded, and which copy
survives depends on feed arrival order). A false SPLIT shows a duplicate row --
visible, and fully recoverable. So when the marks disagree, split."""

from __future__ import annotations

import re

from rapidfuzz import fuzz as _rf_fuzz

from . import config

_CORP_SUFFIX = re.compile(r"\b(inc|llc|ltd|corp|co|company|the)\b")
# Work-arrangement / modifier tokens that DON'T distinguish one role from another
# ("AI Engineer" vs "AI Engineer - Remote" is the same role; "AI Engineer" vs
# "AI Engineer, Payments" is not). Stripped only for the fuzzy length comparison,
# so a noise-only difference still merges but a real extra token blocks the merge.
_TITLE_NOISE = frozenset(
    {"remote", "onsite", "hybrid", "wfh", "telecommute", "anywhere", "us", "usa"}
)


# ── disqualifying marks ───────────────────────────────────────────────────────
# LEVEL. Two openings at different levels are two openings, whatever the string
# similarity says. Mapped to a canonical value so the roman, arabic, and spelled
# forms of one level compare equal ("AI Engineer II" vs "AI Engineer 2").
_LEVEL = {
    "i": "L1",
    "1": "L1",
    "ii": "L2",
    "2": "L2",
    "iii": "L3",
    "3": "L3",
    "iv": "L4",
    "4": "L4",
    "jr": "junior",
    "junior": "junior",
    "sr": "senior",
    "senior": "senior",
    "staff": "staff",
    "principal": "principal",
    "lead": "lead",
    "associate": "associate",
}

# Work-arrangement tokens are noise INSIDE a qualifier too: "AI Engineer (Remote)"
# is the same opening as "AI Engineer". Deliberately NOT reusing _TITLE_NOISE
# below -- that set contains "us"/"usa", and dropping those would erase the
# (US)-vs-(EU) distinction, which is the exact case these marks exist to catch.
# Geography discriminates; work arrangement does not.
_QUAL_NOISE = frozenset(
    {"remote", "onsite", "on", "site", "hybrid", "wfh", "telecommute", "anywhere"}
)

# A trailing parenthetical/bracket, or a trailing comma-clause: "(US)", "[EU]",
# ", Ads", ", Payments".
_QUAL_RE = re.compile(r"[(\[]([^)\]]*)[)\]]|,\s*([^,]+)$")


def _marks(title: str) -> tuple[frozenset, frozenset]:
    """(level, qualifier) marks for a RAW title.

    Must run on the raw title: `normalize_title` strips the parentheses and
    commas both marks are read from.

    A qualifier whose text is ITSELF a level word folds into the level set rather
    than counting as a qualifier -- otherwise "Senior AI Engineer" and "AI
    Engineer (Senior)" would disagree on qualifiers and stop merging, when they
    are plainly the same role stated two ways.
    """
    t = (title or "").lower()
    quals = set()
    for m in _QUAL_RE.finditer(t):
        quals.update(re.findall(r"[a-z0-9]+", m.group(1) or m.group(2) or ""))
    levels = {_LEVEL[w] for w in re.findall(r"[a-z0-9]+", t) if w in _LEVEL}
    return frozenset(levels), frozenset(quals - set(_LEVEL) - _QUAL_NOISE)


def same_role(a: str, b: str) -> tuple[bool, str]:
    """Do two RAW titles describe the same opening? `(verdict, reason)`.

    Disqualifiers only -- this never merges anything on its own. It answers "is
    there positive evidence AGAINST a merge", and the caller runs the fuzzy
    string gates afterwards.
    """
    la, qa = _marks(a)
    lb, qb = _marks(b)
    if la != lb:
        return False, "level differs"
    # A qualifier only DISCRIMINATES if the other title doesn't carry the same
    # information somewhere else. Without this, "Engineer, Machine Learning" and
    # "Machine Learning Engineer" -- the same role, comma-inverted, which is a
    # routine cross-source retitle -- would split on a qualifier the other side
    # states in its stem.
    ta = set(re.findall(r"[a-z0-9]+", (a or "").lower()))
    tb = set(re.findall(r"[a-z0-9]+", (b or "").lower()))
    qa, qb = qa - tb, qb - ta
    if (qa or qb) and qa != qb:
        return False, "qualifier differs"
    return True, "no disqualifier"


# The per-posting id inside an ATS URL. Two DIFFERENT ids on the SAME board are
# definitionally two openings -- the strongest signal available here, and one the
# matcher previously ignored entirely. Only ever used to VETO: a URL that doesn't
# resolve (an aggregator redirect) yields None and changes nothing, which is what
# keeps the cross-source merge this tool is built on intact.
_JOB_REF = {
    "greenhouse": re.compile(r"/jobs/(\d+)|[?&]gh_jid=(\d+)"),
    "lever": re.compile(r"jobs\.lever\.co/[^/?#]+/([0-9a-f-]{8,})"),
    "ashby": re.compile(r"jobs\.ashbyhq\.com/[^/?#]+/([0-9a-f-]{8,})"),
    "workable": re.compile(r"apply\.workable\.com/[^/?#]+/j/([a-z0-9]+)"),
    "smartrecruiters": re.compile(r"jobs\.smartrecruiters\.com/[^/?#]+/(\d+)"),
}


# `gh_jid` on ANY host. Greenhouse customers commonly serve the board from their own
# domain -- databricks.com/company/careers/open-positions/job?gh_jid=8559344002 -- and
# ats_from_url only matches boards.greenhouse.io, so job_ref returned None and the
# "two different job ids are two openings" veto could not fire on the biggest boards
# in the corpus. Measured on databricks (810 postings): every residual collision after
# location joined the key was a pair of DISTINCT gh_jids that looked like duplicates
# purely because the parameter was invisible. The parameter is unambiguous wherever it
# appears; the host it appears on is not part of what makes it a job id.
_GH_JID = re.compile(r"[?&]gh_jid=(\d+)")


def job_ref(url: str):
    """`(ats, slug, ref)` when a URL identifies one specific posting on a known
    board, else None. None is the safe answer -- it disables the veto rather than
    guessing."""
    got = ats_from_url(url)
    if not got:
        m = _GH_JID.search(url or "")
        # Slug unknown on a custom domain, and that is fine: `different_openings`
        # only ever compares postings already inside one company block, so the
        # company is established before the ref is consulted.
        return ("greenhouse", "", m.group(1)) if m else None
    rx = _JOB_REF.get(got[0])
    m = rx.search(url.lower()) if rx else None
    if not m:
        return None
    ref = next((g for g in m.groups() if g), None)
    return (got[0], got[1], ref) if ref else None


def different_openings(a: dict, b: dict) -> bool:
    """True when two postings carry different job ids on the SAME board.

    KNOWN LIMIT, deliberate: this vetoes only the FUZZY pass. Two postings whose
    normalized titles are byte-identical produce the same `dedup_key` and merge on
    the exact-key branch before any veto runs, so a company that posts two
    openings under one identical title on one board still collapses to one row.
    Splitting those would mean putting the job id INTO `dedup_key` -- and
    `dedup_key` is the store's primary key, carrying `status` and `first_seen`
    across runs. Re-deriving it would orphan every existing row (applied history
    lost), and suffixing only on collision makes which copy owns the bare key
    depend on feed arrival order, so a later run could reattach an "applied"
    status to the WRONG opening. That is a worse failure than the merge it fixes.
    The residual is bounded: by definition those two rows are indistinguishable to
    the user except by URL, and the merge tiebreak keeps the better-provenance
    copy.
    """
    ra, rb = job_ref(a.get("url", "")), job_ref(b.get("url", ""))
    if not ra or not rb:
        return False
    return ra[:2] == rb[:2] and ra[2] != rb[2]


def _title_core(t: str) -> str:
    """A normalized title with work-arrangement noise tokens dropped (falls back to
    the full title if stripping would empty it)."""
    toks = [w for w in normalize_title(t).split() if w not in _TITLE_NOISE]
    return " ".join(toks) or normalize_title(t)


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def normalize_title(t: str) -> str:
    # Keep seniority (Staff/Senior/Lead are genuinely different roles). Cross-source
    # re-titling of the SAME role is still caught by the fuzzy pass; the fuzzy score
    # of two distinct levels (e.g. "staff … eng" vs "senior … eng") stays below the
    # match threshold, so they no longer collapse into one.
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


# Work-arrangement words carry no PLACE information, so they must not discriminate
# in the key: "Remote - India" and "India" are the same place. Stripped before the
# location joins the key, so a source that decorates its location string does not
# split a role away from a source that does not.
_LOC_NOISE = re.compile(
    r"\b(remote|onsite|on site|hybrid|wfh|telecommute|anywhere|flexible)\b"
)


def normalize_location(loc: str) -> str:
    """A location string -> a comparable key component.

    Only the FIRST place when a posting lists several. Greenhouse separates them
    with `;` ("Berlin, Germany; Munich, Germany") and that is ONE posting with one
    job id -- it must not key differently from the same role elsewhere, and its
    other places are carried in `locations[]` rather than in the key.
    """
    first = (loc or "").split(";")[0]
    first = _LOC_NOISE.sub(" ", first.lower())
    return re.sub(r"[^a-z0-9]+", " ", first).strip()


def dedup_key(p: dict) -> str:
    """`company|title|location` -- the store's primary key.

    LOCATION IS PART OF IT, and that was measured rather than assumed. Without it,
    every opening a company posts under one title in several cities collapses into a
    single row and the rest are DISCARDED along with their apply URLs. On a live
    Greenhouse board (databricks, 810 postings, 2026-08-05): 250 postings dropped, 83
    of them distinct jobs in different cities, each with its own Greenhouse job id --
    "Delivery Solutions Architect" was eight separate openings across Canada, London,
    Brisbane, Seoul, Singapore, Paris, Tokyo and Stockholm, and seven vanished. That
    is the exact failure this module's docstring says the design avoids: a wrong
    merge deletes a job the user wanted and hides the evidence.

    The objection to doing this was that it would break CROSS-SOURCE merging. It does
    not, measured: across RemoteOK, Jobicy, Remotive and Arbeitnow (249 postings)
    there were ZERO collisions on `company|title` to break. And it could not key on
    the structured `city` instead, because that is populated for 0 of those 249 --
    the raw string, normalized, is the only location those boards actually send.

    Re-keying costs nothing: `shortlist.upsert` falls back to matching on URL, so an
    existing row keeps its `status` and `first_seen` through the change (verified).
    """
    ref = job_ref(p.get("url", ""))
    key = (
        norm(p.get("company", ""))
        + "|"
        + normalize_title(p.get("title", ""))
        + "|"
        + normalize_location(p.get("location", ""))
    )
    # THE BOARD'S OWN JOB ID, when the URL carries one. Two different ids on one board
    # are two openings by definition -- the strongest evidence available here, and it
    # was being used only to veto the FUZZY pass while the exact-key branch merged
    # them anyway. On databricks that left 21 postings discarded AFTER location was in
    # the key, every one of them a pair of distinct gh_jids.
    #
    # A URL that carries no id contributes nothing, which is what keeps cross-source
    # merging intact: an aggregator redirect yields no ref and still matches the
    # employer's copy on company|title|location.
    return key + ("|" + ref[2] if ref else "")


def company_block(p: dict) -> str:
    return _CORP_SUFFIX.sub("", norm(p.get("company", ""))).strip()


def fuzzy_title_match(a: str, b: str, cfg=None) -> bool:
    cfg = cfg or config.active()
    if not a or not b:
        return False
    # Both gates must clear: token_set_ratio catches reordered/re-punctuated
    # retitles, and token_sort_ratio (length-sensitive, on the noise-stripped core)
    # rejects a bare-subset title merging into a longer, more-specific one — while
    # still merging when the only difference is a modifier like "Remote". See
    # fuzzy_title_sort_floor and _TITLE_NOISE.
    if _rf_fuzz.token_set_ratio(a, b) < cfg.fuzzy_title_threshold:
        return False
    return (
        _rf_fuzz.token_sort_ratio(_title_core(a), _title_core(b))
        >= cfg.fuzzy_title_sort_floor
    )


def find_hit_key(p: dict, hits: dict, blocks: dict, cfg=None):
    """Resolve p to an existing hit and hand back the values computed doing so.

    Returns `(match, key, blk, nt)`:
      * `match` — the existing key this posting dedups into, or None if it's new
      * `key` / `blk` / `nt` — p's dedup_key / company_block / normalized title,
        computed ONCE here so the caller reuses them on insert instead of
        re-deriving all three (they used to be recomputed engine-side — the
        redundant 2–4×-per-posting work this eliminates).

    `blocks` is a company-block index (block -> [key]) so the fuzzy pass only
    compares against hits in the SAME company, not the whole set — turning an
    O(n) scan per posting into O(hits-in-this-company). The compared hits carry
    their normalized title precomputed on insert (`_nt`), so nothing is re-derived
    inside the loop. Together this keeps de-dup linear instead of O(n²)."""
    key = dedup_key(p)
    blk = company_block(p)
    nt = normalize_title(p.get("title", ""))
    if key in hits:
        return key, key, blk, nt
    if blk:
        for k in blocks.get(blk, ()):
            cur = hits.get(k)
            if cur is None:
                continue
            # DISQUALIFIERS FIRST, on the RAW title and URL (both already on every
            # hit dict, so nothing extra is stashed). The fuzzy ratios are positive
            # evidence only; these are the evidence that can argue against.
            if different_openings(p, cur):
                continue
            if not same_role(p.get("title", ""), cur.get("title", ""))[0]:
                continue
            if fuzzy_title_match(nt, cur.get("_nt", ""), cfg):
                return k, key, blk, nt
    return None, key, blk, nt


def ats_from_url(url: str):
    """Map a job/apply URL to (ats, slug) when it points at a known ATS host."""
    if not url:
        return None
    u = url.lower()
    # `&` is excluded alongside /?# — the greenhouse embed form consumes the '?'
    # itself (embed/job_app?for=SLUG&token=...), so without it the capture ran on
    # through the query string and produced slugs like
    # 'gemini&token=7743177&gh_jid=7743177'. Those merely probe as 404s, so the
    # symptom was a company quietly looking unresolvable rather than a wrong board —
    # invisible until parsed slugs were compared against real resolutions.
    patterns = [
        (
            r"(?:job-)?boards(?:\.eu)?\.greenhouse\.io/(?:embed/job_app\?for=)?([^/?#&]+)",
            "greenhouse",
        ),
        (r"jobs\.lever\.co/([^/?#&]+)", "lever"),
        (r"jobs\.ashbyhq\.com/([^/?#&]+)", "ashby"),
        (r"apply\.workable\.com/([^/?#&]+)", "workable"),
        (r"jobs\.smartrecruiters\.com/([^/?#&]+)", "smartrecruiters"),
    ]
    for rx, ats in patterns:
        m = re.search(rx, u)
        if m and m.group(1) not in ("embed", "j"):
            return (ats, m.group(1))
    return None
