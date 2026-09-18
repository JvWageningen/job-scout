"""Generated text is cleaned of the marks that give it away, and nothing else."""

from __future__ import annotations

import pytest

from job_scout.writing_style import HOUSE_STYLE, ai_tells, humanise, strip_emphasis


@pytest.mark.parametrize(
    ("generated", "cleaned"),
    [
        (
            "De functie is on-site in Noordwijk \u2014 is er ruimte voor thuiswerk?",
            "De functie is on-site in Noordwijk, is er ruimte voor thuiswerk?",
        ),
        (
            "Ik werkte er\u2014met plezier\u2014drie jaar.",
            "Ik werkte er, met plezier, drie jaar.",
        ),
        ("Van 2019\u20132021 bij Topgeschenken.", "Van 2019-2021 bij Topgeschenken."),
        ("Van 2019 - 2021 bij Topgeschenken.", "Van 2019-2021 bij Topgeschenken."),
        ("Goed werk - en snel.", "Goed werk, en snel."),
        ("Dat is **echt** belangrijk.", "Dat is echt belangrijk."),
        ("Dat is *echt* belangrijk.", "Dat is echt belangrijk."),
        ("Top resultaat \U0001f680 behaald.", "Top resultaat behaald."),
        ("Klaar\u2026", "Klaar..."),
        ("Einde zin \u2014.", "Einde zin."),
    ],
)
def test_the_marks_of_generated_text_are_removed(generated: str, cleaned: str) -> None:
    assert humanise(generated) == cleaned


@pytest.mark.parametrize(
    "kept",
    [
        "B2B-klanten in de e-commerce, 3 x per week.",
        "Werkervaring bij Laser 2000 Benelux.",
        "sanne.witteman@example.org of 06-51418568",
        "Mijn salarisindicatie is EUR 50.000-70.000 per jaar.",
    ],
)
def test_ordinary_text_is_left_exactly_as_it_is(kept: str) -> None:
    """Hyphens in words, ranges and numbers are not dashes."""
    assert humanise(kept) == kept


def test_a_bullet_list_keeps_its_bullets_and_its_lines() -> None:
    """A letter paragraph may hold a list on purpose; only the dashes inside go."""
    generated = "Wat ik meebreng:\n- CRO \u2014 A/B-testen\n- SEO"

    assert humanise(generated) == "Wat ik meebreng:\n- CRO, A/B-testen\n- SEO"


def test_empty_text_stays_empty() -> None:
    assert humanise("") == ""


def test_stock_phrases_are_found_in_either_language() -> None:
    text = (
        "I am passionate about growth and eager to leverage my skills. "
        "Kortom, ik werk graag in een dynamische omgeving met passie."
    )

    assert ai_tells(text) == [
        "passionate",
        "eager to",
        "leverage",
        "kortom",
        "dynamische",
        "passie",
    ]


def test_plain_writing_has_no_tells() -> None:
    text = "Ik heb drie jaar A/B-testen gedraaid bij Topgeschenken en wil dat hier."

    assert ai_tells(text) == []


def test_a_tell_inside_a_longer_word_does_not_count() -> None:
    """'passie' is a tell; 'passief' and 'compassie' are just words."""
    assert ai_tells("Een passieve houding en compassie.") == []


def test_the_house_style_does_not_use_the_dashes_it_forbids() -> None:
    """A model copies the punctuation of its instructions."""
    assert "\u2014" not in HOUSE_STYLE
    assert "\u2013" not in HOUSE_STYLE
    assert " - " not in HOUSE_STYLE
    assert " -- " not in HOUSE_STYLE


def test_the_house_style_names_the_double_hyphen() -> None:
    """Asked for plain text, a model types two hyphens where it means a dash."""
    assert "double hyphen (--)" in HOUSE_STYLE


@pytest.mark.parametrize(
    ("generated", "cleaned"),
    [
        ("Strong fit -- but the role is senior", "Strong fit, but the role is senior"),
        ("Strong fit--but senior", "Strong fit, but senior"),
        ("a --- b", "a, b"),
        ("2019--2021", "2019-2021"),
        ("pages 10--20", "pages 10-20"),
        ("- item -- note", "- item, note"),
    ],
)
def test_a_double_hyphen_is_a_dash_too(generated: str, cleaned: str) -> None:
    """Between words it joins clauses; between digits it is a range."""
    assert humanise(generated) == cleaned


@pytest.mark.parametrize(
    "kept", ["run it with --verbose", "---", "e-commerce B2B-klanten", "- item"]
)
def test_a_flag_or_a_rule_line_is_not_a_dash(kept: str) -> None:
    """Two hyphens before a word, or a line of hyphens, join no clauses."""
    assert humanise(kept) == kept


def test_strip_emphasis_removes_only_the_markdown() -> None:
    """A line that belongs to the applicant keeps its own dashes."""
    line = "Hewlett Packard Enterprise \u2013 **Amstelveen** - *team*"

    assert strip_emphasis(line) == "Hewlett Packard Enterprise \u2013 Amstelveen - team"
