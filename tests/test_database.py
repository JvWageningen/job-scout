"""Tests for SQLite database operations."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from job_scout.database import Database, company_share_key, names_company
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


def test_geocode_cache_save_and_retrieve(tmp_db: Database) -> None:
    """Geocode cache stores and retrieves coordinates by normalized address."""
    address = "Amsterdam, Netherlands"
    lat, lon = 52.3676, 4.9041

    tmp_db.save_geocode_cache(address, lat, lon)

    # Retrieve with same address
    result = tmp_db.get_cached_geocode(address, cache_days=90)
    assert result is not None
    assert result[0] == lon  # Returns (lon, lat)
    assert result[1] == lat


def test_geocode_cache_normalization(tmp_db: Database) -> None:
    """Geocode cache key normalizes addresses (whitespace, case)."""
    address1 = "Amsterdam, Netherlands"
    address2 = "AMSTERDAM,   NETHERLANDS"
    lat, lon = 52.3676, 4.9041

    tmp_db.save_geocode_cache(address1, lat, lon)

    # Should retrieve with differently-formatted address
    result = tmp_db.get_cached_geocode(address2, cache_days=90)
    assert result is not None
    assert result == (lon, lat)


def test_geocode_cache_ttl_expired(tmp_db: Database) -> None:
    """Expired geocode cache entries are not returned."""
    address = "Berlin, Germany"
    lat, lon = 52.5200, 13.4050

    # Save the cache
    tmp_db.save_geocode_cache(address, lat, lon)

    # Should be retrievable with long TTL
    result = tmp_db.get_cached_geocode(address, cache_days=90)
    assert result is not None

    # Should NOT be retrievable with 0-day TTL (i.e., immediately expired)
    result = tmp_db.get_cached_geocode(address, cache_days=0)
    assert result is None


def test_geocode_cache_miss(tmp_db: Database) -> None:
    """Geocode cache miss returns None."""
    result = tmp_db.get_cached_geocode("NonexistentCity XYZ", cache_days=90)
    assert result is None


def test_travel_time_cache_save_and_retrieve(tmp_db: Database) -> None:
    """Travel time cache stores and retrieves minutes by route key."""
    origin = "52.3676,4.9041"
    dest = "52.1326,5.2913"
    mode = "car"
    minutes = 45.5

    tmp_db.save_travel_time_cache(origin, dest, mode, minutes)

    result = tmp_db.get_cached_travel_time(origin, dest, mode, cache_days=14)
    assert result == minutes


def test_travel_time_cache_multiple_modes(tmp_db: Database) -> None:
    """Travel time cache distinguishes between different transport modes."""
    origin = "52.3676,4.9041"
    dest = "52.1326,5.2913"

    tmp_db.save_travel_time_cache(origin, dest, "car", 45.0)
    tmp_db.save_travel_time_cache(origin, dest, "bike", 120.0)
    tmp_db.save_travel_time_cache(origin, dest, "ns_public_transport", 60.0)

    car_result = tmp_db.get_cached_travel_time(origin, dest, "car", 14)
    bike_result = tmp_db.get_cached_travel_time(origin, dest, "bike", 14)
    pt_result = tmp_db.get_cached_travel_time(origin, dest, "ns_public_transport", 14)

    assert car_result == 45.0
    assert bike_result == 120.0
    assert pt_result == 60.0


def test_travel_time_cache_ttl_expired(tmp_db: Database) -> None:
    """Expired travel time cache entries are not returned."""
    origin = "52.3676,4.9041"
    dest = "52.1326,5.2913"
    mode = "car"

    tmp_db.save_travel_time_cache(origin, dest, mode, 45.0)

    # Should be retrievable with long TTL
    result = tmp_db.get_cached_travel_time(origin, dest, mode, cache_days=14)
    assert result is not None

    # Should NOT be retrievable with 0-day TTL
    result = tmp_db.get_cached_travel_time(origin, dest, mode, cache_days=0)
    assert result is None


def test_travel_time_cache_miss(tmp_db: Database) -> None:
    """Travel time cache miss returns None."""
    result = tmp_db.get_cached_travel_time("1.0,1.0", "2.0,2.0", "car", 14)
    assert result is None


def test_travel_time_cache_replace_updates(tmp_db: Database) -> None:
    """Updating an existing travel time cache entry updates the timestamp."""
    origin = "52.3676,4.9041"
    dest = "52.1326,5.2913"
    mode = "car"

    tmp_db.save_travel_time_cache(origin, dest, mode, 45.0)
    result1 = tmp_db.get_cached_travel_time(origin, dest, mode, 14)
    assert result1 == 45.0

    # Update the same cache entry
    tmp_db.save_travel_time_cache(origin, dest, mode, 50.0)
    result2 = tmp_db.get_cached_travel_time(origin, dest, mode, 14)
    assert result2 == 50.0


def test_approve_job(tmp_db: Database, sample_job: JobListing) -> None:
    """Approving a job sets approval fields correctly."""
    job_id = tmp_db.save_job(sample_job)
    assert job_id

    tmp_db.approve_job(job_id, "test_user", "Looks good")

    approved_jobs = [j for j in tmp_db.get_jobs_by_status(JobStatus.APPROVED)]
    assert len(approved_jobs) == 1
    assert approved_jobs[0].approved_by == "test_user"
    assert approved_jobs[0].approval_notes == "Looks good"


def test_update_job_status_valid_transition(
    tmp_db: Database, sample_job: JobListing
) -> None:
    """Updating job status with valid transition succeeds."""
    job_id = tmp_db.save_job(sample_job)
    assert job_id

    # NEW -> VIEWED is valid
    success = tmp_db.update_job_status(job_id, JobStatus.VIEWED)
    assert success

    jobs = tmp_db.get_jobs_by_status(JobStatus.VIEWED)
    assert len(jobs) == 1
    assert jobs[0].status == JobStatus.VIEWED


def test_update_job_status_invalid_transition(
    tmp_db: Database, sample_job: JobListing
) -> None:
    """Updating job status with invalid transition fails."""
    job_id = tmp_db.save_job(sample_job)
    assert job_id

    # NEW -> SUBMITTED is invalid (must go through intermediate states)
    success = tmp_db.update_job_status(job_id, JobStatus.SUBMITTED)
    assert not success

    # Status should not have changed
    jobs = tmp_db.get_jobs_by_status(JobStatus.NEW)
    assert len(jobs) == 1


def test_get_jobs_by_status(tmp_db: Database, sample_job: JobListing) -> None:
    """Getting jobs by status returns correct results."""
    job1_id = tmp_db.save_job(sample_job)

    job2 = sample_job.model_copy(update={"url": "https://example.com/job2"})
    job2_id = tmp_db.save_job(job2)

    # Both should be NEW
    new_jobs = tmp_db.get_jobs_by_status(JobStatus.NEW)
    assert len(new_jobs) == 2

    # Transition first job to VIEWED
    tmp_db.update_job_status(job1_id, JobStatus.VIEWED)

    new_jobs = tmp_db.get_jobs_by_status(JobStatus.NEW)
    assert len(new_jobs) == 1
    assert new_jobs[0].id == job2_id

    viewed_jobs = tmp_db.get_jobs_by_status(JobStatus.VIEWED)
    assert len(viewed_jobs) == 1
    assert viewed_jobs[0].id == job1_id


def test_get_approval_queue(tmp_db: Database, sample_job: JobListing) -> None:
    """Approval queue returns jobs in NEW and VIEWED status."""
    job1_id = tmp_db.save_job(sample_job)

    job2 = sample_job.model_copy(update={"url": "https://example.com/job2"})
    job2_id = tmp_db.save_job(job2)

    job3 = sample_job.model_copy(update={"url": "https://example.com/job3"})
    job3_id = tmp_db.save_job(job3)

    # Transition job2 to VIEWED
    tmp_db.update_job_status(job2_id, JobStatus.VIEWED)

    # Transition job3 to APPROVED (should not appear in queue)
    tmp_db.update_job_status(job3_id, JobStatus.VIEWED)
    tmp_db.approve_job(job3_id, "test_user")

    queue = tmp_db.get_approval_queue()
    queue_ids = [j.id for j in queue]

    # Queue should only have job1 (NEW) and job2 (VIEWED)
    assert job1_id in queue_ids
    assert job2_id in queue_ids
    assert job3_id not in queue_ids


def test_upsert_preserves_user_set_lifecycle_status(
    tmp_db: Database, sample_job: JobListing
) -> None:
    """Re-scrape with update_existing=True preserves user-set lifecycle status.

    This is the CRITICAL test: when a user manually advances a job's status
    (e.g., from MATCHED to VIEWED or APPROVED), a re-scrape that would
    normally reset it back to MATCHED must NOT do so.
    """
    # Save job initially as MATCHED (from evaluation)
    job = sample_job.model_copy(update={"status": JobStatus.MATCHED})
    job_id = tmp_db.save_job(job)
    assert job_id > 0

    # User manually advances it to APPROVED
    tmp_db.update_job_status(job_id, JobStatus.VIEWED)
    tmp_db.update_job_status(job_id, JobStatus.APPROVED)

    # Verify it's APPROVED
    approved = tmp_db.get_jobs_by_status(JobStatus.APPROVED)
    assert len(approved) == 1
    assert approved[0].id == job_id

    # Re-scrape with update_existing=True and new status MATCHED
    # This simulates what happens with --full-rerun
    rescraped = sample_job.model_copy(
        update={
            "status": JobStatus.MATCHED,
            "fit_score": 95,  # Different evaluation
        }
    )
    new_id = tmp_db.save_job(rescraped, update_existing=True)
    assert new_id == job_id  # Same row, not a duplicate

    # CRITICAL: Status should still be APPROVED, not reverted to MATCHED
    approved_after = tmp_db.get_jobs_by_status(JobStatus.APPROVED)
    assert len(approved_after) == 1
    assert approved_after[0].id == job_id
    assert approved_after[0].status == JobStatus.APPROVED

    # But the evaluation (fit_score) should be updated
    assert approved_after[0].fit_score == 95


def test_upsert_refreshes_location_unknown(
    tmp_db: Database, sample_job: JobListing
) -> None:
    """Re-checking a job's travel data must be able to clear location_unknown.

    A rechecked job that now resolves cleanly must not stay flagged unknown
    forever -- otherwise the travel filter keeps waving it through no matter
    how far away it really is.
    """
    job = sample_job.model_copy(
        update={"status": JobStatus.MATCHED, "location_unknown": True}
    )
    job_id = tmp_db.save_job(job)

    rechecked = sample_job.model_copy(
        update={
            "status": JobStatus.MATCHED,
            "location_unknown": False,
            "distance_km": 42.0,
        }
    )
    tmp_db.save_job(rechecked, update_existing=True)

    saved = tmp_db.get_job(job_id)
    assert saved is not None
    assert saved.location_unknown is False
    assert saved.distance_km == 42.0


def test_batch_upsert_refreshes_location_unknown(
    tmp_db: Database, sample_job: JobListing
) -> None:
    """save_jobs_batch (the path a real pipeline run uses) has the same fix."""
    job = sample_job.model_copy(
        update={"status": JobStatus.MATCHED, "location_unknown": True}
    )
    job_id = tmp_db.save_jobs_batch([job])[0]

    rechecked = sample_job.model_copy(
        update={"status": JobStatus.MATCHED, "location_unknown": False}
    )
    tmp_db.save_jobs_batch([rechecked], update_existing=True)

    saved = tmp_db.get_job(job_id)
    assert saved is not None
    assert saved.location_unknown is False


def test_save_and_get_person_search(tmp_db: Database) -> None:
    """A saved person-search result round-trips through the cache."""
    tmp_db.save_person_search("Jane Doe", '{"full_name": "Jane Doe"}')
    assert tmp_db.get_person_search("Jane Doe") == '{"full_name": "Jane Doe"}'


def test_get_person_search_normalises_key(tmp_db: Database) -> None:
    """Lookup is case- and whitespace-insensitive, like company reviews."""
    tmp_db.save_person_search("Jane   Doe", '{"full_name": "Jane Doe"}')
    assert tmp_db.get_person_search("jane doe") is not None
    assert tmp_db.get_person_search("JANE DOE") is not None


def test_get_person_search_missing_returns_none(tmp_db: Database) -> None:
    """An unknown name returns None rather than raising."""
    assert tmp_db.get_person_search("Nobody Here") is None


def test_save_person_search_overwrites_existing(tmp_db: Database) -> None:
    """Re-saving for the same name replaces the cached value."""
    tmp_db.save_person_search("Jane Doe", '{"confidence": "low"}')
    tmp_db.save_person_search("Jane Doe", '{"confidence": "high"}')
    assert tmp_db.get_person_search("Jane Doe") == '{"confidence": "high"}'


def test_get_person_search_respects_max_age(tmp_db: Database) -> None:
    """A result older than max_age_days is treated as absent."""
    from datetime import timedelta

    tmp_db.save_person_search("Jane Doe", '{"confidence": "low"}')
    stale = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    with tmp_db._conn() as conn:
        conn.execute(
            "UPDATE person_search_cache SET searched_at = ? WHERE person_key = ?",
            (stale, "jane doe"),
        )
    assert tmp_db.get_person_search("Jane Doe", max_age_days=14) is None
    assert tmp_db.get_person_search("Jane Doe", max_age_days=60) is not None


class TestCommuteFilteredStat:
    """Tests for recording how many jobs the commute filter dropped."""

    def test_the_count_survives_a_round_trip(self, tmp_path) -> None:  # noqa: ANN001
        """A stage that drops half the candidates should be visible in history."""
        from datetime import datetime

        from job_scout.models import RunStats

        db = Database(tmp_path / "jobs.db")
        db.save_run_stats(
            RunStats(scraped=468, title_screened=325, commute_filtered=68, matched=3),
            datetime(2026, 9, 15, 9, 33),
            410.0,
        )

        entry = db.get_run_history(limit=1)[0]
        assert entry.commute_filtered == 68
        assert entry.title_screened == 325
        assert entry.matched == 3

    def test_a_database_from_before_the_column_still_opens(self, tmp_path) -> None:  # noqa: ANN001
        """Existing installs must not need their history wiped."""
        import sqlite3

        path = tmp_path / "old.db"
        conn = sqlite3.connect(path)
        conn.execute(
            """
            CREATE TABLE runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                duration_seconds REAL NOT NULL,
                scraped INTEGER DEFAULT 0,
                deduplicated INTEGER DEFAULT 0,
                title_filtered INTEGER DEFAULT 0,
                title_screened INTEGER DEFAULT 0,
                quick_filtered INTEGER DEFAULT 0,
                evaluated INTEGER DEFAULT 0,
                matched INTEGER DEFAULT 0,
                rejected INTEGER DEFAULT 0,
                notified INTEGER DEFAULT 0,
                errors INTEGER DEFAULT 0
            )
            """
        )
        conn.execute(
            "INSERT INTO runs (started_at, duration_seconds, scraped, matched) "
            "VALUES ('2026-09-08T17:00:00', 905.0, 320, 3)"
        )
        conn.commit()
        conn.close()

        db = Database(path)
        entry = db.get_run_history(limit=1)[0]

        # The old row has no commute stage to report, and says so rather than
        # failing to load.
        assert entry.commute_filtered == 0
        assert entry.scraped == 320


class TestCompanyResearchByCompany:
    """Research is stored per vacancy and read per company."""

    @staticmethod
    def _job(db: Database, slug: str, company: str) -> int:
        """Save one vacancy and return its id."""
        return db.save_job(
            JobListing(
                title="Kwaliteitsadviseur",
                company=company,
                url=f"https://vacatures.example/{slug}",
                source="test",
            )
        )

    def test_any_vacancy_at_the_company_finds_its_research(self, tmp_path) -> None:  # noqa: ANN001
        """A differently written name of the same company finds the same rows."""
        db = Database(tmp_path / "jobs.db")
        first = self._job(db, "a", "Ravelijn Zorggroep")
        second = self._job(db, "b", "  ravelijn   ZORGGROEP")
        other = self._job(db, "c", "Kwadrant Meetlab")
        db.save_company_research(first, '{"company_name": "old"}')
        db.save_company_research(second, '{"company_name": "new"}')
        db.save_company_research(other, '{"company_name": "other"}')

        rows = db.get_company_research_for_company("Ravelijn Zorggroep")

        assert rows == ['{"company_name": "new"}', '{"company_name": "old"}']
        assert db.get_company_research(first) == '{"company_name": "old"}'

    def test_a_vacancys_own_row_counts_whatever_name_it_was_stored_under(
        self,
        tmp_path,  # noqa: ANN001
    ) -> None:
        """Research saved for a vacancy that no longer exists is still its own."""
        db = Database(tmp_path / "jobs.db")
        db.save_company_research(99, '{"company_name": "orphan"}')
        assert db.get_company_research_for_company("Ravelijn Zorggroep") == []
        assert db.get_company_research_for_company("Ravelijn Zorggroep", job_id=99) == [
            '{"company_name": "orphan"}'
        ]

    def test_a_placeholder_name_shares_nothing(self, tmp_path) -> None:  # noqa: ANN001
        """Two "Unknown" vacancies are two employers; each keeps its own research."""
        db = Database(tmp_path / "jobs.db")
        first = self._job(db, "a", "Unknown")
        second = self._job(db, "b", "unknown")
        db.save_company_research(first, '{"company_name": "first"}')
        db.save_company_research(second, '{"company_name": "second"}')
        # The orphan fallback name save_company_research writes is one too.
        db.save_company_research(99, '{"company_name": "orphan"}')

        assert db.get_company_research_for_company("Unknown") == []
        assert db.get_company_research_for_company("Unknown", job_id=second) == [
            '{"company_name": "second"}'
        ]

    @pytest.mark.parametrize(
        ("company", "named"),
        [
            ("Ravelijn Zorggroep", True),
            ("Unknown Industries", True),
            ("Unknown", False),
            (" UNKNOWN ", False),
            ("nan", False),
            ("   ", False),
        ],
    )
    def test_names_company(self, company: str, named: bool) -> None:
        """Only a blank name or a scraper placeholder names no employer."""
        assert names_company(company) is named


class TestCompanyLookupMigration:
    """Databases on the NAS upgrade in place and keep what they hold."""

    def test_a_database_from_before_company_keys_keeps_its_research_and_reviews(
        self,
        tmp_path,  # noqa: ANN001
    ) -> None:
        """Old research gains its company key; nothing stored is lost."""
        import sqlite3

        path = tmp_path / "old.db"
        job_id = Database(path).save_job(
            JobListing(
                title="Kwaliteitsadviseur",
                company="Ravelijn  Zorggroep",
                url="https://vacatures.example/ravelijn",
                source="test",
            )
        )
        research = '{"company_name": "Ravelijn Zorggroep", "sources": ["u"]}'
        review = '{"company": "Ravelijn Zorggroep", "summary": "Prima."}'
        conn = sqlite3.connect(path)
        conn.executescript(
            """
            DROP TABLE company_research;
            DROP TABLE company_lookups;
            CREATE TABLE company_research (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id INTEGER NOT NULL,
                company_name TEXT NOT NULL,
                research_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (job_id) REFERENCES jobs(id)
            );
            """
        )
        conn.execute(
            "INSERT INTO company_research (job_id, company_name, research_json, "
            "created_at, updated_at) VALUES (?, 'Unknown', ?, ?, ?)",
            (job_id, research, "2026-09-01T10:00:00+00:00", "2026-09-01T10:00:00"),
        )
        conn.execute(
            "INSERT OR REPLACE INTO company_reviews VALUES (?, ?, ?)",
            ("ravelijn zorggroep", review, datetime.now(UTC).isoformat()),
        )
        conn.commit()
        conn.close()

        db = Database(path)
        Database(path)  # opening twice must not fail or duplicate anything

        assert db.get_company_research(job_id) == research
        assert db.get_company_research_for_company("ravelijn zorggroep") == [research]
        assert db.get_company_review("Ravelijn Zorggroep", max_age_days=365) == review
        db.record_company_lookup("Ravelijn Zorggroep", "research", "found")
        assert set(db.get_company_lookups("Ravelijn Zorggroep", "research")) == {
            "found"
        }

    def test_rows_stored_per_spelling_are_merged_once(
        self,
        tmp_path,  # noqa: ANN001
    ) -> None:
        """Keys written before legal forms were dropped meet on one key.

        The latest time of each lookup outcome and the newest review win, so
        nothing learnt is lost and no key is written twice.
        """
        import sqlite3

        path = tmp_path / "old.db"
        Database(path)
        conn = sqlite3.connect(path)
        conn.execute("DELETE FROM meta WHERE key = 'company_key_version'")
        conn.executemany(
            "INSERT INTO company_lookups VALUES (?, ?, ?, ?)",
            [
                ("kwadrant meetlab", "research", "failed", "2026-09-10T10:00:00+00:00"),
                ("kwadrant meetlab b.v.", "research", "failed", "2026-09-12T10:00:00"),
                (
                    "kwadrant meetlab bv",
                    "research",
                    "found",
                    "2026-09-01T10:00:00+00:00",
                ),
                (
                    "kwadrant meetlab b.v.",
                    "review",
                    "nothing_found",
                    "2026-09-05T10:00",
                ),
            ],
        )
        conn.executemany(
            "INSERT INTO company_reviews VALUES (?, ?, ?)",
            [
                ("kwadrant meetlab", "old", "2026-08-01T10:00:00+00:00"),
                ("kwadrant meetlab b.v.", "new", "2026-09-01T10:00:00+00:00"),
            ],
        )
        conn.commit()
        conn.close()

        db = Database(path)
        Database(path)  # the merge runs once

        assert db.get_company_lookups("Kwadrant Meetlab", "research") == {
            "failed": datetime(2026, 9, 12, 10, tzinfo=UTC),
            "found": datetime(2026, 9, 1, 10, tzinfo=UTC),
        }
        assert set(db.get_company_lookups("Kwadrant Meetlab BV", "review")) == {
            "nothing_found"
        }
        assert db.get_company_review("Kwadrant Meetlab", max_age_days=10**6) == "new"
        conn = sqlite3.connect(path)
        stored = conn.execute("SELECT company_key FROM company_reviews").fetchall()
        conn.close()
        assert stored == [("kwadrant meetlab",)]


class TestCompanyShareKey:
    """Research, reviews and lookups are shared by every spelling of an employer."""

    @pytest.mark.parametrize(
        ("company", "key"),
        [
            ("Kwadrant Meetlab", "kwadrant meetlab"),
            ("Kwadrant Meetlab B.V.", "kwadrant meetlab"),
            ("Kwadrant  Meetlab BV", "kwadrant meetlab"),
            ("Kwadrant Meetlab, b. v.", "kwadrant meetlab"),
            ("Ravelijn N.V.", "ravelijn"),
            ("Findwhere GmbH", "findwhere"),
            ("Findwhere Ltd.", "findwhere"),
            ("Voorbeeld V.O.F.", "voorbeeld"),
            ("Ravelijn Holding B.V.", "ravelijn holding"),
            ("B.V.", "b.v."),
            ("Unknown", "unknown"),
        ],
    )
    def test_a_trailing_legal_form_is_dropped(self, company: str, key: str) -> None:
        """Group words stay: a holding can be another company of the group."""
        assert company_share_key(company) == key

    @pytest.mark.parametrize(
        "spelling", ["Kwadrant Meetlab B.V.", "Kwadrant Meetlab BV", "KWADRANT MEETLAB"]
    )
    def test_every_spelling_of_one_employer_finds_the_same_rows(
        self,
        tmp_path,  # noqa: ANN001
        spelling: str,
    ) -> None:
        db = Database(tmp_path / "jobs.db")
        job_id = db.save_job(
            JobListing(
                title="Kwaliteitsadviseur",
                company="Kwadrant Meetlab",
                url="https://vacatures.example/kwadrant",
                source="test",
            )
        )
        db.save_company_research(job_id, '{"company_name": "Kwadrant Meetlab"}')
        db.record_company_lookup("Kwadrant Meetlab", "research", "found")
        db.save_company_review("Kwadrant Meetlab", '{"summary": "Prima."}')

        assert db.get_company_research_for_company(spelling) == [
            '{"company_name": "Kwadrant Meetlab"}'
        ]
        assert set(db.get_company_lookups(spelling, "research")) == {"found"}
        assert (
            db.get_company_review(spelling, max_age_days=365) == '{"summary": "Prima."}'
        )
        assert db.get_company_review("Kwadrant", max_age_days=365) is None
        assert not names_company("Unknown")
