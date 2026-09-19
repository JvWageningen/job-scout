"""Automatic capture of memories after a letter or interview set is generated.

The generators are stubbed, so what is under test is the wiring: when a
capture is scheduled, with what, and that it never costs the applicant what
was just generated. The model is a FakeLLMClient; nothing touches the network.
Every person and employer here is invented.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import job_scout.config as config
from job_scout.database import Database
from job_scout.interview_answers import (
    AnswerFooting,
    InterviewAnswerError,
    InterviewAnswerSet,
    LikelyQuestion,
    QuestionKind,
)
from job_scout.interview_questions import (
    InterviewQuestion,
    InterviewQuestionSet,
    QuestionTheme,
)
from job_scout.letters.models import Letter, LetterLanguage, LetterRequest
from job_scout.memories import (
    MemoryDraft,
    MemorySource,
    add_memory,
    delete_memory,
    list_memories,
)
from job_scout.memory_extract import CLAIM_STALE_AFTER, notes_hash, set_auto_capture
from job_scout.models import JobListing, JobStatus
from job_scout.web.app import create_app
from job_scout.web.memories_api import CAPTURE_HEADER
from tests.helpers import FakeLLMClient

USER = "Sam"
COMPANY = "Deltameet Institute"
TITLE = "Meetspecialist"
NOTES = "Ik heb bij Bureau Kalibra de jaarlijkse audit twee keer zelf geleid."
REPLY = json.dumps(
    {
        "memories": [
            {
                "text": "Ik heb bij Bureau Kalibra de jaarlijkse audit twee keer "
                "zelf geleid.",
                "kind": "achievement",
                "tags": ["audit"],
                "hint": "Gebruik voor kwaliteitsrollen.",
                "use_in": ["cv", "letter", "interview"],
                "sensitive": False,
            }
        ]
    }
)


class Captures:
    """Stands in for ``capture_from_notes`` and records every scheduled call."""

    def __init__(self) -> None:
        """Start with no calls."""
        self.calls: list[dict[str, Any]] = []

    def __call__(self, user: str, notes: str, **kwargs: Any) -> list[object]:
        """Record one capture instead of running it."""
        self.calls.append({"user": user, "notes": notes, **kwargs})
        return []


@pytest.fixture
def job_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> int:
    """One user with one open vacancy, in a private data directory.

    Args:
        tmp_path: Private data root for this test.
        monkeypatch: Used to redirect config.

    Returns:
        The vacancy's id.
    """
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.yaml")
    config.write_global_config({"llm_provider": "local"})
    config.save_user_config(USER, {"name": USER})
    return Database(config.user_db_path(USER)).save_job(
        JobListing(
            title=TITLE,
            company=COMPANY,
            url="https://voorbeeld.example/vacatures/1",
            source="board",
            description="Je bewaakt de kwaliteit van onze meetmethoden.",
            fit_score=80,
            status=JobStatus.MATCHED,
        )
    )


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch) -> FakeLLMClient:
    """The model every generation route is given; it answers memory extraction."""
    client = FakeLLMClient([REPLY])
    for module in ("job_scout.letters.api", "job_scout.web.app"):
        monkeypatch.setattr(f"{module}.get_llm_client", lambda _cfg: client)
    return client


@pytest.fixture
def written(monkeypatch: pytest.MonkeyPatch, model: FakeLLMClient) -> list[object]:
    """Stub the letter writer and both interview generators.

    Returns:
        The model each stubbed generator was handed, in call order.
    """
    clients: list[object] = []

    def letter(user: str, request: LetterRequest, client: object) -> Letter:
        clients.append(client)
        return Letter(
            job_id=request.job_id,
            language=LetterLanguage.NL,
            place_date="Utrecht, 1 maart 2026",
            subject="Sollicitatie",
            salutation="Beste wervingsteam,",
            paragraphs=["Ik solliciteer graag."],
            closing="Met vriendelijke groet,",
            signature="Sam de Vries",
            generated_at=datetime(2026, 3, 1, tzinfo=UTC),
        )

    def questions(user: str, job_id: int, client: object, **_: object) -> object:
        clients.append(client)
        return InterviewQuestionSet(
            job_id=job_id,
            company=COMPANY,
            language=LetterLanguage.NL,
            questions=[
                InterviewQuestion(
                    question="Hoe loopt de audit bij jullie?",
                    theme=QuestionTheme.ROLE,
                    why="De vacature noemt de audit.",
                    grounded_in="vacancy",
                )
            ],
            generated_at=datetime(2026, 3, 1, tzinfo=UTC),
        )

    monkeypatch.setattr("job_scout.letters.api.write_letter", letter)
    monkeypatch.setattr("job_scout.web.app.generate_interview_questions", questions)
    return clients


@pytest.fixture
def captures(monkeypatch: pytest.MonkeyPatch) -> Captures:
    """Record scheduled captures instead of running them."""
    recorder = Captures()
    monkeypatch.setattr("job_scout.web.memories_api.capture_from_notes", recorder)
    return recorder


def _letter(job_id: int, notes: str) -> Any:
    """Generate a letter through the API with these notes."""
    return TestClient(create_app()).post(
        f"/api/letters/generate?user={USER}", json={"job_id": job_id, "notes": notes}
    )


def _questions(job_id: int, notes: str) -> Any:
    """Generate the questions to ask through the API with these notes."""
    return TestClient(create_app()).post(
        f"/api/interview/questions?user={USER}",
        json={"job_id": job_id, "notes": notes},
    )


def test_letter_notes_are_captured_after_the_letter(
    job_id: int, written: list[object], captures: Captures
) -> None:
    """Once, with the vacancy named, and with the model the letter used."""
    response = _letter(job_id, NOTES)

    assert response.status_code == 200, response.text
    assert response.headers[CAPTURE_HEADER] == "started"
    assert captures.calls == [
        {
            "user": USER,
            "notes": NOTES,
            "source": MemorySource.LETTER_NOTES,
            "source_detail": f"notes for the {TITLE} letter to {COMPANY}",
            "job_id": job_id,
            "client": written[0],
        }
    ]


def test_interview_notes_are_captured_after_the_questions(
    job_id: int, written: list[object], captures: Captures
) -> None:
    """The interview routes schedule the same capture, as interview notes."""
    response = _questions(job_id, NOTES)

    assert response.status_code == 200, response.text
    assert response.headers[CAPTURE_HEADER] == "started"
    assert len(captures.calls) == 1
    call = captures.calls[0]
    assert call["source"] is MemorySource.INTERVIEW_NOTES
    assert call["source_detail"] == f"notes for the {TITLE} interview at {COMPANY}"
    assert call["job_id"] == job_id
    assert call["client"] is written[0]


def _answers(job_id: int, notes: str) -> Any:
    """Generate the likely questions and draft answers through the API."""
    return TestClient(create_app()).post(
        f"/api/interview/answers?user={USER}", json={"job_id": job_id, "notes": notes}
    )


def test_interview_notes_are_captured_after_the_answers_too(
    job_id: int, captures: Captures, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half schedules the same capture, as interview notes."""

    def answers(user: str, job_id: int, client: object, **_: object) -> object:
        return InterviewAnswerSet(
            job_id=job_id,
            company=COMPANY,
            language=LetterLanguage.NL,
            questions=[
                LikelyQuestion(
                    question="Hoe pak je een audit aan?",
                    kind=QuestionKind.EXPERIENCE,
                    why_asked="De vacature noemt de audit.",
                    draft_answer="Bij Bureau Kalibra leidde ik de audit zelf.",
                    footing=AnswerFooting.STRONG,
                )
            ],
            generated_at=datetime(2026, 3, 1, tzinfo=UTC),
        )

    monkeypatch.setattr("job_scout.web.app.generate_interview_answers", answers)
    monkeypatch.setattr("job_scout.web.app.get_llm_client", lambda _cfg: "model")

    response = _answers(job_id, NOTES)

    assert response.status_code == 200, response.text
    assert response.headers[CAPTURE_HEADER] == "started"
    assert [(c["source"], c["client"]) for c in captures.calls] == [
        (MemorySource.INTERVIEW_NOTES, "model")
    ]


def test_a_failed_generation_captures_nothing(
    job_id: int, captures: Captures, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The notes are read again only after something was generated from them."""

    def answers(user: str, job_id: int, client: object, **_: object) -> object:
        raise InterviewAnswerError("Select a vacancy with a description.")

    monkeypatch.setattr("job_scout.web.app.generate_interview_answers", answers)
    monkeypatch.setattr("job_scout.web.app.get_llm_client", lambda _cfg: "model")

    response = _answers(job_id, NOTES)

    assert response.status_code == 400
    assert captures.calls == []


@pytest.mark.parametrize("notes", ["", "   ", "kort houden"])
def test_no_capture_without_notes_worth_reading(
    job_id: int, written: list[object], captures: Captures, notes: str
) -> None:
    """No notes, or an instruction of two words, is not read again."""
    letter = _letter(job_id, notes)
    questions = _questions(job_id, notes)

    for response in (letter, questions):
        assert response.status_code == 200, response.text
        assert CAPTURE_HEADER not in response.headers
    assert captures.calls == []


def test_no_capture_when_the_user_switched_it_off(
    job_id: int, written: list[object], captures: Captures
) -> None:
    """The per-user switch is respected before anything is scheduled."""
    set_auto_capture(USER, False)

    response = _letter(job_id, NOTES)

    assert response.status_code == 200
    assert CAPTURE_HEADER not in response.headers
    assert captures.calls == []


def test_notes_already_captured_are_not_captured_again(
    job_id: int, written: list[object], captures: Captures
) -> None:
    """Generating again with the same notes schedules nothing."""
    db = Database(config.user_db_path(USER))
    key = notes_hash(NOTES)
    stale = datetime.now(UTC) - CLAIM_STALE_AFTER
    assert db.claim_notes_capture(
        key, source="letter_notes", job_id=job_id, stale_before=stale
    )
    db.complete_notes_capture(key, 1)

    response = _questions(job_id, NOTES)

    assert response.status_code == 200
    assert CAPTURE_HEADER not in response.headers
    assert captures.calls == []


def test_a_failing_capture_check_never_costs_the_letter(
    job_id: int,
    written: list[object],
    captures: Captures,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The letter comes back; only the capture is skipped, and logged."""

    def broken(user: str, notes: str) -> bool:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr("job_scout.web.memories_api.capture_pending", broken)

    response = _letter(job_id, NOTES)

    assert response.status_code == 200, response.text
    assert response.json()["paragraphs"] == ["Ik solliciteer graag."]
    assert CAPTURE_HEADER not in response.headers
    assert captures.calls == []


def test_the_capture_stores_the_facts_from_the_notes_once(
    job_id: int, written: list[object], model: FakeLLMClient
) -> None:
    """End to end: the real capture runs after the letter, then not again."""
    first = _letter(job_id, NOTES)

    assert first.headers[CAPTURE_HEADER] == "started"
    stored = list_memories(USER)
    assert [m.text for m in stored] == [
        "Ik heb bij Bureau Kalibra de jaarlijkse audit twee keer zelf geleid."
    ]
    assert stored[0].source is MemorySource.LETTER_NOTES
    assert stored[0].source_detail == f"notes for the {TITLE} letter to {COMPANY}"
    assert stored[0].job_id == job_id
    assert [purpose for _prompt, purpose in model.calls] == ["cv_parsing"]

    again = _letter(job_id, NOTES)

    assert again.status_code == 200
    assert CAPTURE_HEADER not in again.headers
    assert len(model.calls) == 1
    assert len(list_memories(USER)) == 1


def test_a_capture_sends_deleted_wording_but_never_a_private_memory(
    job_id: int, written: list[object], model: FakeLLMClient
) -> None:
    """What the Memories tab says a capture sends is what the prompt holds.

    A deleted memory goes along when it touches the notes, as the tab says,
    and one about something else does not.
    """
    kept = add_memory(USER, MemoryDraft(text="Ik spreek vloeiend Duits."))
    private = add_memory(USER, MemoryDraft(text="Ik ben mantelzorger.", sensitive=True))
    deleted = add_memory(
        USER, MemoryDraft(text="Ik leidde de audit bij Bureau Kalibra in 2021.")
    )
    unrelated = add_memory(USER, MemoryDraft(text="Ik rijd graag naar Groningen."))
    deleted_private = add_memory(
        USER,
        MemoryDraft(
            text="Ik zorg twee dagen per week voor mijn moeder.", sensitive=True
        ),
    )
    for memory in (deleted, unrelated, deleted_private):
        assert delete_memory(USER, memory.id)

    _letter(job_id, NOTES)

    prompt = model.calls[0][0]
    assert kept.text in prompt
    assert deleted.text in prompt
    assert unrelated.text not in prompt
    assert private.text not in prompt
    assert deleted_private.text not in prompt


def test_a_vacancy_without_a_title_or_company_is_still_named(
    job_id: int, written: list[object], captures: Captures
) -> None:
    """The origin is a label for the applicant; it never breaks the capture."""
    db = Database(config.user_db_path(USER))
    other = db.save_job(
        JobListing(
            title="",
            company="",
            url="https://voorbeeld.example/vacatures/2",
            source="board",
            description="Een vacature zonder titel.",
        )
    )

    _letter(other, NOTES)

    assert captures.calls[0]["source_detail"] == "notes for the letter"
