"""No letter or interview answer may be written from the bundled example CV.

CV Builder seeds a new profile with the example so its editor is not empty, and
saves that seed like any other profile. A user who never opened CV Builder was
therefore getting letters and interview material about a fictional person's career.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import job_scout.config as config
from job_scout.cv.models import CVDocument
from job_scout.cv.sample import sample_cv
from job_scout.cv.storage import ProfileStore
from job_scout.letters.models import LetterLanguage
from job_scout.letters.writer import LetterError, select_cv

USER = "Alex"


@pytest.fixture(autouse=True)
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate every test in its own data directory."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.yaml")
    config.save_user_config(USER, {})


def _save(slug: str, doc: CVDocument) -> None:
    ProfileStore(config.user_cv_dir(USER)).save(slug, doc)


def _made_real(language: str) -> CVDocument:
    """The example with every employer and school replaced: a user's own CV."""
    doc = sample_cv(language).model_copy(deep=True)
    for section in doc.all_sections():
        for entry in getattr(section, "entries", None) or []:
            if getattr(entry, "organisation", None):
                entry.organisation = "Real Employer B.V."
            if getattr(entry, "school", None):
                entry.school = "Real University"
    return doc


def test_an_untouched_example_is_refused_with_a_clear_reason() -> None:
    """The case that produced a CRO letter about a fictional physicist."""
    _save("nederlands", sample_cv("NL"))

    with pytest.raises(LetterError, match="still contains the example CV"):
        select_cv(USER, LetterLanguage.NL)


def test_a_partly_edited_example_is_still_refused() -> None:
    """One fictional employer left behind is still a fictional job in a letter."""
    doc = sample_cv("EN").model_copy(deep=True)
    first = next(
        entry
        for section in doc.all_sections()
        for entry in (getattr(section, "entries", None) or [])
        if getattr(entry, "organisation", None)
    )
    first.organisation = "Real Employer B.V."
    _save("default", doc)

    with pytest.raises(LetterError, match="still contains the example CV"):
        select_cv(USER, LetterLanguage.EN)


def test_a_real_cv_is_chosen_over_an_example_in_the_right_language() -> None:
    """Automatic selection skips the example rather than failing outright."""
    _save("nederlands", sample_cv("NL"))
    _save("mijn-cv", _made_real("NL"))

    slug, _ = select_cv(USER, LetterLanguage.NL)

    assert slug == "mijn-cv"


def test_choosing_the_example_explicitly_is_refused_too() -> None:
    """An explicit choice must not bypass the guard."""
    _save("default", sample_cv("EN"))

    with pytest.raises(LetterError, match="still contains the example CV"):
        select_cv(USER, LetterLanguage.EN, "default")


def test_a_fully_replaced_cv_is_accepted() -> None:
    """The guard must not punish the user who did replace everything."""
    _save("default", _made_real("EN"))

    slug, doc = select_cv(USER, LetterLanguage.EN)

    assert slug == "default"
    assert doc.all_sections()


def test_the_message_names_what_is_still_fictional() -> None:
    """Saying which entry is still the example tells the user what to fix."""
    _save("default", sample_cv("EN"))

    with pytest.raises(LetterError) as raised:
        select_cv(USER, LetterLanguage.EN)

    assert "Deltameet" in str(raised.value)
