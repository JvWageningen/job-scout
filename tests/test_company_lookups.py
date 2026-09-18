"""The memory of company lookups: cooldowns, what blocks, and how dates read.

Every company and URL here is invented. Nothing here searches the web or calls
a model.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from job_scout.company_lookups import (
    FAILED_COOLDOWN,
    RESEARCH_COOLDOWN,
    REVIEW_COOLDOWN,
    LookupKind,
    LookupMemory,
    LookupOutcome,
    day,
    outcome_of,
    recall,
    remember,
    store_research,
)
from job_scout.database import Database
from job_scout.models import CompanyResearch, JobListing

COMPANY = "Ravelijn Zorggroep"
NOW = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)


@pytest.fixture
def db(tmp_path: Path) -> Database:
    """Return an empty database."""
    return Database(tmp_path / "lookups.db")


@pytest.mark.parametrize(
    ("kind", "outcome", "cooldown"),
    [
        (LookupKind.RESEARCH, LookupOutcome.NOTHING_FOUND, RESEARCH_COOLDOWN),
        (LookupKind.REVIEW, LookupOutcome.NOTHING_FOUND, REVIEW_COOLDOWN),
        (LookupKind.REVIEW, LookupOutcome.FOUND, REVIEW_COOLDOWN),
        (LookupKind.RESEARCH, LookupOutcome.FAILED, FAILED_COOLDOWN),
        (LookupKind.REVIEW, LookupOutcome.FAILED, FAILED_COOLDOWN),
    ],
)
def test_an_outcome_blocks_exactly_for_its_cooldown(
    kind: LookupKind, outcome: LookupOutcome, cooldown: timedelta
) -> None:
    """Inside the cooldown the lookup is ruled out; after it, it may run."""
    memory = LookupMemory(kind=kind, last={outcome: NOW})
    inside = memory.blocking(NOW + cooldown - timedelta(seconds=1), on_file=True)
    assert inside is not None
    assert inside.outcome is outcome
    assert memory.blocking(NOW + cooldown, on_file=True) is None


def test_the_cooldowns_are_ordered_as_documented() -> None:
    """A failure is retried soonest, research is left alone longest."""
    assert timedelta(hours=1) == FAILED_COOLDOWN
    assert timedelta(days=14) == REVIEW_COOLDOWN
    assert timedelta(days=30) == RESEARCH_COOLDOWN


def test_a_found_result_blocks_only_while_it_is_on_file() -> None:
    """A result that is gone has to be fetched again."""
    memory = LookupMemory(kind=LookupKind.RESEARCH, last={LookupOutcome.FOUND: NOW})
    later = NOW + timedelta(days=1)
    assert memory.blocking(later, on_file=True) is not None
    assert memory.blocking(later, on_file=False) is None


def test_the_most_recent_blocking_attempt_is_reported() -> None:
    """A failure after a check is what the reader needs to hear about."""
    memory = LookupMemory(
        kind=LookupKind.RESEARCH,
        last={
            LookupOutcome.NOTHING_FOUND: NOW - timedelta(days=5),
            LookupOutcome.FAILED: NOW - timedelta(minutes=10),
        },
    )
    block = memory.blocking(NOW, on_file=False)
    assert block is not None
    assert block.outcome is LookupOutcome.FAILED
    later = memory.blocking(NOW + timedelta(hours=2), on_file=False)
    assert later is not None
    assert later.outcome is LookupOutcome.NOTHING_FOUND


def test_checked_at_ignores_failures() -> None:
    """A failed lookup checked nothing, so it is not a date to show."""
    memory = LookupMemory(
        kind=LookupKind.REVIEW,
        last={
            LookupOutcome.FOUND: NOW - timedelta(days=3),
            LookupOutcome.FAILED: NOW,
        },
    )
    assert memory.checked_at == NOW - timedelta(days=3)
    assert LookupMemory(kind=LookupKind.REVIEW).checked_at is None


def test_lookups_are_remembered_per_company_and_kind(db: Database) -> None:
    """Differently written names of one company share one memory."""
    remember(db, "  ravelijn   ZORGGROEP ", LookupKind.RESEARCH, LookupOutcome.FAILED)
    remember(db, COMPANY, LookupKind.RESEARCH, LookupOutcome.NOTHING_FOUND, now=NOW)
    research = recall(db, COMPANY, LookupKind.RESEARCH)
    assert set(research.last) == {LookupOutcome.FAILED, LookupOutcome.NOTHING_FOUND}
    assert research.last[LookupOutcome.NOTHING_FOUND] == NOW
    assert recall(db, COMPANY, LookupKind.REVIEW).last == {}
    assert recall(db, "Kwadrant Meetlab", LookupKind.RESEARCH).last == {}


def test_a_later_attempt_replaces_the_time_of_the_same_outcome(db: Database) -> None:
    """The table keeps one row per outcome, holding its latest time."""
    remember(db, COMPANY, LookupKind.REVIEW, LookupOutcome.FOUND, now=NOW)
    later = NOW + timedelta(days=20)
    remember(db, COMPANY, LookupKind.REVIEW, LookupOutcome.FOUND, now=later)
    assert recall(db, COMPANY, LookupKind.REVIEW).last == {LookupOutcome.FOUND: later}


def test_a_stored_results_own_date_counts_as_a_found_lookup(db: Database) -> None:
    """Results written before lookups were remembered still hold off a lookup."""
    naive = datetime(2026, 9, 16, 12, 0)
    memory = recall(db, COMPANY, LookupKind.REVIEW, found_at=naive)
    assert memory.last == {LookupOutcome.FOUND: naive.replace(tzinfo=UTC)}
    remember(db, COMPANY, LookupKind.REVIEW, LookupOutcome.FOUND, now=NOW)
    older = recall(db, COMPANY, LookupKind.REVIEW, found_at=naive)
    assert older.last[LookupOutcome.FOUND] == NOW


def test_an_unknown_stored_outcome_is_ignored(db: Database) -> None:
    """A row a later version wrote does not break an older reader."""
    db.record_company_lookup(COMPANY, "research", "postponed", at=NOW)
    assert recall(db, COMPANY, LookupKind.RESEARCH).last == {}


def test_store_research_stores_what_was_found_and_remembers_either_way(
    db: Database,
) -> None:
    """Nothing is stored for an empty web, but the attempt is remembered."""
    job_id = db.save_job(
        JobListing(
            title="Kwaliteitsadviseur",
            company=COMPANY,
            url="https://vacatures.example/ravelijn",
            source="test",
        )
    )
    store_research(db, job_id, COMPANY, None, now=NOW)
    assert db.get_company_research(job_id) is None
    memory = recall(db, COMPANY, LookupKind.RESEARCH)
    assert memory.last == {LookupOutcome.NOTHING_FOUND: NOW}
    research = CompanyResearch(
        company_name=COMPANY, sources=["https://ravelijn.example/over-ons"]
    )
    store_research(db, job_id, COMPANY, research, now=NOW + timedelta(days=31))
    assert db.get_company_research(job_id) == research.model_dump_json()
    found = recall(db, COMPANY, LookupKind.RESEARCH).last[LookupOutcome.FOUND]
    assert found == NOW + timedelta(days=31)


def test_outcome_of_a_completed_lookup() -> None:
    """None is the web having nothing; anything else is a result."""
    assert outcome_of(None) is LookupOutcome.NOTHING_FOUND
    assert outcome_of(CompanyResearch(company_name=COMPANY)) is LookupOutcome.FOUND


def test_dates_read_in_words_whatever_the_locale() -> None:
    """missing_context says "checked 18 September 2026", not an ISO stamp."""
    assert day(NOW) == "18 September 2026"
    assert day(datetime(2026, 3, 1, 12, 0)) == "1 March 2026"
