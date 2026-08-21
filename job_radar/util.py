"""Shared helpers: HTTP, text cleaning, dates, salary parsing, word matching."""

from __future__ import annotations

import contextlib
import html
import http.client
import json
import os
import re
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import config
from . import sections


def atomic_write_text(path, text: str, encoding: str = "utf-8") -> None:
    """Write `text` to `path` atomically: a UNIQUE temp file (mkstemp) in the same
    dir, then os.replace. Unique so two overlapping runs can't collide on a fixed
    `.tmp` name and replace a half-written or foreign file; the replace itself is
    atomic so an interrupted write leaves the prior file intact."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=p.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as f:
            f.write(text)
        os.replace(tmp, p)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


# Expected transient fetch failures (network down, timeout, a source returning
# non-JSON). Catching THESE and moving on is correct; catching everything hides a
# real schema-break bug (KeyError/AttributeError) as if the source had no jobs.
NET_ERRORS = (urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeError)

# All dates are Eastern Time (this is a US-centric job tool; naive local time gave
# off-by-one ages near the day boundary depending on the machine's zone).
_ET = ZoneInfo("America/New_York")


# ── the HTTP layer ────────────────────────────────────────────────────────────
# A scan is ~500 companies concentrated onto a handful of ATS hosts, and urllib
# opens a fresh TCP + TLS handshake for every single one. Measured: 149ms per
# request cold vs 84ms on a reused connection -- so most of a scan's wall clock
# was spent re-introducing ourselves to servers we had just finished talking to.
#
# THREAD-LOCAL, never shared. Connection reuse across a worker pool is the classic
# source of interleaved-response corruption; a per-thread cache makes that
# structurally impossible rather than merely unlikely. The pool is capped so a
# long run can't accumulate descriptors.
#
# The contract above this layer is preserved EXACTLY, which is most of the work
# here and all of the risk: callers catch `urllib.error.HTTPError` to read
# `.code`, and NET_ERRORS catches `URLError`/`TimeoutError` to mean "this source
# is having a bad day, move on". http.client raises neither, so its failures are
# translated rather than allowed to escape as a new exception type that every
# `except NET_ERRORS` in the codebase would miss. Redirects are followed for the
# same reason: urllib followed them, so dropping that would silently break any
# source whose API redirects.
_POOL = threading.local()
_POOL_MAX = 8


def _conn(scheme: str, host: str, timeout: int):
    cache = getattr(_POOL, "conns", None)
    if cache is None:
        cache = _POOL.conns = {}
    c = cache.get((scheme, host))
    if c is None:
        if len(cache) >= _POOL_MAX:  # evict the oldest rather than grow forever
            _, old = cache.popitem()
            with contextlib.suppress(Exception):
                old.close()
        cls = (
            http.client.HTTPSConnection
            if scheme == "https"
            else http.client.HTTPConnection
        )
        c = cache[(scheme, host)] = cls(host, timeout=timeout)
    return c


def _drop(scheme: str, host: str) -> None:
    c = getattr(_POOL, "conns", {}).pop((scheme, host), None)
    if c is not None:
        with contextlib.suppress(Exception):
            c.close()


def _request(method: str, url: str, body=None, headers=None, _hops: int = 5) -> bytes:
    cfg = config.active()
    parts = urllib.parse.urlsplit(url)
    scheme, host = (parts.scheme or "https"), parts.netloc
    target = urllib.parse.urlunsplit(("", "", parts.path or "/", parts.query, ""))
    hdrs = {"User-Agent": cfg.user_agent, **(headers or {})}
    for attempt in (0, 1):
        conn = _conn(scheme, host, cfg.timeout)
        try:
            conn.request(method, target, body=body, headers=hdrs)
            resp = conn.getresponse()
            data = resp.read()  # MUST drain before the connection can be reused
            break
        except (OSError, http.client.HTTPException) as e:
            # A keep-alive socket the server closed while idle looks exactly like
            # a network failure on first use. Drop it and try once on a fresh
            # connection, so pooling degrades to the old behaviour instead of
            # inventing outages.
            _drop(scheme, host)
            if attempt == 0:
                continue
            raise urllib.error.URLError(e) from e
    if resp.status in (301, 302, 303, 307, 308) and _hops:
        loc = resp.getheader("Location")
        if loc:
            keep = resp.status in (307, 308)
            return _request(
                method if keep else "GET",
                urllib.parse.urljoin(url, loc),
                body if keep else None,
                headers,
                _hops - 1,
            )
    if resp.status >= 400:
        raise urllib.error.HTTPError(url, resp.status, resp.reason, resp.headers, None)
    return data


def get_json(url: str):
    return json.loads(_request("GET", url).decode("utf-8", "replace"))


def post_json(url: str, payload: dict):
    """POST a JSON body and decode the JSON response.

    Needed because a few ATS read-APIs are POST-only — Workday's CxS job-search
    endpoint takes its paging/facet parameters in the body rather than the query
    string. Same UA and timeout policy as get_json: we identify ourselves honestly
    (verified 2026-07-22 that Workday serves job-radar's own User-Agent exactly as
    it serves a browser's, so there is never a reason to spoof one).
    """
    data = _request(
        "POST",
        url,
        body=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    return json.loads(data.decode("utf-8", "replace"))


def q(s: str) -> str:
    return urllib.parse.quote(s)


# A TAG, not "any angle bracket with something between it" -- INSURANCE, and stated as
# insurance rather than as a live fix. The obvious `<[^>]+>` produces byte-identical
# output to this on all 2,697 measured Greenhouse bodies, so nothing is broken today.
# What makes it worth changing anyway: 12 of those bodies carry an inner literal `<`
# once entities are decoded ("travel as needed (<25%) ... Bachelor's degree"), and 0 of
# them happen to have a literal `>` later in the text. The day one does, the loose
# pattern silently deletes the whole clause between them. Requiring a letter (or `/`)
# after the `<` makes that impossible instead of merely unobserved -- which is also what
# lets the second strip below be safe.
_TAG = re.compile(r"</?[A-Za-z][^>]*>")
# AN INLINE TAG NEVER SEPARATED TWO WORDS, so it must not leave a space where it stood.
# `_TAG.sub(" ", ...)` replaced EVERY tag with a space, which is right for a block tag
# and wrong for a bold or a link: `At <a>Smartsheet</a>, your ideas` became
# `At Smartsheet , your ideas`, and a tag boundary falling mid-word split "the" into
# "t he". Measured across 2,712 live bodies from 9 boards: 4,580 spaces sitting before a
# punctuation mark, on 67.7% of rows with a body.
#
# THE UPPERCASE GUARD IS THE WHOLE RULE. Deleting an inline run unconditionally would
# glue two real words together -- `<strong>Requirements</strong>Must have` -> the single
# token `RequirementsMust`. A word split by a tag always CONTINUES in lower case, and two
# distinct words do not, so an uppercase letter after the run means "these were two
# words, keep the space". Measured: 4,580 -> 62 space-before-punctuation with zero words
# glued, against a camel-case proxy that stayed at exactly 3,099 either way.
#
# THE `(?i:)` IS SCOPED ON PURPOSE AND MUST STAY THAT WAY. A module-level `re.I` here
# case-folds the `(?![A-Z])` lookahead too, so it rejects lower case as well, the guard
# silently becomes a no-op, and the rule reverts to the gluing version -- with every test
# still green. Two of us hit this independently and it cost four measurements between us.
_INLINE_RUN = re.compile(
    r"(?:(?i:</?(?:a|b|strong|em|i|u|span|code|sup|sub|small|abbr|cite|q|mark|s|del|ins|font)\b[^>]*>))+(?![A-Z])"
)
# Block-level closers become line breaks. NOT because the source runs its bullets
# together -- 2,554 of 2,697 real bodies already put a literal newline between `</li>`
# and `<li>`, and the `\s*\n\s*` collapse at the end of clean() is what recovers those.
# (0.8.2's `\s+ -> " "` is what destroyed them.) This pattern earns its place on the
# boundaries the source does NOT separate: a heading butted straight against the prose
# before it, `...beneficial AI systems.</p>About the Role`. Measured contribution: +6%
# more line breaks, on 1,744 of 2,697 bodies.
# `<br>` is here because it is the most common line-break element in job prose (7,610
# occurrences across 1,495 of 2,697 bodies) and it is not a closer, so the `</...>` half
# of the pattern misses it.
# Verified free either way -- against 809 postings the fit score, the salary parse and
# the remote verdict are identical whether these become spaces or newlines, and there is
# no CSV round-trip to worry about because the store has no body column.
_BLOCK_END = re.compile(
    r"</(li|p|h[1-6]|div|tr|ul|ol|table|section)\s*>|<br\s*/?>", re.I
)


def clean(raw: str) -> str:
    """Vendor description (HTML, escaped HTML, or plain text) -> readable plain text.

    THE ORDER IS THE WHOLE FUNCTION. This stripped tags and then unescaped entities,
    which is backwards for any source that sends HTML-ESCAPED HTML: the strip finds no
    `<...>` to remove, and the unescape then turns `&lt;div&gt;` into `<div>`. The
    function whose job is to remove markup was manufacturing it.

    That was not a rare edge. Greenhouse escapes every body -- 2,697 of 2,697 across
    nine employers, 116,214 live tags left behind on one board alone -- and it is 65% of
    a typical harvest. themuse, arbeitnow, hn, workday and remotive each do it on a
    minority of postings too, which is why this is fixed here rather than in one adapter.
    The cost was not only cosmetic: `salary_from_text` matched 0 of 809 Greenhouse
    postings because the pay figures sat inside tags, and 424 of 809 after this fix.

    `catalog/greenhouse.md` has said "Unescape, THEN strip; the other order is silently
    wrong" since 2026-08-03. The repo documented the correct order and the code did not
    follow it.
    """
    if not raw:
        return ""
    return _clean_decoded(html.unescape(raw))


def _clean_decoded(txt: str) -> str:
    """`clean` minus the first unescape -- everything that happens once the body is
    real HTML rather than escaped HTML.

    Split out so `clean_with_sections` can clean a SECTION of a body with the exact
    transformation that produced the whole, then find one inside the other. Any
    divergence between the two makes the lookup fail, so there must not be two of them.

    Routing sections back through `clean` instead would double-decode -- a literal
    `&amp;` in the prose would become `&` on the second pass -- but be honest about the
    weight of that: measured on 26,742 live sections it changes NOTHING, 0 lookups
    fail either way, and no test pins it. This split is insurance against a shape that
    has not appeared yet, not a fix for one that has.
    """
    txt = _BLOCK_END.sub("\n", txt)
    txt = _INLINE_RUN.sub("", txt)
    txt = _TAG.sub(" ", txt)
    # A SECOND pass, because one is not enough. Greenhouse's escaped markup contains its
    # own entities, so an `&amp;amp;` in the source needs two decodes to reach `&` --
    # load-bearing on 2,507 of 2,697 bodies. The trade is that a source sending literal
    # `&amp;` as prose gets it decoded too (3 bodies of ~3,300 across the clean sources).
    txt = html.unescape(txt)
    # And a second strip, because the decode above can expose markup that was escaped one
    # level deeper. It fires on 0 of 2,697 bodies today and NO TEST PINS IT -- removing it
    # leaves the suite green. It is here because that is the exact shape of the bug this
    # function just had, and under the strict pattern above it cannot damage prose. A
    # fixed-point loop was measured and rejected: unbounded decoding buys nothing over two
    # passes and is only safe by luck.
    # The inline pass is repeated here for the same reason and under the same guard.
    txt = _INLINE_RUN.sub("", txt)
    txt = _TAG.sub(" ", txt)
    # `[^\S\n]+`, not `[ \t]+`: 0.8.2's `\s+` collapsed a non-breaking space and this
    # replaced it with a class that does not, leaving a literal U+00A0 on 67% of bodies --
    # enough to lose the multi-token keyword "prompt engineering" on a real posting. This
    # is every whitespace character EXCEPT the newline we just went to the trouble of
    # creating, so it also covers a lone \r and U+2007.
    txt = re.sub(r"[^\S\n]+", " ", txt)
    # Collapse around the newlines too, not just between words: the OPENING tag of every
    # bullet still becomes a space, so without this every line begins with one.
    return re.sub(r"\s*\n\s*", "\n", txt).strip()


def clean_with_sections(raw: str) -> tuple[str, list[dict]]:
    """Vendor description -> (plain text, labelled sections with offsets INTO that text).

    THE ORDER IS THE POINT. Section headers exist only in the decoded markup, and the
    strip below removes them -- correctly. So the split runs on the decoded HTML, before
    the strip, and what survives into the record is a set of spans rather than the tags.
    After `clean` returns there is no way back without re-fetching the posting.

    Offsets, not copies. Carrying each section's own text would grow a record by 104%;
    offsets into `text` cost 14.3% and lose nothing, because `text` ships in the same
    record. (A bare header list with no boundaries would have cost 9.8% -- the 4.5-point
    difference is what buys a consumer the ability to actually read a section.)

    A section whose text cannot be located emits NO offsets rather than wrong ones, and
    that is the only self-check this design has. `find` returning -1 means `split` and
    `clean` disagreed about the same bytes; the honest response is to say where the
    section is unknown, not to guess. The alternative shape -- cleaning and tracking
    positions in a single pass -- was prototyped and produced text that did not match
    `clean(raw)` on every body tested, silently, with nothing able to detect it.

    The `<` guard is on the DECODED string, never on `raw`. Greenhouse escapes its
    markup, so `"<" in raw` is False on 100% of its bodies and would skip the one source
    this exists for.
    """
    if not raw:
        return "", []
    decoded = html.unescape(raw)
    text = _clean_decoded(decoded)
    if "<" not in decoded:
        return text, []
    out: list[dict] = []
    pos = 0
    for kind, header, seg_html in sections.split(decoded):
        seg = _clean_decoded(seg_html) if seg_html else ""
        entry: dict = {"type": kind, "header": header or None}
        # STEP PAST THE HEADER FIRST. `pos` used to advance only over section BODIES,
        # which left every header sitting inside the next search window -- so a section
        # whose opening words also appear in the header above it matched there instead.
        # Measured on 6,697 real bodies: 14 sections of 63,209 got a span pointing into
        # their own header ("Client Leadership" -> the "Lead" inside "Leadership"), and
        # every one of the 4,516 zero-length spans anchored BEFORE its header rather
        # than after it. `text[start:end]` still returned the right STRING in all 14 --
        # only the position was wrong, which is worse, because a consumer highlighting
        # a span or mapping an offset back to a section has no way to detect it.
        # A header that cannot be located leaves `pos` untouched, so this can only
        # narrow the window, never skip past real body text. That happens on 7 of 30,145
        # headers and the cause is known and singular: a `<br>` INSIDE a header. The
        # splitter returns the header through `sections._detag`, which collapses all
        # whitespace to spaces, while `_clean_decoded` turns that same `<br>` into a
        # newline -- so `<strong>Key<br/>Duties</strong>` yields the header "Key Duties"
        # against a body reading "Key\nDuties", and `find` misses. Those 7 sections fall
        # back to the pre-fix behaviour; none of them produces a bad span today. Making
        # the lookup whitespace-flexible would fix it and is not worth a regex per
        # header for 0.02% of them.
        if header:
            hpos = text.find(header, pos)
            if hpos >= 0:
                pos = hpos + len(header)
        if not seg:
            # A header with NOTHING under it -- one bold line immediately followed by
            # the next, which is 4,516 of 31,258 entries on the nine-board corpus. That
            # is an empty section, not a failed lookup, so it gets a zero-length span at
            # the right place. Giving it absent offsets instead would make it look
            # identical to a real disagreement and destroy the only signal this design
            # has.
            entry["start"] = entry["end"] = pos
            out.append(entry)
            continue
        # Forward-moving: a section is searched for only AFTER the previous one ended
        # (and after its own header, above), so a heading whose words repeat later in
        # the posting cannot capture the wrong span, and the spans come out
        # non-overlapping and in document order.
        idx = text.find(seg, pos)
        if idx >= 0:
            entry["start"] = idx
            entry["end"] = idx + len(seg)
            pos = entry["end"]
        out.append(entry)
    return text, out


# A date is the ONLY thing to_date may return. Sixteen adapters route third-party
# strings through it into the `posted` column, so an unvalidated passthrough is a
# direct channel from any of ~500 job boards into the user's spreadsheet.
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def to_date(val) -> str:
    """Normalize an ISO string / epoch-seconds / epoch-ms to YYYY-MM-DD.

    Anything that is not date-shaped returns "" rather than being passed through.
    This used to `return str(val)[:10]` for any string, which meant a board could
    put its own 10 characters in `posted` -- and `posted` is not a free-text column,
    so it bypassed the CSV formula-injection guard entirely. Blanking an unparseable
    date is also the safer failure: a blank sinks the role in the freshness filter
    instead of quietly presenting a vendor's arbitrary text as a date.
    """
    if not val:
        return ""
    out = ""
    if isinstance(val, (int, float)):
        ts = val / 1000 if val > 1e11 else val
        try:
            out = datetime.fromtimestamp(ts, tz=_ET).strftime("%Y-%m-%d")
        except Exception:
            return ""
    else:
        s = str(val)
        # A TIMESTAMP CARRYING AN OFFSET IS AN INSTANT, and an instant falls on a
        # different calendar day in different zones. Converting is what the epoch
        # branch above already does; this branch used to `s[:10]` instead, which kept
        # the VENDOR's day and put two conventions in one column. Measured against
        # live vendor APIs on 3,732 corpus rows: ashby 16.3% of dates wrong, hn 10.5%,
        # greenhouse 0.00%, lever 0.00% -- the two correct ones being the ones that
        # already send an ET offset or an epoch. Always one day too NEW, so the role
        # also dodged the staleness penalty. `CLAUDE.md` requires ET everywhere; this
        # was the one place enforcing it for epochs and abandoning it for strings.
        #
        # A DATE-ONLY OR ZONE-LESS STRING IS NOT AN INSTANT -- it is already somebody's
        # calendar day, and shifting it would invent a timezone nobody stated. So the
        # offset test looks at `s[10:]` and never the whole string, because
        # "2026-05-22" is itself full of hyphens. Measured on 8,714 live values across
        # 12 sources: 0 changed on greenhouse/lever/himalayas/remotive/arbeitnow, and
        # the sources that DO move land at 14-20%, which is the ~16.7% a uniform
        # 4-hour window predicts.
        #
        # `.astimezone()` raises OverflowError -- NOT ValueError -- at the edge of the
        # datetime range, and "0001-01-01T00:00:00Z" (the .NET/Go zero value) is
        # sitting in this repo's own `catalog/_raw/4dayweek.json`. Today it truncates
        # harmlessly; an uncaught raise here would take out a whole harvest, which is
        # the failure class `_coerce`'s docstring exists to remember.
        #
        # PYTHON 3.10 IS IN THE CI MATRIX and its `fromisoformat` is far narrower: a
        # .NET 7-digit fractional second, a basic-format stamp, and a colon-less or
        # hour-only offset all raise there and fall back to truncation -- i.e. to
        # today's behaviour, never to a new wrong answer. That is a deliberate,
        # accepted split, not an oversight; usajobs is a .NET API and is the source
        # most likely to expose it.
        tail = s[10:]
        if "+" in tail or "-" in tail or "Z" in tail or "z" in tail:
            try:
                out = (
                    datetime.fromisoformat(s.replace("Z", "+00:00").replace("z", "+00:00"))
                    .astimezone(_ET)
                    .strftime("%Y-%m-%d")
                )
            except (ValueError, OverflowError, OSError):
                out = ""
        if not out:
            out = s[:10]
    # ONE VALIDATED EXIT. Every path lands here rather than returning its own string,
    # so the "a date is the ONLY thing to_date may return" contract above is checked
    # rather than asserted by whichever branch happened to produce the value.
    return out if _ISO_DATE_RE.match(out) else ""


_SAL_RE = re.compile(
    r"\$\s?\d{2,3}(?:,\d{3})?\s?[kK]?\s?(?:[-–—]|to)\s?\$?\s?\d{2,3}(?:,\d{3})?\s?[kK]?"
)
# A range is only a SALARY if it reads like pay: it carries a k / thousands, or is
# immediately followed by a per-period unit. A magnitude word right after (million/
# billion/M/B) means it's funding or revenue, not comp — reject it.
_SAL_MAGNITUDE = re.compile(r"^\s*(?:million|billion|mn|bn|[mb])\b", re.I)
_SAL_UNIT = re.compile(
    r"^\s*(?:/|per\b)?\s*(?:hr|hour|yr|year|annum|annually|wk|week|mo|month|k\b)",
    re.I,
)


def salary_from_text(text: str) -> str:
    m = _SAL_RE.search(text or "")
    if not m:
        return ""
    matched = m.group(0)
    tail = (text or "")[m.end() :]
    if _SAL_MAGNITUDE.match(tail):  # "$20-40 million in Series B" -> funding
        return ""
    has_anchor = "k" in matched.lower() or "," in matched
    if not has_anchor and not _SAL_UNIT.match(tail):  # bare "$20-40" -> too ambiguous
        return ""
    return re.sub(r"\s+", " ", matched).strip()


def salary_range(lo, hi) -> str:
    try:
        lo = int(float(lo or 0))
        hi = int(float(hi or 0))
    except Exception:
        return ""
    if lo and hi:
        # A POINT VALUE IS NOT A RANGE. Adzuna's model predictions are point
        # estimates (min == max to two decimals) and this rendered every one of them
        # as `$188,569–$188,569` -- 220 of 220 predicted rows in `_reports/flat.ndjson`
        # [local 94-board harvest, 0.9.0, 2026-08-20]. A reader sees a range and reads
        # a precise employer offer; the only tell was the cents in the underlying
        # value, which this formatting rounds away. `_adzuna_pay` is careful to keep
        # the prediction out of the commitment columns, and then the display string
        # gave it back the appearance of one.
        return f"${lo:,}" if lo == hi else f"${lo:,}–${hi:,}"
    if lo or hi:
        return f"${(lo or hi):,}"
    return ""


# The character class the whole-word boundary is defined against. A frozenset
# membership test is what makes the str.find loop below beat the regex.
_WORDCHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789")


def has(kw: str, text: str) -> bool:
    """Whole-word match, CASE-SENSITIVE: 'ai' hits 'ai' but not 'training' /
    'available'. Callers lowercase both the keyword and the text first (keyword
    lists are lowercase; the scored blob is `.lower()`-ed), so this never uppercases.

    `str.find` in a loop, NOT a lookaround regex, and the difference is not academic:
    the previous `(?<![a-z0-9])kw(?![a-z0-9])` scanned a ~4.8 KB blob once per
    keyword and `re.Pattern.search` measured 46% of whole-corpus `_consume` time.
    Replacing it took `_present` from 122.5 to 6.5 us/posting (18.8x) and the whole
    30,000-posting consume from 3.378s to 1.781s (1.90x), with byte-identical output.

    The semantics are IDENTICAL, not merely similar, which is the only reason this is
    a safe trade: every keyword is a `re.escape`d literal (no patterns), both sides
    are case-sensitive, and a lookaround asserting "not an alnum" is exactly a check
    of the character on either side. Checking those two characters directly does the
    same work without a scan.

    An n-gram token-set alternative was measured and is SLOWER (142 us/posting):
    building the grams in Python costs more than the C-level regex it replaces.
    """
    # No empty-keyword short-circuit: the lookaround regex this replaces DOES match
    # an empty keyword, at any position whose neighbours are not alnum, and the loop
    # below reproduces that exactly. Special-casing it disagreed on 3,701 of 40,000
    # randomized cases -- all of them empty-keyword -- which is how it was caught.
    n, start = len(kw), 0
    while True:
        i = text.find(kw, start)
        if i < 0:
            return False
        if (i == 0 or text[i - 1] not in _WORDCHARS) and (
            i + n == len(text) or text[i + n] not in _WORDCHARS
        ):
            return True
        start = i + 1


def today_et() -> str:
    """Today's date (YYYY-MM-DD) in Eastern Time — the tool's single zone, so
    first_seen can't sit off-by-one from age_int's ET-based math near midnight."""
    return datetime.now(_ET).strftime("%Y-%m-%d")


def posted_from(value) -> dict:
    """An absolute vendor timestamp -> `{posted, posted_basis}`, produced TOGETHER.

    The basis is a property of HOW the date was derived, not of the record, and that
    knowledge exists in exactly one place: which helper the adapter called. Emitting
    both here is what makes them impossible to drift apart -- there is no way to
    change the date and forget the label, and a new adapter gets the basis free.

    Deliberately NOT a default applied at the engine boundary. `_coerce` sees a date
    string and cannot tell an ISO timestamp from arithmetic on "30+ days ago", so a
    default of "stated" there would be right for sixteen adapters and an invisible
    lie for the two that derive -- the same shape as defaulting seniority to "mid".
    An adapter that calls neither helper leaves the basis None, which is honestly
    unknown rather than falsely confident.

    HONEST LIMIT: "stated" means the vendor sent us a date, NOT that the date is
    correct. Greenhouse's `updated_at` was a real ISO timestamp and still the wrong
    field -- a bulk-touch stamp that made every posting look a day old. The basis
    describes derivation, never truthfulness.
    """
    d = to_date(value)
    return {"posted": d, "posted_basis": "stated" if d else None}


def now_et() -> str:
    """Now, as an ET timestamp. Stamped on every harvested row.

    It is the ANCHOR a relative date needs. Workday sends "Posted 26 Days Ago" and
    Google sends "3 days ago"; the absolute date we compute from those only means
    something relative to WHEN it was computed. Store a row and read it a week later
    and, without this, nothing in it says the arithmetic was done last Tuesday.
    """
    return datetime.now(_ET).strftime("%Y-%m-%dT%H:%M:%S")


def age_int(posted: str):
    if not posted:
        return None
    try:
        d = datetime.strptime(posted[:10], "%Y-%m-%d").replace(tzinfo=_ET)
        return (datetime.now(_ET) - d).days
    except Exception:
        return None
