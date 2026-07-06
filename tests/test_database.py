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


def test_compensation_reasoning_persists(tmp_db: Database) -> None:
    """Compensation reasoning is saved and retrieved from the database."""
    job = JobListing(
        title="Data Scientist",
        company="TechCorp",
        url="https://example.com/jobs/science",
        source="indeed",
        status=JobStatus.MATCHED,
        fit_score=80,
        fit_reasoning="Good match for analytics background",
        salary_min=5000,
        salary_max=6500,
        salary_period="monthly",
        vacation_days=25,
        compensation_reasoning="Competitive salary with market rate",
        seen_at=datetime.now(UTC),
    )
    job_id = tmp_db.save_job(job)
    assert job_id > 0

    results = tmp_db.get_recent_matches(10)
    assert len(results) == 1
    assert results[0].compensation_reasoning == "Competitive salary with market rate"


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


def test_get_recent_matches_with_min_score_filter(tmp_db: Database) -> None:
    """get_recent_matches filters by min_score correctly."""
    jobs = [
        JobListing(
            title="High Score Job",
            company="Co",
            url="https://example.com/high",
            source="indeed",
            status=JobStatus.MATCHED,
            fit_score=85,
            fit_reasoning="Good",
            seen_at=datetime.now(UTC),
        ),
        JobListing(
            title="Low Score Job",
            company="Co",
            url="https://example.com/low",
            source="indeed",
            status=JobStatus.MATCHED,
            fit_score=30,
            fit_reasoning="Bad",
            seen_at=datetime.now(UTC),
        ),
        JobListing(
            title="Medium Score Job",
            company="Co",
            url="https://example.com/medium",
            source="indeed",
            status=JobStatus.MATCHED,
            fit_score=60,
            fit_reasoning="OK",
            seen_at=datetime.now(UTC),
        ),
    ]

    for job in jobs:
        tmp_db.save_job(job)

    # Get all matches
    all_matches = tmp_db.get_recent_matches(limit=10)
    assert len(all_matches) == 3

    # Filter by min_score = 70
    high_matches = tmp_db.get_recent_matches(limit=10, min_score=70)
    assert len(high_matches) == 1
    assert high_matches[0].fit_score == 85

    # Filter by min_score = 50
    above_50 = tmp_db.get_recent_matches(limit=10, min_score=50)
    assert len(above_50) == 2
    assert all(j.fit_score >= 50 for j in above_50)


def test_get_recent_matches_with_source_filter(tmp_db: Database) -> None:
    """get_recent_matches filters by source correctly."""
    jobs = [
        JobListing(
            title="Indeed Job",
            company="Co",
            url="https://example.com/indeed",
            source="indeed",
            status=JobStatus.MATCHED,
            fit_score=80,
            fit_reasoning="Good",
            seen_at=datetime.now(UTC),
        ),
        JobListing(
            title="LinkedIn Job",
            company="Co",
            url="https://example.com/linkedin",
            source="linkedin",
            status=JobStatus.MATCHED,
            fit_score=75,
            fit_reasoning="Good",
            seen_at=datetime.now(UTC),
        ),
        JobListing(
            title="Another Indeed Job",
            company="Co",
            url="https://example.com/indeed2",
            source="indeed",
            status=JobStatus.MATCHED,
            fit_score=70,
            fit_reasoning="OK",
            seen_at=datetime.now(UTC),
        ),
    ]

    for job in jobs:
        tmp_db.save_job(job)

    # Filter by source = indeed
    indeed_jobs = tmp_db.get_recent_matches(limit=10, source="indeed")
    assert len(indeed_jobs) == 2
    assert all(j.source == "indeed" for j in indeed_jobs)

    # Filter by source = linkedin
    linkedin_jobs = tmp_db.get_recent_matches(limit=10, source="linkedin")
    assert len(linkedin_jobs) == 1
    assert linkedin_jobs[0].source == "linkedin"


def test_get_recent_matches_sort_by_score_desc(tmp_db: Database) -> None:
    """get_recent_matches sorts by score descending."""
    jobs = [
        JobListing(
            title="Job 1",
            company="Co",
            url="https://example.com/1",
            source="indeed",
            status=JobStatus.MATCHED,
            fit_score=50,
            fit_reasoning="OK",
            seen_at=datetime.now(UTC),
        ),
        JobListing(
            title="Job 2",
            company="Co",
            url="https://example.com/2",
            source="indeed",
            status=JobStatus.MATCHED,
            fit_score=90,
            fit_reasoning="Great",
            seen_at=datetime.now(UTC),
        ),
        JobListing(
            title="Job 3",
            company="Co",
            url="https://example.com/3",
            source="indeed",
            status=JobStatus.MATCHED,
            fit_score=70,
            fit_reasoning="Good",
            seen_at=datetime.now(UTC),
        ),
    ]

    for job in jobs:
        tmp_db.save_job(job)

    results = tmp_db.get_recent_matches(limit=10, sort="score_desc")
    assert len(results) == 3
    assert results[0].fit_score == 90
    assert results[1].fit_score == 70
    assert results[2].fit_score == 50


def test_get_recent_matches_sort_by_score_asc(tmp_db: Database) -> None:
    """get_recent_matches sorts by score ascending."""
    jobs = [
        JobListing(
            title="Job 1",
            company="Co",
            url="https://example.com/1",
            source="indeed",
            status=JobStatus.MATCHED,
            fit_score=50,
            fit_reasoning="OK",
            seen_at=datetime.now(UTC),
        ),
        JobListing(
            title="Job 2",
            company="Co",
            url="https://example.com/2",
            source="indeed",
            status=JobStatus.MATCHED,
            fit_score=90,
            fit_reasoning="Great",
            seen_at=datetime.now(UTC),
        ),
        JobListing(
            title="Job 3",
            company="Co",
            url="https://example.com/3",
            source="indeed",
            status=JobStatus.MATCHED,
            fit_score=70,
            fit_reasoning="Good",
            seen_at=datetime.now(UTC),
        ),
    ]

    for job in jobs:
        tmp_db.save_job(job)

    results = tmp_db.get_recent_matches(limit=10, sort="score_asc")
    assert len(results) == 3
    assert results[0].fit_score == 50
    assert results[1].fit_score == 70
    assert results[2].fit_score == 90


def test_get_recent_matches_sort_by_date_desc(tmp_db: Database) -> None:
    """get_recent_matches sorts by date descending (default)."""
    from datetime import timedelta

    base_time = datetime.now(UTC)
    jobs = [
        JobListing(
            title="Job 1",
            company="Co",
            url="https://example.com/1",
            source="indeed",
            status=JobStatus.MATCHED,
            fit_score=75,
            fit_reasoning="Good",
            seen_at=base_time,
        ),
        JobListing(
            title="Job 2",
            company="Co",
            url="https://example.com/2",
            source="indeed",
            status=JobStatus.MATCHED,
            fit_score=75,
            fit_reasoning="Good",
            seen_at=base_time + timedelta(hours=1),
        ),
        JobListing(
            title="Job 3",
            company="Co",
            url="https://example.com/3",
            source="indeed",
            status=JobStatus.MATCHED,
            fit_score=75,
            fit_reasoning="Good",
            seen_at=base_time + timedelta(hours=2),
        ),
    ]

    for job in jobs:
        tmp_db.save_job(job)

    results = tmp_db.get_recent_matches(limit=10, sort="date_desc")
    assert len(results) == 3
    assert results[0].title == "Job 3"
    assert results[1].title == "Job 2"
    assert results[2].title == "Job 1"


def test_get_rejected_jobs_with_min_score_filter(tmp_db: Database) -> None:
    """get_rejected_jobs filters by min_score correctly."""
    jobs = [
        JobListing(
            title="High Score Rejected",
            company="Co",
            url="https://example.com/high",
            source="indeed",
            status=JobStatus.REJECTED,
            fit_score=45,
            fit_reasoning="Bad",
            seen_at=datetime.now(UTC),
        ),
        JobListing(
            title="Low Score Rejected",
            company="Co",
            url="https://example.com/low",
            source="indeed",
            status=JobStatus.REJECTED,
            fit_score=20,
            fit_reasoning="Very bad",
            seen_at=datetime.now(UTC),
        ),
    ]

    for job in jobs:
        tmp_db.save_job(job)

    # Get all rejected
    all_rejected = tmp_db.get_rejected_jobs(limit=10)
    assert len(all_rejected) == 2

    # Filter by min_score = 30
    above_30 = tmp_db.get_rejected_jobs(limit=10, min_score=30)
    assert len(above_30) == 1
    assert above_30[0].fit_score == 45


def test_get_rejected_jobs_with_source_filter(tmp_db: Database) -> None:
    """get_rejected_jobs filters by source correctly."""
    jobs = [
        JobListing(
            title="Indeed Rejected",
            company="Co",
            url="https://example.com/indeed",
            source="indeed",
            status=JobStatus.REJECTED,
            fit_score=25,
            fit_reasoning="Bad",
            seen_at=datetime.now(UTC),
        ),
        JobListing(
            title="LinkedIn Rejected",
            company="Co",
            url="https://example.com/linkedin",
            source="linkedin",
            status=JobStatus.REJECTED,
            fit_score=30,
            fit_reasoning="Bad",
            seen_at=datetime.now(UTC),
        ),
    ]

    for job in jobs:
        tmp_db.save_job(job)

    # Filter by source
    indeed_rejected = tmp_db.get_rejected_jobs(limit=10, source="indeed")
    assert len(indeed_rejected) == 1
    assert indeed_rejected[0].source == "indeed"


def test_sql_injection_safety_in_source_filter(tmp_db: Database) -> None:
    """Source filter using parameterized queries is safe from SQL injection."""
    job = JobListing(
        title="Test Job",
        company="Co",
        url="https://example.com/test",
        source="indeed",
        status=JobStatus.MATCHED,
        fit_score=75,
        fit_reasoning="Good",
        seen_at=datetime.now(UTC),
    )
    tmp_db.save_job(job)

    # Try to inject SQL via source parameter
    malicious_source = "' OR '1'='1"

    # This should not raise an error and should not return any jobs
    results = tmp_db.get_recent_matches(limit=10, source=malicious_source)
    assert len(results) == 0

    # Try another injection technique
    malicious_source2 = "'; DROP TABLE jobs; --"
    results2 = tmp_db.get_recent_matches(limit=10, source=malicious_source2)
    assert len(results2) == 0

    # Verify the job table still exists and has our job
    all_jobs = tmp_db.get_recent_matches(limit=10)
    assert len(all_jobs) == 1


def test_combined_filters_min_score_and_source(tmp_db: Database) -> None:
    """get_recent_matches correctly applies both min_score and source filters."""
    jobs = [
        JobListing(
            title="Indeed High",
            company="Co",
            url="https://example.com/1",
            source="indeed",
            status=JobStatus.MATCHED,
            fit_score=85,
            fit_reasoning="Good",
            seen_at=datetime.now(UTC),
        ),
        JobListing(
            title="Indeed Low",
            company="Co",
            url="https://example.com/2",
            source="indeed",
            status=JobStatus.MATCHED,
            fit_score=40,
            fit_reasoning="Bad",
            seen_at=datetime.now(UTC),
        ),
        JobListing(
            title="LinkedIn High",
            company="Co",
            url="https://example.com/3",
            source="linkedin",
            status=JobStatus.MATCHED,
            fit_score=80,
            fit_reasoning="Good",
            seen_at=datetime.now(UTC),
        ),
    ]

    for job in jobs:
        tmp_db.save_job(job)

    # Filter by source=indeed AND min_score=70
    results = tmp_db.get_recent_matches(limit=10, source="indeed", min_score=70)
    assert len(results) == 1
    assert results[0].title == "Indeed High"
    assert results[0].source == "indeed"
    assert results[0].fit_score >= 70


def test_save_and_get_run_stats(tmp_db: Database) -> None:
    """save_run_stats persists run statistics and get_run_history retrieves them."""
    from job_scout.models import RunStats

    now = datetime.now(UTC)
    stats = RunStats(
        scraped=100,
        deduplicated=10,
        title_filtered=20,
        title_screened=30,
        quick_filtered=15,
        evaluated=25,
        matched=5,
        rejected=20,
        notified=5,
        errors=["Error 1", "Error 2"],
    )

    # Save the run
    tmp_db.save_run_stats(stats, now, 45.5)

    # Retrieve the history
    history = tmp_db.get_run_history(limit=10)
    assert len(history) == 1

    entry = history[0]
    assert entry.scraped == 100
    assert entry.deduplicated == 10
    assert entry.title_filtered == 20
    assert entry.title_screened == 30
    assert entry.quick_filtered == 15
    assert entry.evaluated == 25
    assert entry.matched == 5
    assert entry.rejected == 20
    assert entry.notified == 5
    assert entry.errors == 2
    assert entry.duration_seconds == 45.5


def test_get_run_history_ordering(tmp_db: Database) -> None:
    """get_run_history returns runs in reverse chronological order (newest first)."""
    from job_scout.models import RunStats

    # Add multiple runs with different timestamps
    times = [
        datetime(2026, 1, 1, 10, 0, 0, tzinfo=UTC),
        datetime(2026, 1, 2, 10, 0, 0, tzinfo=UTC),
        datetime(2026, 1, 3, 10, 0, 0, tzinfo=UTC),
    ]

    for i, t in enumerate(times):
        stats = RunStats(
            scraped=100 + i,
            matched=5 + i,
            rejected=20 - i,
            notified=5,
            errors=[],
        )
        tmp_db.save_run_stats(stats, t, 30.0)

    # Retrieve history
    history = tmp_db.get_run_history(limit=10)
    assert len(history) == 3
    # Should be in reverse order (newest first)
    assert history[0].started_at == times[2]
    assert history[1].started_at == times[1]
    assert history[2].started_at == times[0]


def test_get_run_history_limit(tmp_db: Database) -> None:
    """get_run_history respects the limit parameter."""
    from job_scout.models import RunStats

    # Add 5 runs
    for i in range(5):
        stats = RunStats(
            scraped=100 + i,
            matched=5 + i,
            rejected=20 - i,
            notified=5,
            errors=[],
        )
        t = datetime(2026, 1, 1 + i, 10, 0, 0, tzinfo=UTC)
        tmp_db.save_run_stats(stats, t, 30.0)

    # Request only 2 most recent
    history = tmp_db.get_run_history(limit=2)
    assert len(history) == 2

    # Request 10 (more than available)
    history = tmp_db.get_run_history(limit=10)
    assert len(history) == 5


def test_run_stats_with_zero_errors(tmp_db: Database) -> None:
    """save_run_stats correctly stores zero error count when errors list is empty."""
    from job_scout.models import RunStats

    now = datetime.now(UTC)
    stats = RunStats(
        scraped=50,
        matched=10,
        rejected=15,
        notified=10,
        errors=[],
    )

    tmp_db.save_run_stats(stats, now, 20.0)

    history = tmp_db.get_run_history(limit=1)
    assert len(history) == 1
    assert history[0].errors == 0
