"""No letter or interview answer may be written from the example CV.

Earlier versions seeded every new profile with a fictional example CV and saved
it like a real one, so an applicant who never opened CV Builder got letters about
someone else's career. The example is never used, and it is no longer an
obstacle either: CV Builder is one source among several, so the applicant's own
CV file is used instead.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import job_scout.applicant as applicant
import job_scout.config as config
from job_scout.applicant import ApplicantError, gather_applicant_facts
from job_scout.cv.models import CVDocument
from job_scout.cv.sample import sample_cv
from job_scout.cv.storage import ProfileStore
from job_scout.database import Database
from job_scout.letters.models import LetterLanguage, LetterRequest, WarningKind
from job_scout.letters.writer import write_letter
from job_scout.models import JobListing
from tests.helpers import FakeLLMClient

USER = "Alex"
OWN_CV = (
    "Alex Voorbeeld\n2511 AB Den Haag\nalex@example.org\n"
    "Werkervaring\nKwaliteitsmedewerker, Echte Werkgever B.V., 2021 - heden\n"
    "Opleiding\nHBO Chemie, Hogeschool Echt, 2016 - 2020"
)


@pytest.fixture(autouse=True)
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate every test in its own data directory."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.yaml")
    config.write_global_config({"llm_provider": "local"})
    config.save_user_config(USER, {})


def _save(slug: str, doc: CVDocument) -> None:
    ProfileStore(config.user_cv_dir(USER)).save(slug, doc)


def _own_cv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Give the applicant a CV file of their own, as uploaded in Settings."""
    path = tmp_path / "CV Alex Voorbeeld.pdf"
    path.write_bytes(b"%PDF")
    config.save_user_config(USER, {"cv_path": str(path)})
    monkeypatch.setattr(applicant, "parse_cv", lambda _path: OWN_CV)


def _made_real(language: str) -> CVDocument:
    """The example with every employer and school replaced: a user's own CV."""
    doc = sample_cv(language).model_copy(deep=True)
    doc.full_name = "Alex Voorbeeld"
    doc.headline = "Kwaliteitsexpert"
    for section in doc.all_sections():
        for entry in getattr(section, "entries", None) or []:
            if getattr(entry, "organisation", None):
                entry.organisation = "Real Employer B.V."
            if getattr(entry, "school", None):
                entry.school = "Real University"
    return doc


def test_an_untouched_example_is_skipped_for_the_applicants_own_cv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The case that produced a letter about a fictional physicist."""
    _save("nederlands", sample_cv("NL"))
    _own_cv(monkeypatch, tmp_path)

    facts = gather_applicant_facts(USER, LetterLanguage.NL)

    assert "Deltameet" not in facts.evidence()
    assert "Echte Werkgever" in facts.sources["own_cv_document"]
    assert facts.cv_doc is None
    assert any("example CV" in note for note in facts.missing)


def test_with_nothing_but_the_example_there_is_nothing_to_write_from() -> None:
    """The example is not a fallback: refusing beats describing someone else."""
    _save("default", sample_cv("EN"))

    with pytest.raises(ApplicantError, match="No CV information found"):
        gather_applicant_facts(USER, LetterLanguage.EN)


def test_a_partly_edited_example_is_still_skipped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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
    _own_cv(monkeypatch, tmp_path)

    facts = gather_applicant_facts(USER, LetterLanguage.EN)

    assert "cv_builder_profile" not in facts.sources


def test_a_real_cv_is_chosen_over_an_example_in_the_right_language() -> None:
    """Automatic selection skips the example rather than failing outright."""
    _save("nederlands", sample_cv("NL"))
    _save("mijn-cv", _made_real("NL"))

    facts = gather_applicant_facts(USER, LetterLanguage.NL)

    assert facts.cv_slug == "mijn-cv"
    assert "Deltameet" not in facts.evidence()


def test_choosing_the_example_explicitly_does_not_bypass_the_guard(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An explicit choice is noted and skipped, never used."""
    _save("default", sample_cv("EN"))
    _own_cv(monkeypatch, tmp_path)

    facts = gather_applicant_facts(USER, LetterLanguage.EN, cv_slug="default")

    assert facts.cv_slug is None
    assert "Deltameet" not in facts.evidence()
    assert any("'default'" in note and "Deltameet" in note for note in facts.missing)


def test_a_fully_replaced_cv_is_used() -> None:
    """The guard must not punish the user who did replace everything."""
    _save("default", _made_real("EN"))

    facts = gather_applicant_facts(USER, LetterLanguage.EN)

    assert facts.cv_slug == "default"
    assert facts.name == "Alex Voorbeeld"


def test_a_letter_is_written_from_the_own_cv_and_says_what_it_skipped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """End to end: Sanne's situation, where CV Builder only held the example."""
    _save("nederlands", sample_cv("NL"))
    _own_cv(monkeypatch, tmp_path)
    job_id = Database(config.user_db_path(USER)).save_job(
        JobListing(
            title="Analist",
            company="Doelbedrijf",
            url="https://example.org/vacature",
            source="test",
            description="Wij zoeken een analist voor ons laboratorium in Den Haag.",
        )
    )
    client = FakeLLMClient(['{"paragraphs": ["Ik solliciteer graag."]}'])

    letter = write_letter(USER, LetterRequest(job_id=job_id, language="nl"), client)

    prompt = client.calls[0][0]
    sources = json.loads(prompt[prompt.index('{"applicant_sources"') :])
    assert "Echte Werkgever" in json.dumps(sources["applicant_sources"])
    assert "Deltameet" not in prompt
    assert letter.signature == "Alex Voorbeeld"
    assert letter.place_date.startswith("Den Haag")
    assert letter.cv_slug is None
    assert any(w.kind is WarningKind.SOURCES for w in letter.warnings)
