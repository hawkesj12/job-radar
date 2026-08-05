"""Attribution is a contract term here, not a courtesy.

Four wired sources grant API access on the stated condition that you link back and
name them, and two of those say outright they will REVOKE access if you do not.
Nothing in the package provided any of it before 0.7.0.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from job_radar import attribution, config, emit, sources

CATALOG = Path(__file__).resolve().parents[1] / "catalog"


def _wired() -> set[str]:
    return set(sources.DEPTH_ALL) | set(sources.BREADTH_ALL)


@pytest.mark.skipif(not CATALOG.is_dir(), reason="catalog/ not present")
def test_every_wired_source_that_demands_attribution_has_an_entry():
    """The catalog is where the terms were actually READ and dated; this module is
    where they are honoured. A new adapter whose profile says attribution is required
    must not be able to ship without one — that is the whole failure mode, since the
    obligation is invisible until a vendor cuts you off."""
    demanded = set()
    for f in CATALOG.glob("*.md"):
        if f.stem.startswith("_"):
            continue  # _SCHEMA is the template, not a source
        m = re.search(r"attribution_required:\s*(\S+)", f.read_text())
        if m and m.group(1).strip().lower() == "true" and f.stem in _wired():
            demanded.add(f.stem)
    missing = demanded - set(attribution.ATTRIBUTION)
    assert not missing, (
        f"{sorted(missing)} require attribution per catalog/ and are wired, but "
        "nothing in the package credits them"
    )


def test_attribution_is_only_owed_for_sources_that_contributed():
    """Crediting a source that returned nothing is noise, and noise is how a credit
    line gets ignored — which is the outcome the terms exist to prevent."""
    assert attribution.for_sources(["greenhouse", "lever"]) == []
    assert attribution.credit_line(["greenhouse"]) == ""
    owed = attribution.for_sources(["arbeitnow", "greenhouse"])
    assert [a.name for a in owed] == ["Arbeitnow"]


def test_the_credit_line_uses_each_vendors_own_name_and_links_back():
    """Both halves are required by the terms: the NAME as the source, and a link."""
    line = attribution.credit_line(["remoteok", "remotive"])
    for expected in ("Remote OK", "https://remoteok.com", "Remotive"):
        assert expected in line, f"{expected!r} missing from {line!r}"


def test_the_manifest_carries_the_obligation_to_whoever_displays_the_jobs():
    """The load-bearing surface. A library cannot discharge a DISPLAY obligation for
    the thing doing the displaying, so it hands over the terms — keyed by the same
    `source` value that is on every row, so a consumer can join without a lookup
    table of its own."""
    rows = [
        {"source": "arbeitnow", "sources": {"arbeitnow", "remoteok"}},
        {"source": "greenhouse"},
    ]
    m = json.loads(emit.manifest(rows, [], [], config.Config()))
    got = {a["source"]: a for a in m["attribution"]}
    assert set(got) == {"arbeitnow", "remoteok"}, "credited a source that sent nothing"
    assert got["remoteok"]["terms_url"]
    assert got["remoteok"]["terms_read_at"] == "2026-08-03"
    # A consumer must be able to tell "link the row and name them" from an obligation
    # it has to build UI for.
    assert got["arbeitnow"]["row_link_suffices"] is True


def test_adzuna_is_flagged_as_needing_more_than_a_text_credit():
    """Adzuna wants a branded label and a sized logo on every displayed advert, which
    no library can do on a consumer's behalf. Reporting it as satisfiable by a link
    would be the lie that matters."""
    (a,) = attribution.for_sources(["adzuna"])
    assert a.row_link_suffices is False
    assert "Jobs by Adzuna" in a.requirement


def test_remotive_redistribution_limit_is_stated_not_just_the_link():
    """Remotive forbids submitting its jobs to third-party job sites. That is a
    separate constraint from attribution and binds any consumer, so it has to be in
    what they receive."""
    (a,) = attribution.for_sources(["remotive"])
    assert "third-party" in a.requirement or "third party" in a.requirement


def test_every_entry_points_somewhere_real():
    for a in attribution.ATTRIBUTION.values():
        assert a.url.startswith("https://"), a.source
        assert a.terms_url.startswith("https://"), a.source
        assert a.name and a.requirement, a.source
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", a.read_at), a.source
