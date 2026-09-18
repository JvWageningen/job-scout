"""Saved interview preparation: one set per vacancy, half and language.

Generating interview material takes minutes, and longer when the company has to
be looked up first. A result that lived only in the page was gone on the next
reload and had to be paid for again. So every generated set is kept under the
user's own data directory, one JSON file per vacancy, half and language, written
atomically the same way a letter draft is. Generating again for the same three
replaces that file and leaves the other half and the other language alone.

The draft answers are the part the applicant rewrites, so an edited answer set
is saved over the generated one; it keeps the date it was generated.

Every path is built from a validated user, a positive vacancy id and two closed
enums, and is checked to sit directly inside the user's interview directory, so
nothing in a request can point a read or a write anywhere else.

Timestamps are kept in UTC with their zone. A set that arrives or was saved
without one (an edited file, a hand-made request) is taken as UTC, so saved
sets can always be put in order.
"""

from __future__ import annotations

import re
import tempfile
from collections.abc import Container
from datetime import UTC
from enum import StrEnum
from pathlib import Path

from loguru import logger
from pydantic import BaseModel, Field, ValidationError

from job_scout.config import user_db_path, user_interview_dir
from job_scout.database import Database
from job_scout.interview_answers import InterviewAnswerSet
from job_scout.interview_questions import InterviewQuestionSet
from job_scout.letters.models import LetterLanguage
from job_scout.letters.writer import require_user


class InterviewStoreError(ValueError):
    """A saved set was asked for with an unusable vacancy, half or language."""


class StaleInterviewError(RuntimeError):
    """An edit was made on a set older than the one saved since.

    Not a ValueError: the request is sound, the page is just behind, and the
    dashboard answers it with a conflict rather than a bad request.
    """


class InterviewMode(StrEnum):
    """Which half of the interview a set belongs to."""

    ASK = "ask"
    ANSWER = "answer"


InterviewSet = InterviewQuestionSet | InterviewAnswerSet

# The only names save_interview_set writes: vacancy, half and language.
_SAVED_NAME = re.compile(r"^([1-9][0-9]{0,17})-(?:ask|answer)-(?:nl|en)\.json$")


class SavedInterview(BaseModel):
    """Everything saved for one vacancy, newest set first within each half.

    Attributes:
        job_id: The vacancy.
        questions: Saved sets of questions to ask the employer, one per language.
        answers: Saved sets of likely questions with draft answers, one per
            language.
    """

    job_id: int = Field(gt=0)
    questions: list[InterviewQuestionSet] = Field(default_factory=list)
    answers: list[InterviewAnswerSet] = Field(default_factory=list)


def mode_of(interview: InterviewSet) -> InterviewMode:
    """Say which half a set belongs to.

    Args:
        interview: A question set or an answer set.

    Returns:
        ANSWER for likely questions with draft answers, ASK otherwise.
    """
    if isinstance(interview, InterviewAnswerSet):
        return InterviewMode.ANSWER
    return InterviewMode.ASK


# Every timestamp a set carries: when it was generated, and when the company
# research and review it used were written.
_MOMENTS = ("generated_at", "company_research_date", "company_review_date")


def _in_utc(interview: InterviewSet) -> InterviewSet:
    """Give a set's timestamps a zone, taking a bare one as UTC.

    Sets are sorted on when they were generated, and a timestamp with a zone
    cannot be compared with one without.

    Args:
        interview: A question set or an answer set.

    Returns:
        The same set, or a copy whose timestamps carry UTC.
    """
    update = {
        name: moment.replace(tzinfo=UTC)
        for name in _MOMENTS
        if (moment := getattr(interview, name)) is not None and moment.tzinfo is None
    }
    return interview.model_copy(update=update) if update else interview


def interview_set_path(
    user: str, job_id: int, mode: InterviewMode | str, language: LetterLanguage | str
) -> Path:
    """Return where one vacancy's set for one half and language is kept.

    Args:
        user: Name of an existing user.
        job_id: The vacancy.
        mode: The half, as an enum or its value.
        language: The language, as an enum or its value.

    Returns:
        The file path, directly inside the user's interview directory.

    Raises:
        LetterError: If the user does not exist or the name is unsafe.
        InterviewStoreError: If the vacancy id, half or language is invalid.
    """
    root = user_interview_dir(require_user(user))
    if isinstance(job_id, bool) or not isinstance(job_id, int) or job_id < 1:
        raise InterviewStoreError("Invalid vacancy ID.")
    try:
        half = InterviewMode(mode)
        chosen = LetterLanguage(language)
    except ValueError as exc:
        raise InterviewStoreError("Unknown interview half or language.") from exc
    path = root / f"{job_id}-{half.value}-{chosen.value}.json"
    if path.resolve().parent != root.resolve():
        raise InterviewStoreError("Invalid interview path.")
    return path


def save_interview_set(user: str, interview: InterviewSet) -> Path:
    """Save a set, replacing the one for the same vacancy, half and language.

    The file is written beside its final name and then moved into place, so a
    crash halfway leaves the previous version rather than half a file. A
    timestamp without a zone is saved as UTC.

    Args:
        user: Name of an existing user.
        interview: The set to keep.

    Returns:
        Where it was saved.
    """
    interview = _in_utc(interview)
    path = interview_set_path(
        user, interview.job_id, mode_of(interview), interview.language
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        dir=path.parent,
        delete=False,
        suffix=".tmp",
    ) as stream:
        temp = Path(stream.name)
        stream.write(interview.model_dump_json(indent=2))
    try:
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)
    logger.info(f"Saved interview {path.stem} for {user}")
    return path


def save_edited_answers(user: str, interview: InterviewAnswerSet) -> Path:
    """Keep the applicant's rewritten answers in place of the generated drafts.

    Unlike a freshly generated set, an edit arrives from the page, so the
    vacancy is checked to still exist before anything is written for it. A
    page left open on an older set must not undo a generation made since,
    on the command line or in another tab, so an edit to a set older than the
    saved one is refused. An edit to the same set, or to a newer one the
    server could not save when it was generated, goes ahead.

    Args:
        user: Name of an existing user.
        interview: The answer set as it is on screen.

    Returns:
        Where it was saved.

    Raises:
        InterviewStoreError: If the vacancy no longer exists.
        StaleInterviewError: If a newer set was generated after this one.
    """
    if Database(user_db_path(require_user(user))).get_job(interview.job_id) is None:
        raise InterviewStoreError("Vacancy no longer exists.")
    edited = _in_utc(interview)
    saved = load_interview_set(
        user, edited.job_id, InterviewMode.ANSWER, edited.language
    )
    if saved is not None and saved.generated_at > edited.generated_at:
        raise StaleInterviewError(
            "Newer answers were generated for this vacancy after this page "
            "opened them. Your edits are still on screen: download them, or "
            "choose the vacancy again to see the newer set."
        )
    return save_interview_set(user, edited)


def load_interview_set(
    user: str, job_id: int, mode: InterviewMode, language: LetterLanguage
) -> InterviewSet | None:
    """Read one saved set, if there is a usable one.

    A file that no longer parses (hand-edited, saved in another encoding, or
    written by a version with a different shape) is logged and treated as
    absent, so generating again replaces it instead of the tab failing on it
    forever. A timestamp without a zone is read as UTC.

    Args:
        user: Name of an existing user.
        job_id: The vacancy.
        mode: The half.
        language: The language.

    Returns:
        The saved set, or None when nothing usable is saved.
    """
    path = interview_set_path(user, job_id, mode, language)
    if not path.is_file():
        return None
    model = InterviewAnswerSet if mode is InterviewMode.ANSWER else InterviewQuestionSet
    try:
        loaded: InterviewSet = model.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (UnicodeDecodeError, ValidationError) as exc:
        logger.warning(f"Ignoring unreadable saved interview {path.name}: {exc}")
        return None
    if loaded.job_id != job_id or loaded.language is not language:
        logger.warning(f"Ignoring saved interview {path.name}: it names another set")
        return None
    return _in_utc(loaded)


def load_saved_interview(
    user: str, job_id: int, mode: InterviewMode | None = None
) -> SavedInterview:
    """Collect what is saved for a vacancy, newest first within each half.

    Args:
        user: Name of an existing user.
        job_id: The vacancy.
        mode: Only this half; None reads both.

    Returns:
        The saved sets; a half with nothing saved is an empty list.

    Raises:
        InterviewStoreError: If the vacancy id is not positive.
    """
    if job_id < 1:
        raise InterviewStoreError("Invalid vacancy ID.")
    saved = SavedInterview(job_id=job_id)
    for language in LetterLanguage:
        if mode in (None, InterviewMode.ASK):
            asked = load_interview_set(user, job_id, InterviewMode.ASK, language)
            if isinstance(asked, InterviewQuestionSet):
                saved.questions.append(asked)
        if mode in (None, InterviewMode.ANSWER):
            answered = load_interview_set(user, job_id, InterviewMode.ANSWER, language)
            if isinstance(answered, InterviewAnswerSet):
                saved.answers.append(answered)
    saved.questions.sort(key=lambda item: item.generated_at, reverse=True)
    saved.answers.sort(key=lambda item: item.generated_at, reverse=True)
    return saved


def saved_job_ids(user: str) -> list[int]:
    """List the vacancies anything is saved for, newest vacancy first.

    Only names this module writes count; a stray file in the folder is not a
    vacancy.

    Args:
        user: Name of an existing user.

    Returns:
        The vacancy ids, highest first.
    """
    root = user_interview_dir(require_user(user))
    if not root.is_dir():
        return []
    found = {
        int(match.group(1))
        for path in root.iterdir()
        if (match := _SAVED_NAME.match(path.name))
    }
    return sorted(found, reverse=True)


def saved_vacancy_choices(
    user: str, listed: Container[object]
) -> list[dict[str, object]]:
    """Offer the vacancies with saved sets that the shortlist no longer shows.

    An employer often takes a posting down once interviews start, and the
    pruner then marks the vacancy expired and closed. What was prepared for it
    is still needed, so it stays reachable. The entries have the same shape
    as the shortlist's and carry nothing private either.

    Args:
        user: Name of an existing user.
        listed: Vacancy ids the shortlist already offers.

    Returns:
        One entry per vacancy that has saved sets, still exists and is not
        in ``listed``, newest vacancy first.
    """
    db = Database(user_db_path(require_user(user)))
    choices: list[dict[str, object]] = []
    for job_id in saved_job_ids(user):
        job = None if job_id in listed else db.get_job(job_id)
        if job is None:
            continue
        choices.append(
            {
                "id": job.id,
                "title": job.title,
                "company": job.company,
                "status": job.status.value,
                "fit_score": job.fit_score,
            }
        )
    return choices


def latest_interview_set(
    user: str,
    job_id: int,
    mode: InterviewMode,
    language: LetterLanguage | None = None,
) -> InterviewSet | None:
    """Pick the set to show or export: this language, or the newest of any.

    Args:
        user: Name of an existing user.
        job_id: The vacancy.
        mode: The half.
        language: The language wanted; None takes the newest saved one.

    Returns:
        The chosen set, or None when nothing matching is saved.
    """
    saved = load_saved_interview(user, job_id, mode)
    sets: list[InterviewSet] = [*saved.questions, *saved.answers]
    if language is None:
        return sets[0] if sets else None
    return next((item for item in sets if item.language is language), None)
