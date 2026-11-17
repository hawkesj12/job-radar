"""`sections` -- the header pipeline, and the one lookup that depends on it.

A NEW FILE rather than more of `tests/test_core.py`, because that file is being
edited concurrently for `vocab`/`util` and two agents holding one file is the
collision this release has already paid for four times.
"""

from job_radar import sections, util


def test_a_header_split_by_an_inline_tag_is_findable_in_its_own_text():
    """P13. `_detag` replaced every tag with a space -- including an inline tag
    sitting INSIDE a word -- while `_clean_decoded` deletes an inline run followed
    by lower case. The two disagreed about the same bytes, so the header the record
    published ("About t he Role") was absent from the text the record shipped beside
    it. Measured [20 live boards, 6,267 bodies, 67,678 sections, 2026-08-21]: 3,962
    headers absent from their own text, 3,882 repaired, 0 lost.
    """
    text, secs = util.clean_with_sections(
        "&lt;h2&gt;&lt;strong&gt;About t&lt;span&gt;he&lt;/span&gt; Role"
        "&lt;/strong&gt;&lt;/h2&gt;&lt;p&gt;You will ship.&lt;/p&gt;"
    )
    assert secs[0]["header"] == "About the Role"
    # The point is not the string -- it is that the lookup can now succeed.
    assert secs[0]["header"] in text
    assert text[secs[0]["start"] : secs[0]["end"]] == "You will ship."


def test_punctuation_outside_the_emphasis_does_not_become_a_stray_space():
    """The commonest live shape by far, and the one a mid-word example hides:
    `<h2><strong>Health can't wait</strong>. </h2>` yielded "Health can't wait ."
    against a body reading "Health can't wait." One board contributed this alone on
    every posting it publishes.
    """
    text, secs = util.clean_with_sections(
        "&lt;h2&gt;&lt;strong&gt;Health can wait&lt;/strong&gt;. &lt;/h2&gt;"
        "&lt;p&gt;Not for symptoms.&lt;/p&gt;"
    )
    assert secs[0]["header"] == "Health can wait."
    assert secs[0]["header"] in text


def test_a_header_is_one_line_even_when_its_markup_is_not():
    """FORK E, REFUSED DELIBERATELY. `_clean_decoded` turns a block end into a
    newline, and adopting that here would make 50 more headers findable -- at the
    cost of a literal newline inside a published `header` string in the NDJSON and
    the CSV. A header is one line by construction; an unfindable header is a better
    record than a header containing a line break. Those 50 are a named residual.
    """
    text, secs = util.clean_with_sections(
        "&lt;h2&gt;&lt;strong&gt;Key&lt;br/&gt;Duties&lt;/strong&gt;&lt;/h2&gt;"
        "&lt;p&gt;Ship things.&lt;/p&gt;"
    )
    assert secs[0]["header"] == "Key Duties"
    assert "\n" not in secs[0]["header"]
    assert "Key\nDuties" in text  # the BODY keeps the break; only the header collapses


def test_an_unfindable_header_still_yields_a_correct_span_not_a_guessed_one():
    """The path the existing span guard cannot reach, which is why it stayed green
    while 229 real spans opened inside their own header.

    `clean_with_sections` advances `pos` past a header it can LOCATE. A header it
    cannot locate leaves `pos` untouched -- by design, so the window can only narrow,
    never skip real body text. The previous test's body is exactly that case, and the
    contract is that the section still gets the right span rather than one anchored
    to the wrong place.
    """
    text, secs = util.clean_with_sections(
        "&lt;h2&gt;&lt;strong&gt;Key&lt;br/&gt;Duties&lt;/strong&gt;&lt;/h2&gt;"
        "&lt;p&gt;Ship things.&lt;/p&gt;"
    )
    assert secs[0]["header"] not in text  # the lookup genuinely fails here
    assert text[secs[0]["start"] : secs[0]["end"]] == "Ship things."


def test_detag_decodes_a_doubly_escaped_entity_the_way_the_body_does():
    """`_ENT` hand-listed nine entities; `html.unescape` handles the whole set, and
    the header now decodes to exactly the same depth its body does. The depth itself
    is the caller's (`clean_with_sections` unescapes once before `_clean_decoded`
    unescapes again), so what this pins is the AGREEMENT, not the level.
    """
    for raw in ("R&amp;amp;D", "R&amp;D", "caf&eacute;"):
        assert sections._detag(raw) == util._clean_decoded(raw)
    assert sections._detag("R&amp;D") == "R&D"
