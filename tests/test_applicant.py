"""Applicant facts come from every source the applicant provided, none mandatory."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import job_scout.applicant as applicant
import job_scout.config as config
from job_scout.applicant import (
    ApplicantError,
    describe_sources,
    gather_applicant_facts,
    profile_choices,
)
from job_scout.cv.models import CVDocument, ExperienceEntry, ExperienceSection
from job_scout.cv.sample import sample_cv
from job_scout.cv.storage import ProfileStore
from job_scout.cv_parser import compute_cv_hash
from job_scout.database import Database
from job_scout.letters.models import LetterLanguage

USER = "Jeroen"
PLAIN = "Werkervaring bij Echte Werkgever, 2021 - heden"
ADDRESS = "Dorpsstraat 1\n2152 KL Nieuw-Vennep\n"
LINKEDIN_PROFILE = {
    "skills": ["Python"],
    "past_roles": [{"title": "Linked", "company": "LinkedIn Only B.V."}],
}
CV_TEXT = (
    "JEROEN VAN\nWAGENINGEN\nAPPROVAL EXPERT\nCONTACT\njeroen@example.org\n"
    "06-12345678\nwww.linkedin.com/in/jvw-example/\n"
    "Werkervaring\nApproval expert, Echte Werkgever B.V., 2021 - heden\n"
    "2018 HR Manager\n"
)


@pytest.fixture(autouse=True)
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate every test in its own data directory."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.yaml")
    config.write_global_config({"llm_provider": "local"})
    config.save_user_config(USER, {"name": USER})


def _own_cv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    text: str = CV_TEXT,
    name: str = "20260809 Jeroen van Wageningen CV NL.pdf",
    **extra: object,
) -> Path:
    path = tmp_path / name
    path.write_bytes(b"%PDF")
    config.save_user_config(USER, {"name": USER, "cv_path": str(path), **extra})
    monkeypatch.setattr(applicant, "parse_cv", lambda _path: text)
    return path


def _real_builder_cv(language: str = "NL") -> CVDocument:
    return CVDocument(
        full_name="Jeroen van Wageningen",
        language=language,
        main=[
            ExperienceSection(
                title="Werkervaring",
                entries=[ExperienceEntry(title="Expert", organisation="Builder B.V.")],
            )
        ],
    )


def test_the_own_cv_file_alone_is_enough(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """CV Builder is optional: an uploaded CV of one's own is a full source."""
    _own_cv(monkeypatch, tmp_path)

    facts = gather_applicant_facts(USER, LetterLanguage.NL)

    assert "Echte Werkgever" in facts.sources["own_cv_document"]
    assert facts.cv_slug is None
    assert facts.missing == []


def test_every_source_is_used_together(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """All of them at once, each under its own label."""
    _own_cv(
        monkeypatch,
        tmp_path,
        cv_notes="Freelance projects since 2024.",
        profile_description="Ik zoek werk in procesverbetering.",
        career_tracks=[{"id": "qa", "name": "Kwaliteit", "description": "QA-rollen"}],
    )
    ProfileStore(config.user_cv_dir(USER)).save("nederlands", _real_builder_cv())
    db = Database(config.user_db_path(USER))
    db.save_cv_profile_cache(
        compute_cv_hash(CV_TEXT),
        json.dumps(LINKEDIN_PROFILE),
    )
    db.save_star_story("S", "T", "A", "R", [])

    facts = gather_applicant_facts(USER, LetterLanguage.NL)

    assert set(facts.sources) == {
        "cv_builder_profile",
        "own_cv_document",
        "extra_experience_notes",
        "parsed_profile",
        "self_description",
        "career_directions",
        "star_stories",
    }
    assert "LinkedIn Only B.V." in facts.evidence()
    assert facts.missing == []


def test_stories_can_be_left_out_for_a_caller_that_cites_them_itself(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _own_cv(monkeypatch, tmp_path)
    Database(config.user_db_path(USER)).save_star_story("S", "T", "A", "R", [])

    facts = gather_applicant_facts(USER, LetterLanguage.NL, stories=False)

    assert "star_stories" not in facts.sources


def test_wishes_alone_are_not_a_career(monkeypatch: pytest.MonkeyPatch) -> None:
    """A profile description says what someone wants, not what they did."""
    config.save_user_config(USER, {"profile_description": "Ik zoek een baan."})

    with pytest.raises(ApplicantError, match="No CV information found"):
        gather_applicant_facts(USER, LetterLanguage.NL)


def test_the_name_is_found_even_when_the_pdf_split_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """PDF extraction gave 'JEROEN VAN\\nWAGENINGEN'; the letter needs it signed."""
    _own_cv(monkeypatch, tmp_path, name="cv.pdf")

    facts = gather_applicant_facts(USER, LetterLanguage.NL)

    assert facts.name == "Jeroen van Wageningen"


def test_the_file_name_comes_first_because_extraction_splits_names(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A real CV came out of the PDF as 'Sanne Tessa W ittem an'."""
    _own_cv(monkeypatch, tmp_path, text="Jeroen W ageningen\n" + PLAIN)

    facts = gather_applicant_facts(USER, LetterLanguage.NL)

    assert facts.name == "Jeroen van Wageningen"


def test_no_name_is_guessed_when_no_source_states_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _own_cv(monkeypatch, tmp_path, text=PLAIN, name="cv.pdf")

    facts = gather_applicant_facts(USER, LetterLanguage.NL)

    assert facts.name == ""


def test_contacts_come_from_the_own_cv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _own_cv(monkeypatch, tmp_path)

    facts = gather_applicant_facts(USER, LetterLanguage.NL)

    assert facts.contacts == [
        "jeroen@example.org",
        "06-12345678",
        "www.linkedin.com/in/jvw-example/",
    ]


@pytest.mark.parametrize(
    ("written", "found"),
    [
        ("+31 (0)6-40940750", "+31 (0)6-40940750"),
        ("+31 6 1234 5678", "+31 6 1234 5678"),
        ("tel. 06 12 34 56 78", "06 12 34 56 78"),
    ],
)
def test_mobile_numbers_are_taken_whole(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, written: str, found: str
) -> None:
    _own_cv(monkeypatch, tmp_path, text=f"{PLAIN}\n{written}\n")

    assert found in gather_applicant_facts(USER, LetterLanguage.NL).contacts


def test_the_place_comes_from_the_home_address_first(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _own_cv(monkeypatch, tmp_path, home_address="Hoofdweg 1, 2152 KL Nieuw-Vennep")

    assert gather_applicant_facts(USER, LetterLanguage.NL).place == "Nieuw-Vennep"


def test_a_year_and_an_abbreviation_are_not_mistaken_for_a_postcode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """'2018 HR Manager' has the shape of '2152 KL Nieuw-Vennep'."""
    _own_cv(monkeypatch, tmp_path)

    assert gather_applicant_facts(USER, LetterLanguage.NL).place == ""


def test_a_postcode_line_in_the_own_cv_gives_the_place(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _own_cv(monkeypatch, tmp_path, text=CV_TEXT + ADDRESS)

    assert gather_applicant_facts(USER, LetterLanguage.NL).place == "Nieuw-Vennep"


def test_a_real_builder_cv_supplies_the_identity(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _own_cv(monkeypatch, tmp_path, text=PLAIN, name="cv.pdf")
    ProfileStore(config.user_cv_dir(USER)).save("nederlands", _real_builder_cv())

    facts = gather_applicant_facts(USER, LetterLanguage.NL)

    assert facts.name == "Jeroen van Wageningen"
    assert facts.cv_slug == "nederlands"


def test_the_dropdown_marks_the_example_and_empty_profiles(tmp_path: Path) -> None:
    store = ProfileStore(config.user_cv_dir(USER))
    store.save("default", sample_cv("EN"))
    store.save("leeg", CVDocument())
    store.save("nederlands", _real_builder_cv())

    choices = {c["slug"]: c for c in profile_choices(USER)}

    assert choices["default"]["example"] is True
    assert choices["leeg"]["empty"] is True
    assert choices["nederlands"]["example"] is False
    assert choices["nederlands"]["empty"] is False


def test_the_summary_names_cv_builder_as_a_whole(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _own_cv(monkeypatch, tmp_path)
    store = ProfileStore(config.user_cv_dir(USER))
    store.save("default", _real_builder_cv("EN"))
    store.save("nederlands", _real_builder_cv("NL"))

    summary = describe_sources(USER)

    assert summary["used"][0] == "CV Builder (default, nederlands)"
    assert any("own CV" in source for source in summary["used"])


def test_the_summary_explains_why_nothing_can_be_written_yet() -> None:
    summary = describe_sources(USER)

    assert summary["used"] == []
    assert "Upload your own CV" in summary["missing"][0]


def test_an_unknown_user_is_refused() -> None:
    with pytest.raises(ApplicantError):
        gather_applicant_facts("../Jeroen")
