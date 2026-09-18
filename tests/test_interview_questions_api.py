"""Contracts for the API and shortlist behind the questions the candidate asks.

The generator itself is covered by tests/test_interview_questions.py; here the
generator is stubbed so the endpoints, their error mapping and the shared
vacancy shortlist are what is under test.

Every person and employer below is invented. The repository is public, so no
fixture may carry a real name, address or company.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import job_scout.config as config
from job_scout.cv.models import CVDocument, TextSection
from job_scout.cv.storage import ProfileStore
from job_scout.database import Database
from job_scout.interview_questions import (
    InterviewQuestion,
    InterviewQuestionError,
    InterviewQuestionSet,
    QuestionTheme,
)
from job_scout.letters.models import LetterLanguage
from job_scout.llm.base import LLMError
from job_scout.models import ApplicationStage, JobListing, JobStatus
from job_scout.web.app import create_app
from job_scout.web.vacancies import VacancyUpdate, update_vacancy
from tests.helpers import FakeLLMClient

USER = "Sam"
OTHER = "Robin"
COMPANY = "Deltameet Institute"
DESCRIPTION = (
    "Je bewaakt de kwaliteit van onze meetmethoden en ondersteunt het team "
    "bij de jaarlijkse audits."
)
VAGUE = "The model could not complete the request. Check LLM settings and retry."
TOKEN = "fictional-test-token"

Generator = Callable[..., InterviewQuestionSet]


def add_job(db: Database, score: int, **values: object) -> int:
    """Insert a vacancy that may be prepared for unless a test says otherwise.

    Args:
        db: The user's database.
        score: Fit score, also used to keep the titles apart.
        values: Fields overriding the defaults.

    Returns:
        The stored vacancy's id.
    """
    data: dict[str, object] = dict(
        title=f"Meetspecialist {score}",
        company=COMPANY,
        url=f"https://voorbeeld.example/vacatures/{score}",
        source="board",
        description=DESCRIPTION,
        fit_score=score,
        status=JobStatus.MATCHED,
        seen_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    data.update(values)
    return db.save_job(JobListing.model_validate(data))


def question_set(job_id: int) -> InterviewQuestionSet:
    """Build the answer a successful generation would produce.

    Args:
        job_id: Vacancy the questions belong to.

    Returns:
        One question with its grounding and one absent source.
    """
    return InterviewQuestionSet(
        job_id=job_id,
        company=COMPANY,
        language=LetterLanguage.NL,
        questions=[
            InterviewQuestion(
                question="Hoe vangt het team de piek rond de audits op?",
                theme=QuestionTheme.CONCERNS,
                why="De reviews noemen werkdruk, en dat raakt dit werk direct.",
                grounded_in="company review: cons",
            )
        ],
        generated_at=datetime(2026, 3, 1, tzinfo=UTC),
        missing_context=["no company research yet"],
    )


def recording_generator(seen: list[dict[str, object]]) -> Generator:
    """Stand in for the generator and record what the endpoint passed on.

    Args:
        seen: List each call's arguments are appended to.

    Returns:
        A drop-in replacement for ``generate_interview_questions``.
    """

    def fake(
        user: str,
        job_id: int,
        client: object,
        *,
        language: LetterLanguage | None = None,
        cv_slug: str | None = None,
        notes: str = "",
        now: datetime | None = None,
    ) -> InterviewQuestionSet:
        seen.append(
            {
                "user": user,
                "job_id": job_id,
                "language": language,
                "cv_slug": cv_slug,
                "notes": notes,
            }
        )
        return question_set(job_id)

    return fake


def failing_generator(error: Exception) -> Generator:
    """Stand in for the generator and fail the way the real one can.

    Args:
        error: The exception every call raises.

    Returns:
        A drop-in replacement for ``generate_interview_questions``.
    """

    def fake(*args: object, **kwargs: object) -> InterviewQuestionSet:
        raise error

    return fake


@pytest.fixture
def job_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> int:
    """Two users with a saved CV, one of whom has a vacancy worth preparing for.

    Args:
        tmp_path: Private data root for this test.
        monkeypatch: Used to redirect config and the LLM client factory.

    Returns:
        The id of the vacancy belonging to ``USER``.
    """
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.yaml")
    config.write_global_config({"llm_provider": "local"})
    for user in (USER, OTHER):
        config.save_user_config(user, {})
        ProfileStore(config.user_cv_dir(user)).save(
            "default",
            CVDocument(
                full_name=f"{user} de Vries",
                language="NL",
                main=[
                    TextSection(
                        title="Werkervaring",
                        body="Meetwerk bij Bureau Kalibra sinds 2021.",
                    )
                ],
            ),
        )
    monkeypatch.setattr(
        "job_scout.web.app.get_llm_client", lambda _: FakeLLMClient(["{}"])
    )
    return add_job(Database(config.user_db_path(USER)), 88)


def test_questions_are_generated_from_the_request_the_caller_sent(
    job_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every field of the request has to reach the generator to matter."""
    seen: list[dict[str, object]] = []
    monkeypatch.setattr(
        "job_scout.web.app.generate_interview_questions", recording_generator(seen)
    )

    response = TestClient(create_app()).post(
        f"/api/interview/questions?user={USER}",
        json={
            "job_id": job_id,
            "language": "nl",
            "cv_slug": "default",
            "notes": "Ik wil weten hoe de audits lopen.",
        },
    )

    assert response.status_code == 200, response.text
    assert seen == [
        {
            "user": USER,
            "job_id": job_id,
            "language": LetterLanguage.NL,
            "cv_slug": "default",
            "notes": "Ik wil weten hoe de audits lopen.",
        }
    ]
    body = response.json()
    assert body["company"] == COMPANY
    assert body["missing_context"] == ["no company research yet"]
    assert body["questions"][0]["theme"] == "concerns"
    assert body["questions"][0]["grounded_in"] == "company review: cons"
    assert response.headers["cache-control"] == "no-store"


def test_an_omitted_language_is_left_to_the_generator_to_detect(
    job_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing in the API may guess a language the vacancy can answer for."""
    seen: list[dict[str, object]] = []
    monkeypatch.setattr(
        "job_scout.web.app.generate_interview_questions", recording_generator(seen)
    )

    response = TestClient(create_app()).post(
        f"/api/interview/questions?user={USER}", json={"job_id": job_id}
    )

    assert response.status_code == 200, response.text
    assert seen[0]["language"] is None
    assert seen[0]["cv_slug"] is None
    assert seen[0]["notes"] == ""


def test_the_context_offers_vacancies_best_match_first(job_id: int) -> None:
    """The dropdown is only obviously ordered if the best match is on top."""
    db = Database(config.user_db_path(USER))
    add_job(db, 41)
    add_job(db, 93)

    body = TestClient(create_app()).get(f"/api/interview/context?user={USER}").json()

    assert [job["fit_score"] for job in body["jobs"]] == [93, 88, 41]
    assert body["jobs"][0]["company"] == COMPANY
    assert body["jobs"][0]["status"] == "matched"
    assert body["profiles"] == [
        {"slug": "default", "language": "NL", "example": False, "empty": False}
    ]


def test_the_context_describes_sources_exactly_as_the_letter_tab_does(
    job_id: int,
) -> None:
    """One source summary for both tabs, so they cannot tell different stories."""
    client = TestClient(create_app())
    interview = client.get(f"/api/interview/context?user={USER}").json()
    letters = client.get(f"/api/letters/context?user={USER}").json()

    assert interview["sources"]["used"][0] == "CV Builder (default)"
    assert interview["sources"] == letters["sources"]
    assert interview["profiles"] == letters["profiles"]


@pytest.mark.parametrize(
    ("label", "status"),
    [
        ("rejected by the pipeline", JobStatus.REJECTED),
        ("no longer advertised", JobStatus.EXPIRED),
    ],
)
def test_vacancies_that_are_over_are_not_offered(
    job_id: int, label: str, status: JobStatus
) -> None:
    """Preparing an interview for a dead vacancy is wasted work."""
    db = Database(config.user_db_path(USER))
    add_job(db, 95, title=f"Excluded: {label}", status=status)

    body = TestClient(create_app()).get(f"/api/interview/context?user={USER}").json()

    assert [job["id"] for job in body["jobs"]] == [job_id]


def test_a_vacancy_the_applicant_closed_is_not_offered(job_id: int) -> None:
    """Closing is an applicant action, so it is made the way a person makes it."""
    db = Database(config.user_db_path(USER))
    closed = add_job(db, 95, title="Closed by the applicant")
    update_vacancy(db, closed, VacancyUpdate(user=USER, stage=ApplicationStage.CLOSED))

    body = TestClient(create_app()).get(f"/api/interview/context?user={USER}").json()

    assert [job["id"] for job in body["jobs"]] == [job_id]


def test_a_vacancy_without_a_description_is_not_offered(job_id: int) -> None:
    """There is nothing to ground a question in without the posting text."""
    add_job(Database(config.user_db_path(USER)), 95, description=None)

    body = TestClient(create_app()).get(f"/api/interview/context?user={USER}").json()

    assert [job["id"] for job in body["jobs"]] == [job_id]


def test_one_users_vacancies_never_reach_another(job_id: int) -> None:
    """Two applicants share a dashboard; their shortlists must not mix."""
    add_job(Database(config.user_db_path(OTHER)), 99, title="Robin only")

    client = TestClient(create_app())
    mine = client.get(f"/api/interview/context?user={USER}").json()["jobs"]
    theirs = client.get(f"/api/interview/context?user={OTHER}").json()["jobs"]

    assert [job["id"] for job in mine] == [job_id]
    assert [job["title"] for job in theirs] == ["Robin only"]


def test_an_unusable_request_is_the_callers_to_fix(
    job_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing vacancy or CV is a 400: the caller can do something about it."""
    monkeypatch.setattr(
        "job_scout.web.app.generate_interview_questions",
        failing_generator(InterviewQuestionError("Vacancy 4321 no longer exists.")),
    )

    response = TestClient(create_app()).post(
        f"/api/interview/questions?user={USER}", json={"job_id": job_id}
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Vacancy 4321 no longer exists."


def test_an_unknown_user_never_reaches_the_generator(job_id: int) -> None:
    """A traversal attempt is refused before any private path is built."""
    client = TestClient(create_app())

    assert client.get("/api/interview/context?user=../Sam").status_code == 400
    assert (
        client.post(
            "/api/interview/questions?user=Nobody", json={"job_id": job_id}
        ).status_code
        == 400
    )


def test_a_provider_failure_stays_vague_to_the_client(
    job_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Provider errors can carry endpoints and key fragments; the log gets those."""
    monkeypatch.setattr(
        "job_scout.web.app.generate_interview_questions",
        failing_generator(
            LLMError("401 from https://llm.internal.example/v1 key sk-1")
        ),
    )

    response = TestClient(create_app()).post(
        f"/api/interview/questions?user={USER}", json={"job_id": job_id}
    )

    assert response.status_code == 502
    assert response.json()["detail"] == VAGUE
    assert "llm.internal.example" not in response.text
    assert "sk-1" not in response.text


def test_a_malformed_body_is_rejected_before_any_model_call(
    job_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The request model is the first gate; a bad job id never costs a call."""
    seen: list[dict[str, object]] = []
    monkeypatch.setattr(
        "job_scout.web.app.generate_interview_questions", recording_generator(seen)
    )

    response = TestClient(create_app()).post(
        f"/api/interview/questions?user={USER}", json={"job_id": 0}
    )

    assert response.status_code == 422
    assert seen == []


def test_both_endpoints_sit_behind_the_dashboard_token(
    job_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The routes live under /api/ precisely so the middleware covers them."""
    monkeypatch.setattr(
        "job_scout.web.app.load_secrets", lambda: {"dashboard_token": TOKEN}
    )
    seen: list[dict[str, object]] = []
    monkeypatch.setattr(
        "job_scout.web.app.generate_interview_questions", recording_generator(seen)
    )

    with TestClient(create_app()) as client:
        assert client.get(f"/api/interview/context?user={USER}").status_code == 401
        assert (
            client.post(
                f"/api/interview/questions?user={USER}", json={"job_id": job_id}
            ).status_code
            == 401
        )
        assert seen == []

        client.headers["Authorization"] = f"Bearer {TOKEN}"
        assert client.get(f"/api/interview/context?user={USER}").status_code == 200
        assert (
            client.post(
                f"/api/interview/questions?user={USER}", json={"job_id": job_id}
            ).status_code
            == 200
        )


def test_the_command_does_not_call_a_thin_review_unseen(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A thin review was in the prompt; only absent sources are "not seen"."""
    from job_scout.cli import _print_interview_questions
    from job_scout.interview_questions import NO_PUBLIC_INFO, THIN_REVIEW

    result = question_set(7).model_copy(
        update={"missing_context": [NO_PUBLIC_INFO, THIN_REVIEW]}
    )
    _print_interview_questions(result)
    printed = capsys.readouterr().out
    unseen = printed.split("Not seen, so nothing above is based on it:")[1]
    assert NO_PUBLIC_INFO in unseen
    assert THIN_REVIEW not in printed
    assert "rests on little web evidence" in printed
