"""Tests for SQLite database operations."""

from __future__ import annotations

from datetime import UTC, datetime

from job_scout.database import Database
from job_scout.models import JobListing, JobStatus, TravelMode, TravelTime


def test_save_and_retrieve_matched(tmp_db: Database, matched_job: JobListing) -> None:
    """Saved matched jobs appear in get_recent_matches."""
    job_id = tmp_db.save_job(matched_job)
    assert job_id > 0

    results = tmp_db.get_recent_matches(10)
    assert len(results) == 1
    assert results[0].title == matched_job.title
    assert results[0].fit_score == matched_job.fit_score


def test_duplicate_detection_by_url(tmp_db: Database, sample_job: JobListing) -> None:
    """is_duplicate returns True for a job with a matching URL."""
    tmp_db.save_job(sample_job)
    assert tmp_db.is_duplicate(sample_job) is True


def test_duplicate_detection_by_title_company(
    tmp_db: Database, sample_job: JobListing
) -> None:  # noqa: E501
    """is_duplicate matches on normalised title + company."""
    tmp_db.save_job(sample_job)
    clone = sample_job.model_copy(update={"url": "https://example.com/different-url"})
    assert tmp_db.is_duplicate(clone) is True


def test_no_duplicate_for_new_job(tmp_db: Database, sample_job: JobListing) -> None:
    """is_duplicate returns False for a job not in the database."""
    assert tmp_db.is_duplicate(sample_job) is False


def test_mark_notified(tmp_db: Database, matched_job: JobListing) -> None:
    """mark_notified clears the pending flag and sets notified=True."""
    jid = tmp_db.save_job(matched_job)
    tmp_db.mark_notification_pending(jid)

    pending = tmp_db.get_pending_notifications()
    assert len(pending) == 1

    tmp_db.mark_notified(jid)
    assert tmp_db.get_pending_notifications() == []


def test_rejected_jobs(tmp_db: Database) -> None:
    """Rejected jobs are retrievable via get_rejected_jobs."""
    job = JobListing(
        title="Junior Content Writer",
        company="Media Co",
        url="https://example.com/jobs/99",
        source="indeed",
        status=JobStatus.REJECTED,
        fit_score=30,
        fit_reasoning="Wrong domain",
        seen_at=datetime.now(UTC),
    )
    tmp_db.save_job(job)
    rejected = tmp_db.get_rejected_jobs(10)
    assert len(rejected) == 1
    assert rejected[0].status == JobStatus.REJECTED


def test_travel_times_persist(tmp_db: Database, sample_job: JobListing) -> None:
    """TravelTime objects survive a database round-trip."""
    sample_job.travel_times = [
        TravelTime(mode=TravelMode.CAR, minutes=25.0),
        TravelTime(mode=TravelMode.PUBLIC_TRANSPORT, minutes=50.0),
    ]
    sample_job.status = JobStatus.MATCHED
    tmp_db.save_job(sample_job)

    matches = tmp_db.get_recent_matches(5)
    assert len(matches) == 1
    assert len(matches[0].travel_times) == 2
    car_times = [t for t in matches[0].travel_times if t.mode == TravelMode.CAR]
    assert car_times[0].minutes == 25.0


def test_log_stats(
    tmp_db: Database, matched_job: JobListing, sample_job: JobListing
) -> None:  # noqa: E501
    """log_stats returns a dict of status counts."""
    tmp_db.save_job(matched_job)
    sample_job.status = JobStatus.REJECTED
    tmp_db.save_job(sample_job)

    stats = tmp_db.log_stats()
    assert stats.get("matched", 0) == 1
    assert stats.get("rejected", 0) == 1


def test_get_recent_matches_respects_limit(tmp_db: Database) -> None:
    """get_recent_matches returns at most `limit` records."""
    for i in range(5):
        job = JobListing(
            title=f"Job {i}",
            company="Co",
            url=f"https://example.com/job/{i}",
            source="indeed",
            status=JobStatus.MATCHED,
            fit_score=70,
            fit_reasoning="Good",
            seen_at=datetime.now(UTC),
        )
        tmp_db.save_job(job)

    results = tmp_db.get_recent_matches(3)
    assert len(results) == 3


def test_get_rejected_jobs_respects_limit(tmp_db: Database) -> None:
    """get_rejected_jobs returns at most `limit` records."""
    for i in range(5):
        job = JobListing(
            title=f"Rejected {i}",
            company="Co",
            url=f"https://example.com/rejected/{i}",
            source="indeed",
            status=JobStatus.REJECTED,
            fit_score=20,
            fit_reasoning="Bad fit",
            seen_at=datetime.now(UTC),
        )
        tmp_db.save_job(job)

    results = tmp_db.get_rejected_jobs(2)
    assert len(results) == 2


def test_negative_match_persists(tmp_db: Database) -> None:
    """Negative match flag and reasoning survive a database round-trip."""
    job = JobListing(
        title="Marketing Manager",
        company="Brand Co",
        url="https://example.com/job/neg",
        source="indeed",
        status=JobStatus.REJECTED,
        negative_match=True,
        negative_reasoning="Social media role — out of scope",
        seen_at=datetime.now(UTC),
    )
    tmp_db.save_job(job)
    rejected = tmp_db.get_rejected_jobs(5)
    assert len(rejected) == 1
    assert rejected[0].negative_match is True
    assert "out of scope" in (rejected[0].negative_reasoning or "")


def test_save_job_insert_or_ignore_on_duplicate_url(
    tmp_db: Database, sample_job: JobListing
) -> None:
    """Saving a job with a duplicate URL returns 0 (INSERT OR IGNORE)."""
    first_id = tmp_db.save_job(sample_job)
    assert first_id > 0
    second_id = tmp_db.save_job(sample_job)
    assert second_id == 0


def test_multiple_pending_notifications(tmp_db: Database) -> None:
    """get_pending_notifications returns all jobs awaiting retry."""
    for i in range(3):
        job = JobListing(
            title=f"Pending {i}",
            company="Co",
            url=f"https://example.com/pending/{i}",
            source="indeed",
            status=JobStatus.MATCHED,
            fit_score=75,
            fit_reasoning="Good",
            seen_at=datetime.now(UTC),
        )
        jid = tmp_db.save_job(job)
        tmp_db.mark_notification_pending(jid)

    pending = tmp_db.get_pending_notifications()
    assert len(pending) == 3
