"""Remember when a company was looked up, so a lookup runs once and is reused.

Company research and the company review each cost web searches and a model
call: about ten searches and a call together, and minutes when the local model
is asleep and a remote provider has to answer instead. Interview questions and
answers used to run them again on every generation whenever the result was
missing or thin, so an employer the web knows little about was searched for on
every click, and a review resting on two pages was rewritten every time.

Every lookup is now remembered per company and kind (research or review), with
when it happened and how it went: found, nothing found or failed. Within the
cooldown for that outcome the same lookup does not run again, whatever it
returned, and what is stored is used as it is. An explicit request (the
``company research`` command, ``POST /api/company/research``) always looks up
and is remembered like any other attempt.

Companies are matched on :func:`job_scout.database.company_key`, the same
normalised name the review cache uses, so every vacancy at one employer shares
one memory. A placeholder name such as "Unknown" names no employer, so nothing
is remembered under it (see :func:`job_scout.database.names_company`).

A search that returned no result for any query did not look at the company at
all: the search was down or blocked. The research and review modules raise for
it, and it is remembered as a failure, never as "nothing found".
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING

from loguru import logger
from pydantic import BaseModel, Field

from job_scout.database import names_company

if TYPE_CHECKING:
    from job_scout.database import Database
    from job_scout.models import CompanyResearch


class LookupKind(StrEnum):
    """What was looked up about a company."""

    RESEARCH = "research"
    REVIEW = "review"


class LookupOutcome(StrEnum):
    """How a lookup went."""

    FOUND = "found"
    NOTHING_FOUND = "nothing_found"
    FAILED = "failed"


# What a company does, what it sells and how big it is change over months, not
# days. Once research was found, or the web showed it holds nothing about the
# company, a month passes before the web is asked again.
RESEARCH_COOLDOWN = timedelta(days=30)
# Employee reviews appear faster than company facts change, and a thin review
# is the one result worth another look. Two weeks gives it time to gain sources
# without paying for ten searches and a model call on every click.
REVIEW_COOLDOWN = timedelta(days=14)
# A failure says nothing about the company, only that the model or the search
# could not be used just then. An hour stops a run of clicks from each waiting
# out the same timeout, and is short enough that the next session tries again.
FAILED_COOLDOWN = timedelta(hours=1)

_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


class LookupAttempt(BaseModel):
    """One remembered lookup: how it went and when."""

    outcome: LookupOutcome
    at: datetime


class LookupMemory(BaseModel):
    """What is remembered about looking one company up for one kind of result.

    Attributes:
        kind: Research or review.
        last: When each outcome last happened. One entry per outcome is enough
            to tell both when a lookup was last tried and when one last
            completed.
    """

    kind: LookupKind
    last: dict[LookupOutcome, datetime] = Field(default_factory=dict)

    def cooldown(self, outcome: LookupOutcome) -> timedelta:
        """Return how long an outcome stands before the lookup may run again.

        Args:
            outcome: How the remembered lookup went.

        Returns:
            :data:`FAILED_COOLDOWN` for a failure, otherwise the cooldown of
            this kind of lookup.
        """
        if outcome is LookupOutcome.FAILED:
            return FAILED_COOLDOWN
        if self.kind is LookupKind.RESEARCH:
            return RESEARCH_COOLDOWN
        return REVIEW_COOLDOWN

    def blocking(self, now: datetime, *, on_file: bool) -> LookupAttempt | None:
        """Return the remembered attempt that rules out looking up again now.

        A found result rules a lookup out only while it is still on file: the
        cooldown is there so a result is not fetched twice, and one that is no
        longer stored has to be fetched again.

        Args:
            now: The current time.
            on_file: Whether a result of this kind is stored for the company.

        Returns:
            The most recent attempt still inside its cooldown, or None when a
            lookup may run.
        """
        moment = aware(now)
        recent = [
            LookupAttempt(outcome=outcome, at=at)
            for outcome, at in self.last.items()
            if (on_file or outcome is not LookupOutcome.FOUND)
            and moment - aware(at) < self.cooldown(outcome)
        ]
        return max(recent, key=lambda attempt: attempt.at, default=None)

    @property
    def checked_at(self) -> datetime | None:
        """When a lookup last completed, found or not; a failure checked nothing."""
        done = [
            at
            for outcome, at in self.last.items()
            if outcome is not LookupOutcome.FAILED
        ]
        return max(done, default=None)


def aware(moment: datetime) -> datetime:
    """Return *moment* with a time zone, assuming UTC when it has none.

    Args:
        moment: A stored or computed time.

    Returns:
        The same moment, comparable with ``datetime.now(UTC)``.
    """
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def recall(
    db: Database,
    company: str,
    kind: LookupKind,
    *,
    found_at: datetime | None = None,
) -> LookupMemory:
    """Read what is remembered about looking a company up.

    Args:
        db: The user's database.
        company: Company name as the vacancy gives it.
        kind: Research or review.
        found_at: When the result on file was produced, such as a review's
            ``reviewed_at``. It counts as a found lookup at that time, which
            covers results stored before lookups were remembered and results
            that other paths (the daily run, the ``company-review`` command)
            store without remembering the lookup.

    Returns:
        The memory, empty when the company was never looked up or the name is
        a placeholder that names no employer.
    """
    last: dict[LookupOutcome, datetime] = {}
    stored = db.get_company_lookups(company, kind) if names_company(company) else {}
    for outcome, at in stored.items():
        try:
            last[LookupOutcome(outcome)] = at
        except ValueError:
            logger.debug(f"Ignoring unknown {kind} lookup outcome {outcome!r}")
    if found_at is not None:
        found = aware(found_at)
        last[LookupOutcome.FOUND] = max(last.get(LookupOutcome.FOUND, found), found)
    return LookupMemory(kind=kind, last=last)


def remember(
    db: Database,
    company: str,
    kind: LookupKind,
    outcome: LookupOutcome,
    *,
    now: datetime | None = None,
) -> None:
    """Remember that a company was looked up, and how that went.

    Nothing is remembered under a placeholder name such as "Unknown": two
    vacancies without a company name are two different employers.

    Args:
        db: The user's database.
        company: Company name as the vacancy gives it.
        kind: Research or review.
        outcome: Found, nothing found or failed.
        now: When the lookup ran; the current time when omitted.
    """
    if not names_company(company):
        logger.debug(f"Not remembering a {kind} lookup for placeholder {company!r}")
        return
    db.record_company_lookup(company, kind, outcome, at=now)
    logger.debug(f"Remembered {kind} lookup for {company!r}: {outcome}")


def outcome_of(result: object) -> LookupOutcome:
    """Name the outcome of a lookup that completed.

    Args:
        result: What the lookup returned; None when the web had nothing.

    Returns:
        FOUND for a result, NOTHING_FOUND for None.
    """
    return LookupOutcome.NOTHING_FOUND if result is None else LookupOutcome.FOUND


def store_research(
    db: Database,
    job_id: int,
    company: str,
    research: CompanyResearch | None,
    *,
    now: datetime | None = None,
) -> None:
    """Store what a research lookup found, and remember that it ran.

    Nothing is stored when nothing was found; the attempt is remembered either
    way, so the web is not asked again within the cooldown.

    Args:
        db: The user's database.
        job_id: The vacancy the research was run for, which is its storage key.
        company: Company name as the vacancy gives it.
        research: What the lookup returned; None when the web had nothing.
        now: When the lookup ran; the current time when omitted.
    """
    if research is not None:
        # model_dump_json, not json.dumps(model_dump()): the timestamp is a datetime.
        db.save_company_research(job_id, research.model_dump_json())
    remember(db, company, LookupKind.RESEARCH, outcome_of(research), now=now)


def day(moment: datetime) -> str:
    """Write a date the way ``missing_context`` shows it, e.g. "18 September 2026".

    Month names are spelled out here rather than taken from the locale, so the
    text does not change with the machine it runs on.

    Args:
        moment: The time to show, in any zone.

    Returns:
        The local calendar date in words.
    """
    local = aware(moment).astimezone()
    return f"{local.day} {_MONTHS[local.month - 1]} {local.year}"
