"""Source attribution: who must be credited, and in what words.

Four of the wired sources GRANT access on a stated condition rather than staying
silent, and the condition is always some form of "link back and name us". Those are
contract terms, quoted from each vendor's own page and dated in `catalog/`, not a
courtesy:

    Remote OK   "Please link back (with follow, and without nofollow!) to the URL on
                 Remote OK and mention Remote OK as a source... If you do not we'll
                 have to suspend API access."
    Remotive    "Please link back to the URL found on Remotive AND mention Remotive
                 as a source... If you don't do that, we'll terminate your API
                 access, sorry!"
    Himalayas   "If you display Himalayas job data on your own website or
                 application, include a visible link back to himalayas.app and
                 mention that the data is sourced from Himalayas."
    Arbeitnow   "You also agree to providing a link back to Arbeitnow.com on your
                 platform."
    Adzuna      each displayed advert labelled "Jobs by Adzuna", logo >= 116x23 px,
                with "Jobs" hyperlinked to the local Adzuna domain.

WHY THIS IS A MODULE AND NOT A README LINE. A library cannot discharge a display
obligation on behalf of the thing that does the displaying. job-radar prints rows in
a terminal and writes a CSV -- it credits its sources there, and that part it can
honour itself. But the "platform" these terms mean is whatever ends up showing a job
to a person, which for this package is usually a downstream consumer. So the
obligation has to travel WITH THE DATA: `emit.manifest` carries the attribution for
every source that contributed to a run, which is what lets a consumer render it
correctly instead of having to re-read five terms-of-service pages.

`row_link_suffices` is the distinction that decides what a consumer must build. Every
one of these terms asks for a link to the JOB's url, which the record already carries
in `url` -- that half is satisfied structurally by the product's whole design. What is
never satisfied automatically is naming the source, and for Adzuna the branded label.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Attribution:
    source: str
    name: str  # the credit MUST use this name -- vendor's own capitalization
    url: str  # where the link back must point
    requirement: str  # what a display surface has to do, in plain words
    terms_url: str
    read_at: str  # when the terms were last read, per catalog/
    # True when linking the row's own apply url + naming the source is the whole
    # obligation. False means a display surface has extra work (Adzuna's branded
    # label and logo), which no library can do for it.
    row_link_suffices: bool = True


# Keyed by the `source` value that lands on every record, so a consumer can join
# straight from a row without a lookup table of its own.
ATTRIBUTION: dict[str, Attribution] = {
    "remoteok": Attribution(
        source="remoteok",
        name="Remote OK",
        url="https://remoteok.com",
        requirement=(
            "Link back to the job's Remote OK URL and name Remote OK as the source. "
            "The link must be followable — explicitly NOT rel=nofollow. Do not use "
            "the Remote OK logo (a registered trademark); the name is fine."
        ),
        terms_url="https://remoteok.com/api",
        read_at="2026-08-03",
    ),
    "remotive": Attribution(
        source="remotive",
        name="Remotive",
        url="https://remotive.com",
        requirement=(
            "Link back to the job's Remotive URL and name Remotive as the source. "
            "Remotive also forbids submitting its jobs to third-party job sites "
            "(Google Jobs, LinkedIn Jobs, Jooble, and the like) — a redistribution "
            "limit, separate from attribution, that binds any consumer."
        ),
        terms_url="https://remotive.com/api-documentation",
        read_at="2026-08-03",
    ),
    "himalayas": Attribution(
        source="himalayas",
        name="Himalayas",
        url="https://himalayas.app",
        requirement=(
            "Include a visible link back to himalayas.app and state that the data is "
            "sourced from Himalayas."
        ),
        terms_url="https://himalayas.app/docs/remote-jobs-api",
        read_at="2026-08-03",
    ),
    "arbeitnow": Attribution(
        source="arbeitnow",
        name="Arbeitnow",
        url="https://www.arbeitnow.com",
        requirement=(
            "Provide a link back to Arbeitnow.com. Arbeitnow is the one source here "
            "whose access is an explicit, revocable grant rather than silence, and "
            "this link is the condition attached to it."
        ),
        terms_url="https://www.arbeitnow.com/terms",
        read_at="2026-08-03",
    ),
    "adzuna": Attribution(
        source="adzuna",
        name="Adzuna",
        url="https://www.adzuna.com",
        requirement=(
            'Label each displayed advert "Jobs by Adzuna", with the Adzuna logo at '
            'least 116x23 px and the word "Jobs" hyperlinked to the relevant local '
            'Adzuna domain. Research or salary use must cite "The Adzuna API". This '
            "one needs a branded label, so a text credit alone does not satisfy it."
        ),
        terms_url="https://developer.adzuna.com/docs/terms_of_service",
        read_at="2026-08-03",
        row_link_suffices=False,
    ),
}


def for_sources(names) -> list[Attribution]:
    """Attributions owed for the sources that actually contributed, sorted by name.

    Only what a run really used: crediting a source that returned nothing would be
    noise, and noise is how a credit line gets ignored.
    """
    seen = {n for n in names if n in ATTRIBUTION}
    return sorted((ATTRIBUTION[n] for n in seen), key=lambda a: a.name)


def as_dicts(names) -> list[dict]:
    """The manifest form — the obligation travelling with the data."""
    return [
        {
            "source": a.source,
            "name": a.name,
            "url": a.url,
            "requirement": a.requirement,
            "terms_url": a.terms_url,
            "terms_read_at": a.read_at,
            "row_link_suffices": a.row_link_suffices,
        }
        for a in for_sources(names)
    ]


def credit_line(names) -> str:
    """One human-readable line for a terminal or a footer. Empty when nothing is
    owed, so a caller can print it unconditionally without an empty label."""
    owed = for_sources(names)
    if not owed:
        return ""
    return "Jobs from " + ", ".join(f"{a.name} ({a.url})" for a in owed)
