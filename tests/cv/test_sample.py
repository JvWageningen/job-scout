"""The language versions must stay structurally identical.

The whole point of building both CVs from one structure is that only the words
differ. These tests fail if the two ever drift apart in shape.
"""

from __future__ import annotations

import pytest

from job_scout.cv.models import (
    CVDocument,
    EducationSection,
    ExperienceSection,
    Section,
    SkillsSection,
)
from job_scout.cv.sample import DUTCH, ENGLISH, LANGUAGES, Strings, sample_cv
from tests.cv.conftest import page_text, render_bytes


def shape(section: Section) -> tuple[object, ...]:
    """Reduce a section to the parts that must match across languages."""
    counts: int = 0
    if isinstance(section, ExperienceSection | EducationSection):
        counts = len(section.entries)
    elif hasattr(section, "items"):
        counts = len(section.items)
    return (section.kind, section.icon, section.enabled, counts)


@pytest.mark.parametrize("language", ["EN", "NL"])
def test_each_language_builds(language: str) -> None:
    doc = sample_cv(language)
    assert doc.language == language
    assert doc.full_name == "Sam de Vries"


def test_unknown_language_falls_back_to_english() -> None:
    assert sample_cv("de").language == "EN"
    assert sample_cv("").language == "EN"


def test_language_tag_is_case_insensitive() -> None:
    assert sample_cv("nl").language == "NL"


def test_columns_have_the_same_shape() -> None:
    english, dutch = sample_cv("EN"), sample_cv("NL")
    assert [shape(s) for s in english.sidebar] == [shape(s) for s in dutch.sidebar]
    assert [shape(s) for s in english.main] == [shape(s) for s in dutch.main]


def test_theme_and_identity_match() -> None:
    english, dutch = sample_cv("EN"), sample_cv("NL")
    assert english.theme == dutch.theme
    assert english.full_name == dutch.full_name
    assert english.photo == dutch.photo


def test_shared_content_is_not_translated() -> None:
    """Proper nouns must be identical, not accidentally localised."""
    english, dutch = sample_cv("EN"), sample_cv("NL")
    for doc in (english, dutch):
        contact = doc.sidebar[0]
        assert [item.value for item in contact.items] == [  # type: ignore[attr-defined]
            "sam.devries@example.com",
            "in/sam-de-vries",
            "+31 (0)6 1234 5678",
        ]

    skills_en, skills_nl = english.sidebar[1], dutch.sidebar[1]
    assert isinstance(skills_en, SkillsSection)
    assert isinstance(skills_nl, SkillsSection)
    assert [i.name for i in skills_en.items] == [i.name for i in skills_nl.items]


def test_section_titles_actually_differ() -> None:
    """Guards against a copy-paste that leaves Dutch showing English headings."""
    english, dutch = sample_cv("EN"), sample_cv("NL")
    assert english.main[1].title == "Work experience"
    assert dutch.main[1].title == "Werkervaring"
    assert english.main[2].title == "Education"
    assert dutch.main[2].title == "Opleidingen"


def test_periods_match_position_by_position() -> None:
    """Same roles in the same order, even though month names are localised."""
    english = sample_cv("EN").main[1]
    dutch = sample_cv("NL").main[1]
    assert isinstance(english, ExperienceSection)
    assert isinstance(dutch, ExperienceSection)
    assert len(english.entries) == len(dutch.entries) == 5
    assert english.entries[0].period == "Aug 2024 - present"
    assert dutch.entries[0].period == "aug 2024 - heden"


def test_every_translatable_field_is_populated() -> None:
    """No language may ship an empty string where the other has content."""
    # description_label is deliberately blank in both: the original template's
    # "Job description:" line was dropped. It must be blank in both or neither.
    optional = {"description_label"}
    for text in (ENGLISH, DUTCH):
        for field in Strings.__dataclass_fields__:
            if field in optional:
                continue
            assert getattr(text, field), f"{text.language}.{field} is empty"


def test_optional_fields_agree_across_languages() -> None:
    """A field blank in one language must be blank in the other."""
    for field in Strings.__dataclass_fields__:
        assert bool(getattr(ENGLISH, field)) == bool(getattr(DUTCH, field)), (
            f"{field} is set in one language but not the other"
        )


@pytest.mark.parametrize("language", ["EN", "NL"])
def test_letter_facts_reached_the_cv(language: str) -> None:
    """Specific details from the seed content must survive through to the PDF.

    Compared upper-cased: the template upper-cases entry titles and organisation
    lines, so "Mobility team" reaches the page as "MOBILITY TEAM". The chosen
    phrases appear verbatim in both languages, so one tuple covers the pair.
    """
    text = page_text(render_bytes(sample_cv(language))).upper()
    for expected in (
        "SELF-HOSTED AI",
        "COMPUTER VISION",
        "MOBILITY TEAM",
    ):
        assert expected in text, f"{language}: missing {expected!r}"


def test_automation_bullets_are_present_in_both_languages() -> None:
    for language in ("EN", "NL"):
        section = sample_cv(language).main[1]
        assert isinstance(section, ExperienceSection)
        assert len(section.entries[0].bullets) == 3


@pytest.mark.parametrize("language", ["EN", "NL"])
def test_each_language_fits_one_page(language: str) -> None:
    from tests.cv.conftest import open_pdf

    with open_pdf(render_bytes(sample_cv(language))) as document:
        assert document.page_count == 1


def test_languages_registry_is_consistent() -> None:
    for tag, text in LANGUAGES.items():
        assert tag == text.language


def test_document_language_defaults_to_english() -> None:
    assert CVDocument().language == "EN"
