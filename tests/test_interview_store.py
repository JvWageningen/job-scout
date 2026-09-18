"""Saved interview sets: one per user, vacancy, half and language.

Every person and employer below is invented. The repository is public, so no
fixture may carry a real name, address or company.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

import job_scout.config as config
from job_scout.database import Database
from job_scout.interview_answers import (
    AnswerFooting,
    InterviewAnswerSet,
    LikelyQuestion,
    QuestionKind,
)
from job_scout.interview_questions import (
    InterviewQuestion,
    InterviewQuestionSet,
    QuestionTheme,
)
from job_scout.interview_store import (
    InterviewMode,
    InterviewStoreError,
    interview_set_path,
    latest_interview_set,
    load_interview_set,
    load_saved_interview,
    save_edited_answers,
    save_interview_set,
)
from job_scout.letters.models import LetterLanguage
from job_scout.letters.writer import LetterError
from job_scout.models import JobListing, JobStatus

USER = "Sam"
OTHER = "Robin"
COMPANY = "Deltameet Institute"


def question_set(
    job_id: int,
    question: str = "Hoe vangt het team de piek rond de audits op?",
    language: LetterLanguage = LetterLanguage.NL,
    day: int = 1,
) -> InterviewQuestionSet:
    """Build a one-question set to ask the employer.

    Args:
        job_id: The vacancy.
        question: The question, so two sets can be told apart.
        language: The set's language.
        day: Day of March 2026 it was generated on.

    Returns:
        The set.
    """
    return InterviewQuestionSet(
        job_id=job_id,
        company=COMPANY,
        language=language,
        questions=[
            InterviewQuestion(
                question=question,
                theme=QuestionTheme.CONCERNS,
                why="De reviews noemen werkdruk.",
                grounded_in="company review: cons",
            )
        ],
        generated_at=datetime(2026, 3, day, 9, tzinfo=UTC),
    )


def answer_set(
    job_id: int, answer: str = "Bij Bureau Kalibra draaide ik de audit."
) -> InterviewAnswerSet:
    """Build a one-question set of likely questions with a draft answer.

    Args:
        job_id: The vacancy.
        answer: The draft answer, so an edit can be recognised.

    Returns:
        The set.
    """
    return InterviewAnswerSet(
        job_id=job_id,
        company=COMPANY,
        language=LetterLanguage.NL,
        questions=[
            LikelyQuestion(
                question="Hoe pak je een jaarlijkse audit aan?",
                kind=QuestionKind.EXPERIENCE,
                why_asked="De vacature noemt de audits.",
                draft_answer=answer,
                based_on=["STAR story 3"],
                footing=AnswerFooting.STRONG,
            )
        ],
        generated_at=datetime(2026, 3, 2, 9, tzinfo=UTC),
    )


@pytest.fixture
def job_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> int:
    """Two users, one of whom has a vacancy.

    Args:
        tmp_path: Private data root for this test.
        monkeypatch: Used to redirect the data directory.

    Returns:
        The id of the vacancy belonging to ``USER``.
    """
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.yaml")
    for user in (USER, OTHER):
        config.save_user_config(user, {})
    return Database(config.user_db_path(USER)).save_job(
        JobListing(
            title="Meetspecialist",
            company=COMPANY,
            url="https://voorbeeld.example/vacatures/1",
            source="board",
            status=JobStatus.MATCHED,
            seen_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )


def test_a_saved_set_reads_back_unchanged(job_id: int) -> None:
    """A saved set is the set: nothing is lost or reshaped on the way."""
    asked, answered = question_set(job_id), answer_set(job_id)

    save_interview_set(USER, asked)
    save_interview_set(USER, answered)

    nl = LetterLanguage.NL
    assert load_interview_set(USER, job_id, InterviewMode.ASK, nl) == asked
    assert load_interview_set(USER, job_id, InterviewMode.ANSWER, nl) == answered


def test_generating_again_replaces_only_that_set(job_id: int) -> None:
    """A new set replaces its own half and language, never the others."""
    save_interview_set(USER, question_set(job_id, "Eerste vraag?"))
    save_interview_set(
        USER, question_set(job_id, "First question?", language=LetterLanguage.EN)
    )
    save_interview_set(USER, answer_set(job_id))

    save_interview_set(USER, question_set(job_id, "Tweede vraag?", day=5))

    saved = load_saved_interview(USER, job_id)
    assert [s.questions[0].question for s in saved.questions] == [
        "Tweede vraag?",
        "First question?",
    ]
    assert saved.answers == [answer_set(job_id)]


def test_saved_sets_are_listed_newest_first(job_id: int) -> None:
    """The page shows the newest set when the language is left on Automatic."""
    save_interview_set(USER, question_set(job_id, "Oud?", day=1))
    save_interview_set(
        USER, question_set(job_id, "New?", language=LetterLanguage.EN, day=9)
    )

    saved = load_saved_interview(USER, job_id, InterviewMode.ASK)

    assert [s.language for s in saved.questions] == [
        LetterLanguage.EN,
        LetterLanguage.NL,
    ]
    assert saved.answers == []
    newest = latest_interview_set(USER, job_id, InterviewMode.ASK)
    dutch = latest_interview_set(USER, job_id, InterviewMode.ASK, LetterLanguage.NL)
    assert newest is not None and newest.questions[0].question == "New?"
    assert dutch is not None and dutch.questions[0].question == "Oud?"
    assert latest_interview_set(USER, job_id, InterviewMode.ANSWER) is None


def test_one_users_sets_never_reach_another(job_id: int) -> None:
    """Two applicants share a dashboard; their saved preparation must not mix."""
    save_interview_set(USER, question_set(job_id))

    theirs = load_saved_interview(OTHER, job_id)

    assert theirs.questions == [] and theirs.answers == []
    assert interview_set_path(USER, job_id, "ask", "nl").parent == (
        config.user_interview_dir(USER)
    )
    assert interview_set_path(OTHER, job_id, "ask", "nl").parent == (
        config.user_interview_dir(OTHER)
    )


@pytest.mark.parametrize(
    ("bad_job", "mode", "language"),
    [
        (0, "ask", "nl"),
        (-3, "ask", "nl"),
        (True, "ask", "nl"),
        (1, "../../escape", "nl"),
        (1, "ask/../../x", "nl"),
        (1, "ask", "../nl"),
        (1, "ask", "de"),
    ],
)
def test_nothing_in_a_request_can_leave_the_users_directory(
    job_id: int, bad_job: int, mode: str, language: str
) -> None:
    """The vacancy id and both enums are checked before any path is built."""
    with pytest.raises(InterviewStoreError):
        interview_set_path(USER, bad_job, mode, language)


@pytest.mark.parametrize("user", ["../Sam", "Sam/..", "all", "", "Nobody"])
def test_an_unknown_or_unsafe_user_gets_no_path(job_id: int, user: str) -> None:
    """A traversal attempt in the user name is refused like any unknown user."""
    with pytest.raises(LetterError):
        interview_set_path(user, job_id, InterviewMode.ASK, LetterLanguage.NL)


def test_the_file_name_is_built_from_checked_parts_only(job_id: int) -> None:
    """One file per vacancy, half and language, directly in the user's folder."""
    path = interview_set_path(USER, job_id, InterviewMode.ANSWER, LetterLanguage.EN)

    assert path.name == f"{job_id}-answer-en.json"
    assert path.parent == config.user_interview_dir(USER)


def test_a_write_leaves_no_temporary_file_behind(job_id: int) -> None:
    """The set is written beside its final name and moved into place."""
    path = save_interview_set(USER, question_set(job_id))
    save_interview_set(USER, question_set(job_id, "Nog een vraag?"))

    assert sorted(p.name for p in path.parent.iterdir()) == [path.name]


def test_an_unreadable_file_counts_as_nothing_saved(job_id: int) -> None:
    """A damaged file must not break the tab; generating again replaces it."""
    path = interview_set_path(USER, job_id, InterviewMode.ASK, LetterLanguage.NL)
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")

    assert load_saved_interview(USER, job_id).questions == []
    save_interview_set(USER, question_set(job_id))
    assert load_saved_interview(USER, job_id).questions == [question_set(job_id)]


def test_a_file_naming_another_set_is_ignored(job_id: int) -> None:
    """A set copied to the wrong name is not shown as this vacancy's set."""
    path = interview_set_path(USER, job_id, InterviewMode.ASK, LetterLanguage.NL)
    path.parent.mkdir(parents=True)
    path.write_text(question_set(job_id + 1).model_dump_json(), encoding="utf-8")

    assert load_saved_interview(USER, job_id).questions == []


def test_edited_answers_replace_the_drafts(job_id: int) -> None:
    """The applicant's rewrite is what is kept, with the original date."""
    save_interview_set(USER, answer_set(job_id))

    save_edited_answers(USER, answer_set(job_id, "In mijn eigen woorden."))

    saved = load_saved_interview(USER, job_id).answers
    assert saved[0].questions[0].draft_answer == "In mijn eigen woorden."
    assert saved[0].generated_at == answer_set(job_id).generated_at


def test_edited_answers_need_a_vacancy_that_still_exists(job_id: int) -> None:
    """An edit arrives from the page, so the vacancy is checked first."""
    with pytest.raises(InterviewStoreError, match="no longer exists"):
        save_edited_answers(USER, answer_set(job_id + 50))
