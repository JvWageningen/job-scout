"""Lorem ipsum fills what is empty, only in a copy, and never mixes into content."""

from __future__ import annotations

from job_scout.cv.models import ExperienceEntry, ExperienceSection
from job_scout.cv.placeholders import LOREM_NAME, LOREM_PARAGRAPH, with_placeholders
from job_scout.cv.sample import blank_cv


def test_an_empty_cv_is_filled_everywhere() -> None:
    filled = with_placeholders(blank_cv("NL"))

    assert filled.full_name == LOREM_NAME
    experience = next(s for s in filled.main if isinstance(s, ExperienceSection))
    assert experience.entries[0].organisation
    assert LOREM_PARAGRAPH in filled.model_dump_json()


def test_the_original_is_never_changed() -> None:
    """The editor's document is what gets saved; placeholders must not reach it."""
    doc = blank_cv("EN")

    with_placeholders(doc)

    assert doc.full_name == ""
    assert "Lorem" not in doc.model_dump_json()


def test_a_half_finished_entry_is_shown_as_it_is() -> None:
    """Placeholder text next to real text would read as part of the CV."""
    doc = blank_cv("EN")
    experience = next(s for s in doc.main if isinstance(s, ExperienceSection))
    experience.entries = [ExperienceEntry(title="Analyst", organisation="")]

    filled = with_placeholders(doc)

    entry = next(s for s in filled.main if isinstance(s, ExperienceSection)).entries[0]
    assert entry.title == "Analyst"
    assert entry.organisation == ""
