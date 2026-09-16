"""Text measurement and wrapping."""

from __future__ import annotations

import pytest
from reportlab.lib.colors import black

from job_scout.cv.fonts import face, register_fonts
from job_scout.cv.render.text import (
    TextStyle,
    baseline_offset,
    prepare,
    text_width,
    wrap,
)


def style(
    size: float = 10.0, char_space: float = 0.0, upper: bool = False
) -> TextStyle:
    """Build a plain Lato style for tests."""
    return TextStyle(
        font=face("Lato", "regular"),
        size=size,
        fill=black,
        leading=size * 1.3,
        char_space=char_space,
        upper=upper,
    )


def test_bundled_fonts_register() -> None:
    assert register_fonts() is True
    assert face("Lato", "bold") == "Lato-Bold"


def test_unknown_family_falls_back_to_helvetica() -> None:
    assert face("Nonexistent", "bold") == "Helvetica-Bold"


def test_prepare_applies_upper() -> None:
    assert prepare("hello", style(upper=True)) == "HELLO"
    assert prepare("hello", style()) == "hello"


def test_char_space_widens_text() -> None:
    plain = text_width("HELLO", style())
    spaced = text_width("HELLO", style(char_space=2.0))
    # Four gaps between five glyphs; the trailing gap leaves no ink.
    assert spaced == pytest.approx(plain + 8.0)


def test_empty_text_has_zero_width() -> None:
    assert text_width("", style()) == 0.0


def test_wrap_breaks_on_width() -> None:
    text = "the quick brown fox jumps over the lazy dog"
    narrow = wrap(text, style(), 60)
    wide = wrap(text, style(), 400)
    assert len(narrow) > len(wide)
    assert " ".join(" ".join(narrow).split()) == text


def test_wrap_honours_newlines_and_blank_lines() -> None:
    lines = wrap("alpha\n\nbeta", style(), 500)
    assert lines == ["alpha", "", "beta"]


def test_wrap_returns_nothing_for_blank_input() -> None:
    assert wrap("   \n  ", style(), 200) == []


def test_wrap_hard_breaks_an_oversized_word() -> None:
    lines = wrap("A" * 80, style(), 40)
    assert len(lines) > 1
    assert "".join(lines) == "A" * 80
    for line in lines:
        assert text_width(line, style()) <= 40


def test_wrap_never_exceeds_width() -> None:
    text = "Consultation, installation and training customers on various products"
    for line in wrap(text, style(), 120):
        assert text_width(line, style()) <= 120


def test_wrap_measures_with_letter_spacing() -> None:
    text = "photonics and nanotechnology"
    assert len(wrap(text, style(char_space=3.0), 150)) > len(wrap(text, style(), 150))


def test_baseline_offset_sits_inside_the_line_box() -> None:
    offset = baseline_offset(style(12.0))
    assert 0 < offset < style(12.0).leading
