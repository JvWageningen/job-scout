"""Contracts for the API and CLI behind the answers this candidate would give.

The generator itself is covered by tests/test_interview_answers.py; here it is
stubbed, so what is under test is the endpoint, the command, the error mapping
both of them share with the question side, and the fact that the older
interview-prep endpoint and command still work beside them.

Every person and employer below is invented. The repository is public, so no
fixture may carry a real name, address or company.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient
from loguru import logger

import job_scout.config as config
from job_scout.cli import cli
from job_scout.cv.models import CVDocument, TextSection
from job_scout.cv.storage import ProfileStore
from job_scout.database import Database
from job_scout.interview_answers import (
    AnswerFooting,
    InterviewAnswerError,
    InterviewAnswerSet,
    LikelyQuestion,
    QuestionKind,
)
from job_scout.letters.models import LetterLanguage
from job_scout.llm.base import LLMError
from job_scout.models import JobListing, JobStatus
from job_scout.web.app import create_app
from tests.helpers import FakeLLMClient

USER = "Sam"
OTHER = "Robin"
COMPANY = "Deltameet Institute"
DESCRIPTION = (
    "Je bewaakt de kwaliteit van onze meetmethoden en ondersteunt het team "
    "bij de jaarlijkse audits."
)
STRONG_ANSWER = "Bij Bureau Kalibra draaide ik de jaarlijkse audit, en dat ging goed."
GAP_ANSWER = "Die norm ken ik niet. Ik zou me er graag in verdiepen."
VAGUE = "The model could not complete the request. Check LLM settings and retry."
TOKEN = "fictional-test-token"
PREP_RESPONSE = (
    '{"questions": [{"question": "Vertel over een audit die misging", '
    '"keywords": ["audit", "kwaliteit"]}]}'
)

Generator = Callable[..., InterviewAnswerSet]


def add_job(db: Database, score: int, **values: object) -> int:
    """Insert a vacancy an interview may be prepared for.

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


def answer_set(job_id: int) -> InterviewAnswerSet:
    """Build the answer a successful generation would produce.

    One question the candidate can carry and one they cannot, because the
    difference between those two is the whole point of the footing.

    Args:
        job_id: Vacancy the questions belong to.

    Returns:
        A set with a strong answer, a gap answer and one absent source.
    """
    return InterviewAnswerSet(
        job_id=job_id,
        company=COMPANY,
        language=LetterLanguage.NL,
        questions=[
            LikelyQuestion(
                question="Hoe pak je een jaarlijkse audit aan?",
                kind=QuestionKind.EXPERIENCE,
                why_asked="De vacature noemt de jaarlijkse audits met zoveel woorden.",
                draft_answer=STRONG_ANSWER,
                based_on=["STAR story 3"],
                footing=AnswerFooting.STRONG,
            ),
            LikelyQuestion(
                question="Werk je met ISO 17025?",
                kind=QuestionKind.GAP,
                why_asked="De vacature vraagt die norm, je CV noemt hem nergens.",
                draft_answer=GAP_ANSWER,
                footing=AnswerFooting.GAP,
            ),
        ],
        missing_context=["no company research yet"],
        generated_at=datetime(2026, 3, 1, tzinfo=UTC),
    )


def recording_generator(seen: list[dict[str, object]]) -> Generator:
    """Stand in for the generator and record what the caller passed on.

    Args:
        seen: List each call's arguments are appended to.

    Returns:
        A drop-in replacement for ``generate_interview_answers``.
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
    ) -> InterviewAnswerSet:
        seen.append(
            {
                "user": user,
                "job_id": job_id,
                "language": language,
                "cv_slug": cv_slug,
                "notes": notes,
            }
        )
        return answer_set(job_id)

    return fake


def failing_generator(error: Exception) -> Generator:
    """Stand in for the generator and fail the way the real one can.

    Args:
        error: The exception every call raises.

    Returns:
        A drop-in replacement for ``generate_interview_answers``.
    """

    def fake(*args: object, **kwargs: object) -> InterviewAnswerSet:
        raise error

    return fake


@pytest.fixture
def job_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> int:
    """Two users with a saved CV, one of whom has a vacancy and a STAR story.

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
    db = Database(config.user_db_path(USER))
    db.save_star_story(
        situation="De jaarlijkse audit stond voor de deur.",
        task="De meetmethoden moesten aantoonbaar kloppen.",
        action="Ik heb de procedures herschreven en het team bijgepraat.",
        result="De audit leverde geen enkele afwijking op.",
        keywords=["audit", "kwaliteit"],
    )
    return add_job(db, 88)


@pytest.fixture
def cli_ready(job_id: int, monkeypatch: pytest.MonkeyPatch) -> int:
    """The same data, with the CLI's provider checks stubbed out.

    Args:
        job_id: The vacancy fixture, which also lays down the config.
        monkeypatch: Used to stub the provider check and the client factory.

    Returns:
        The id of the vacancy belonging to ``USER``.
    """
    monkeypatch.setattr("job_scout.cli.check_llm_available", lambda _: (True, None))
    monkeypatch.setattr("job_scout.cli.get_llm_client", lambda _: FakeLLMClient(["{}"]))
    return job_id


@pytest.fixture
def errors_logged() -> Iterator[list[str]]:
    """Capture error-level logging, where a hidden cause has to end up.

    Yields:
        The formatted messages logged at error level during the test.
    """
    captured: list[str] = []

    def sink(message: Any) -> None:
        captured.append(str(message))

    sink_id = logger.add(sink, level="ERROR")
    try:
        yield captured
    finally:
        logger.remove(sink_id)


def test_answers_are_generated_from_the_request_the_caller_sent(
    job_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every field of the request has to reach the generator to matter."""
    seen: list[dict[str, object]] = []
    monkeypatch.setattr(
        "job_scout.web.app.generate_interview_answers", recording_generator(seen)
    )

    response = TestClient(create_app()).post(
        f"/api/interview/answers?user={USER}",
        json={
            "job_id": job_id,
            "language": "nl",
            "cv_slug": "default",
            "notes": "Ik vertrek omdat het team is opgeheven.",
        },
    )

    assert response.status_code == 200, response.text
    assert seen == [
        {
            "user": USER,
            "job_id": job_id,
            "language": LetterLanguage.NL,
            "cv_slug": "default",
            "notes": "Ik vertrek omdat het team is opgeheven.",
        }
    ]
    body = response.json()
    assert body["company"] == COMPANY
    assert body["missing_context"] == ["no company research yet"]
    assert body["questions"][0]["kind"] == "experience"
    assert body["questions"][0]["draft_answer"] == STRONG_ANSWER
    assert body["questions"][0]["based_on"] == ["STAR story 3"]
    assert body["questions"][1]["footing"] == "gap"
    assert response.headers["cache-control"] == "no-store"


def test_an_omitted_language_is_left_to_the_generator_to_detect(
    job_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing in the API may guess what the vacancy can answer for itself."""
    seen: list[dict[str, object]] = []
    monkeypatch.setattr(
        "job_scout.web.app.generate_interview_answers", recording_generator(seen)
    )

    response = TestClient(create_app()).post(
        f"/api/interview/answers?user={USER}", json={"job_id": job_id}
    )

    assert response.status_code == 200, response.text
    assert seen[0]["language"] is None
    assert seen[0]["cv_slug"] is None
    assert seen[0]["notes"] == ""


def test_both_directions_share_one_context_endpoint(job_id: int) -> None:
    """A second shortlist could offer a vacancy the other side refuses."""
    app = create_app()

    documented = app.openapi()["paths"]
    assert {path for path in documented if path.startswith("/api/interview/")} == {
        "/api/interview/context",
        "/api/interview/questions",
        "/api/interview/answers",
    }
    assert "/api/interview-prep/{job_id}" in documented

    body = TestClient(app).get(f"/api/interview/context?user={USER}").json()
    assert [job["id"] for job in body["jobs"]] == [job_id]
    assert body["profiles"] == [
        {"slug": "default", "language": "NL", "example": False, "empty": False}
    ]
    assert "used" in body["sources"]
    assert "missing" in body["sources"]


def test_an_unusable_request_is_the_callers_to_fix(
    job_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing vacancy or CV is a 400: the caller can do something about it."""
    monkeypatch.setattr(
        "job_scout.web.app.generate_interview_answers",
        failing_generator(InterviewAnswerError("Vacancy 4321 no longer exists.")),
    )

    response = TestClient(create_app()).post(
        f"/api/interview/answers?user={USER}", json={"job_id": job_id}
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Vacancy 4321 no longer exists."


def test_an_unknown_user_never_reaches_the_generator(job_id: int) -> None:
    """A traversal attempt is refused before any private path is built."""
    client = TestClient(create_app())

    assert (
        client.post(
            "/api/interview/answers?user=Nobody", json={"job_id": job_id}
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/interview/answers?user=../Sam", json={"job_id": job_id}
        ).status_code
        == 400
    )


def test_a_provider_failure_stays_vague_to_the_client(
    job_id: int, monkeypatch: pytest.MonkeyPatch, errors_logged: list[str]
) -> None:
    """Provider errors can carry endpoints and key fragments; the log gets those."""
    monkeypatch.setattr(
        "job_scout.web.app.generate_interview_answers",
        failing_generator(
            LLMError("401 from https://llm.internal.example/v1 key sk-1")
        ),
    )

    response = TestClient(create_app()).post(
        f"/api/interview/answers?user={USER}", json={"job_id": job_id}
    )

    assert response.status_code == 502
    assert response.json()["detail"] == VAGUE
    assert "llm.internal.example" not in response.text
    assert "sk-1" not in response.text
    assert any("llm.internal.example" in message for message in errors_logged)


def test_a_malformed_body_is_rejected_before_any_model_call(
    job_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The request model is the first gate; a bad job id never costs a call."""
    seen: list[dict[str, object]] = []
    monkeypatch.setattr(
        "job_scout.web.app.generate_interview_answers", recording_generator(seen)
    )

    response = TestClient(create_app()).post(
        f"/api/interview/answers?user={USER}", json={"job_id": 0}
    )

    assert response.status_code == 422
    assert seen == []


def test_the_answers_endpoint_sits_behind_the_dashboard_token(
    job_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The route lives under /api/ precisely so the middleware covers it."""
    monkeypatch.setattr(
        "job_scout.web.app.load_secrets", lambda: {"dashboard_token": TOKEN}
    )
    seen: list[dict[str, object]] = []
    monkeypatch.setattr(
        "job_scout.web.app.generate_interview_answers", recording_generator(seen)
    )

    with TestClient(create_app()) as client:
        assert (
            client.post(
                f"/api/interview/answers?user={USER}", json={"job_id": job_id}
            ).status_code
            == 401
        )
        assert seen == []

        client.headers["Authorization"] = f"Bearer {TOKEN}"
        assert (
            client.post(
                f"/api/interview/answers?user={USER}", json={"job_id": job_id}
            ).status_code
            == 200
        )
        assert seen != []


def test_the_command_prints_each_answer_with_its_footing(
    cli_ready: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The footing decides what to rehearse, so it cannot be buried."""
    seen: list[dict[str, object]] = []
    monkeypatch.setattr(
        "job_scout.cli.generate_interview_answers", recording_generator(seen)
    )

    result = CliRunner().invoke(
        cli,
        [
            "interview",
            "answers",
            str(cli_ready),
            "--user",
            USER,
            "--language",
            "nl",
            "--cv",
            "default",
            "--notes",
            "Het team is opgeheven.",
        ],
    )

    assert result.exit_code == 0, result.output
    assert seen == [
        {
            "user": USER,
            "job_id": cli_ready,
            "language": LetterLanguage.NL,
            "cv_slug": "default",
            "notes": "Het team is opgeheven.",
        }
    ]
    assert f"Likely questions from {COMPANY}" in result.output
    assert "[experience]" in result.output
    assert "asked because: De vacature noemt de jaarlijkse audits" in result.output
    assert STRONG_ANSWER in result.output
    assert "based on: STAR story 3" in result.output
    assert "footing: STRONG" in result.output
    assert "!!" in result.output
    assert "footing: GAP" in result.output
    assert GAP_ANSWER in result.output
    assert "no company research yet" in result.output


def test_an_auto_language_is_left_to_the_generator(
    cli_ready: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default must reach the generator as 'decide it yourself', not as a guess."""
    seen: list[dict[str, object]] = []
    monkeypatch.setattr(
        "job_scout.cli.generate_interview_answers", recording_generator(seen)
    )

    result = CliRunner().invoke(
        cli, ["interview", "answers", str(cli_ready), "--user", USER]
    )

    assert result.exit_code == 0, result.output
    assert seen[0]["language"] is None
    assert seen[0]["cv_slug"] is None


def test_a_generator_failure_is_a_clean_command_error(
    cli_ready: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing CV is the user's to fix; a traceback tells them nothing."""
    monkeypatch.setattr(
        "job_scout.cli.generate_interview_answers",
        failing_generator(InterviewAnswerError("No usable CV for these answers.")),
    )

    result = CliRunner().invoke(
        cli, ["interview", "answers", str(cli_ready), "--user", USER]
    )

    assert result.exit_code == 1
    assert "No usable CV for these answers." in result.output
    assert "Traceback" not in result.output


def test_a_provider_failure_reaches_the_command_line_without_a_traceback(
    cli_ready: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CLI is used by one person on one machine; the cause may be shown."""
    monkeypatch.setattr(
        "job_scout.cli.generate_interview_answers",
        failing_generator(LLMError("the model timed out")),
    )

    result = CliRunner().invoke(
        cli, ["interview", "answers", str(cli_ready), "--user", USER]
    )

    assert result.exit_code == 1
    assert "the model timed out" in result.output
    assert "Traceback" not in result.output


def test_the_older_interview_prep_endpoint_still_answers(
    job_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The behavioural-question route predates this one and keeps its contract."""
    monkeypatch.setattr(
        "job_scout.llm.factory.get_llm_client", lambda _: FakeLLMClient([PREP_RESPONSE])
    )

    response = TestClient(create_app()).get(f"/api/interview-prep/{job_id}?user={USER}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["behavioral_questions"][0]["question"] == (
        "Vertel over een audit die misging"
    )
    assert body["job_id"] == job_id


def test_the_older_interview_prep_command_still_runs(
    cli_ready: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """'profile interview-prep' is a separate command and stays untouched."""
    monkeypatch.setattr(
        "job_scout.llm.factory.get_llm_client", lambda _: FakeLLMClient([PREP_RESPONSE])
    )

    result = CliRunner().invoke(
        cli, ["profile", "interview-prep", str(cli_ready), "--user", USER]
    )

    assert result.exit_code == 0, result.output
    assert f"Interview Preparation for Job #{cli_ready}" in result.output
    assert "Vertel over een audit die misging" in result.output
