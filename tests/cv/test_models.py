"""Model validation and serialisation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from job_scout.cv.models import (
    ContactSection,
    CVDocument,
    ExperienceSection,
    SkillItem,
    SkillsSection,
    TextSection,
    Theme,
)
from job_scout.cv.sample import blank_cv, sample_cv


@pytest.mark.parametrize("value", ["#008037", "#fff", "#ABCDEF"])
def test_valid_colours_are_accepted(value: str) -> None:
    assert Theme(accent=value).accent == value.upper()


@pytest.mark.parametrize("value", ["008037", "#12345", "red", "", "#GGGGGG"])
def test_invalid_colours_are_rejected(value: str) -> None:
    with pytest.raises(ValidationError):
        Theme(accent=value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("base_font_size", 2.0),
        ("base_font_size", 40.0),
        ("line_spacing", 0.4),
        ("sidebar_width_pct", 0.05),
        ("sidebar_width_pct", 0.9),
        ("photo_diameter_pct", 2.0),
    ],
)
def test_out_of_range_theme_values_are_rejected(field: str, value: float) -> None:
    with pytest.raises(ValidationError):
        Theme(**{field: value})


def test_unknown_theme_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Theme(nonsense=True)


def test_skill_level_is_bounded() -> None:
    assert SkillItem(name="x", level=5).level == 5
    with pytest.raises(ValidationError):
        SkillItem(name="x", level=6)


def test_sections_get_distinct_ids() -> None:
    first, second = TextSection(), TextSection()
    assert first.id != second.id


def test_discriminated_union_round_trips() -> None:
    doc = sample_cv()
    restored = CVDocument.model_validate(doc.model_dump(mode="json"))
    assert restored == doc
    assert isinstance(restored.main[1], ExperienceSection)
    assert isinstance(restored.sidebar[0], ContactSection)


def test_unknown_section_kind_is_rejected() -> None:
    with pytest.raises(ValidationError):
        CVDocument.model_validate({"main": [{"kind": "nope", "title": "x"}]})


def test_missing_discriminator_is_rejected() -> None:
    with pytest.raises(ValidationError):
        CVDocument.model_validate({"main": [{"title": "x"}]})


def test_kind_defaults_are_fixed() -> None:
    with pytest.raises(ValidationError):
        TextSection(kind="experience")


def test_strings_are_stripped() -> None:
    assert TextSection(title="  Profile  ").title == "Profile"


def test_all_sections_spans_both_columns() -> None:
    doc = sample_cv()
    assert len(doc.all_sections()) == len(doc.sidebar) + len(doc.main)


def test_empty_document_is_valid() -> None:
    doc = CVDocument()
    assert doc.sidebar == []
    assert doc.theme.sidebar_bg == "#0B3D2C"


def test_sample_covers_every_section_kind_used_by_the_template() -> None:
    kinds = {section.kind for section in sample_cv().all_sections()}
    assert kinds == {"contact", "skills", "details", "text", "experience", "education"}


def test_blank_cv_is_a_valid_skeleton() -> None:
    doc = blank_cv()
    assert doc.full_name == "Your Name"
    assert {section.kind for section in doc.main} == {
        "text",
        "experience",
        "education",
    }


def _luminance(hex_colour: str) -> float:
    """Relative luminance of a hex colour, per WCAG 2.1."""
    raw = hex_colour.lstrip("#")
    if len(raw) == 3:
        raw = "".join(channel * 2 for channel in raw)
    channels = [int(raw[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [
        value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4
        for value in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(one: str, two: str) -> float:
    """WCAG contrast ratio between two hex colours."""
    high, low = sorted((_luminance(one), _luminance(two)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def test_contrast_helper_matches_known_values() -> None:
    assert _contrast("#FFFFFF", "#000000") == pytest.approx(21.0, abs=0.01)
    assert _contrast("#FFFFFF", "#FFFFFF") == pytest.approx(1.0, abs=0.01)


def test_default_palette_meets_wcag_aa() -> None:
    """The shipped palette must stay legible, including in greyscale print.

    The original reference colour ``#38CB78`` gave white heading text only 2.1:1,
    which is why the default moved to a deeper green.
    """
    theme = Theme()
    assert _contrast(theme.bar_text, theme.accent) >= 4.5
    assert _contrast(theme.sidebar_text, theme.sidebar_bg) >= 4.5
    assert _contrast(theme.body_text, theme.page_bg) >= 4.5
    assert _contrast(theme.heading_text, theme.page_bg) >= 4.5


def test_section_bars_stand_out_against_the_sidebar() -> None:
    theme = Theme()
    assert _contrast(theme.accent, theme.sidebar_bg) >= 1.5


def test_skills_columns_are_bounded() -> None:
    assert SkillsSection(columns=3).columns == 3
    with pytest.raises(ValidationError):
        SkillsSection(columns=9)
