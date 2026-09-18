"""Importing a CV transcribes it, and says what could not be found in it."""

from __future__ import annotations

import json

import pytest

from job_scout.cv.importer import CVImportError, transcribe_cv
from job_scout.cv.models import (
    ContactSection,
    EducationSection,
    ExperienceSection,
    ListSection,
)
from tests.helpers import FakeLLMClient

CV_TEXT = (
    "Sanne Voorbeeld\nsanne@example.org\nWerkervaring\n"
    "Communicatieadviseur, Echte Werkgever B.V., sep 2021 - heden\n"
    "Opleiding\nHBO Com m unicatiem anagem ent, Hogeschool Utrecht, 2015 - 2020"
)


def _reply(**overrides: object) -> str:
    """A transcription of CV_TEXT, with any field replaced."""
    data: dict[str, object] = {
        "full_name": "Sanne Voorbeeld",
        "headline": "Communicatieadviseur",
        "profile": "",
        "contact": [
            {"kind": "email", "value": "sanne@example.org"},
            {"kind": "linkedin", "value": "linkedin.com/in/sanne-example"},
        ],
        "personal": [],
        "experience": [
            {
                "title": "Communicatieadviseur",
                "organisation": "Echte Werkgever B.V.",
                "period": "sep 2021 - heden",
                "description": "",
                "bullets": [],
            }
        ],
        "education": [
            {
                "degree": "HBO Communicatiemanagement",
                "school": "Hogeschool Utrecht",
                "period": "2015 - 2020",
            }
        ],
        "skills": ["Copywriting"],
        "lists": [{"title": "Talen", "items": ["Nederlands", "Engels"]}],
    }
    data.update(overrides)
    return "```json\n" + json.dumps(data) + "\n```"


def test_a_cv_becomes_an_editable_profile() -> None:
    doc, warnings = transcribe_cv(CV_TEXT, "NL", FakeLLMClient([_reply()]))

    assert doc.full_name == "Sanne Voorbeeld"
    assert doc.language == "NL"
    experience = next(s for s in doc.main if isinstance(s, ExperienceSection))
    assert experience.title == "Werkervaring"
    assert experience.entries[0].organisation == "Echte Werkgever B.V."
    contact = next(s for s in doc.sidebar if isinstance(s, ContactSection))
    assert [c.icon for c in contact.items] == ["envelope", "linkedin"]
    assert contact.items[1].url == "https://linkedin.com/in/sanne-example"
    assert any(isinstance(s, ListSection) and s.title == "Talen" for s in doc.main)
    assert warnings == []


def test_a_word_the_pdf_split_still_counts_as_found() -> None:
    """'Com m unicatiem anagem ent' in the text is the same study as the fix."""
    _, warnings = transcribe_cv(CV_TEXT, "NL", FakeLLMClient([_reply()]))

    education_warnings = [w for w in warnings if "Utrecht" in w or "Commun" in w]
    assert education_warnings == []


def test_an_employer_or_year_not_in_the_cv_is_reported() -> None:
    """The check that catches an invented job, and a job dated to fit."""
    invented = _reply(
        experience=[
            {
                "title": "Manager",
                "organisation": "Verzonnen Groep",
                "period": "2019 - 2021",
            }
        ]
    )

    _, warnings = transcribe_cv(CV_TEXT, "NL", FakeLLMClient([invented]))

    assert "'Verzonnen Groep' does not appear in your CV." in warnings
    assert "'2019' does not appear in your CV." in warnings


def test_a_role_from_the_linkedin_profile_is_not_reported() -> None:
    supplement = {"past_roles": [{"title": "Stagiair", "company": "LinkedIn B.V."}]}
    reply = _reply(
        experience=[
            {"title": "Stagiair", "organisation": "LinkedIn B.V.", "period": ""},
            {"title": "Adviseur", "organisation": "Echte Werkgever B.V."},
        ]
    )
    client = FakeLLMClient([reply])

    _, warnings = transcribe_cv(CV_TEXT, "NL", client, supplement=supplement)

    assert warnings == []
    assert "LinkedIn B.V." in client.calls[0][0]


def test_a_transcription_without_any_career_is_refused() -> None:
    empty = _reply(experience=[], education=[], profile="", skills=[], lists=[])

    with pytest.raises(CVImportError, match="No work experience or education"):
        transcribe_cv(CV_TEXT, "NL", FakeLLMClient([empty]))


def test_an_unusable_reply_is_refused() -> None:
    with pytest.raises(CVImportError):
        transcribe_cv(CV_TEXT, "NL", FakeLLMClient(["Sorry, I cannot help."]))


def test_an_empty_cv_is_refused_before_the_model_is_asked() -> None:
    client = FakeLLMClient([_reply()])

    with pytest.raises(CVImportError, match="too little text"):
        transcribe_cv("   ", "NL", client)

    assert client.calls == []


def test_the_english_skeleton_is_used_for_an_english_profile() -> None:
    doc, _ = transcribe_cv(CV_TEXT, "EN", FakeLLMClient([_reply()]))

    titles = [s.title for s in doc.main]
    assert "Work experience" in titles
    education = next(s for s in doc.main if isinstance(s, EducationSection))
    assert education.entries[0].school == "Hogeschool Utrecht"
