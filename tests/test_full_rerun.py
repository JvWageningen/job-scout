"""Tests for --full rerun: upsert path, dedup bypass, re-notification."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from job_scout.database import Database
from job_scout.models import JobListing, JobStatus


def _job(
    url: str = "https://example.com/job/1",
    title: str = "Engineer",
    company: str = "Acme",
    fit_score: int | None = None,
    status: JobStatus = JobStatus.NEW,
) -> JobListing:
    return JobListing(
        title=title,
        company=company,
        url=url,
        source="test",
        seen_at=datetime.now(UTC),
        fit_score=fit_score,
        status=status,
    )


def test_save_job_insert_or_ignore_by_default(tmp_path: Path) -> None:
    """Normal save_job ignores conflicts; existing row is unchanged."""
    db = Database(tmp_path / "jobs.db")
    job = _job(fit_score=80, status=JobStatus.MATCHED)
    db.save_job(job)
    # Try to overwrite with different score
    updated = _job(fit_score=50, status=JobStatus.REJECTED)
    db.save_job(updated)  # should be ignored
    matches = db.get_recent_matches()
    assert len(matches) == 1
    assert matches[0].fit_score == 80


def test_save_job_upsert_overwrites_evaluation_columns(tmp_path: Path) -> None:
    """update_existing=True upserts: fit_score and status are overwritten."""
    db = Database(tmp_path / "jobs.db")
    job = _job(fit_score=40, status=JobStatus.REJECTED)
    row_id = db.save_job(job)
    assert row_id > 0

    # Re-evaluate with better score
    better = _job(fit_score=90, status=JobStatus.MATCHED)
    new_id = db.save_job(better, update_existing=True)
    assert new_id == row_id  # same row, not a duplicate

    matches = db.get_recent_matches()
    assert len(matches) == 1
    assert matches[0].fit_score == 90
    assert matches[0].status == JobStatus.MATCHED


def test_save_job_upsert_preserves_seen_at(tmp_path: Path) -> None:
    """Upsert does not overwrite seen_at of the original row."""
    db = Database(tmp_path / "jobs.db")
    original = _job()
    db.save_job(original)

    # Read back original seen_at
    with db._conn() as conn:
        row = conn.execute(
            "SELECT seen_at FROM jobs WHERE url=?", (original.url,)
        ).fetchone()
    original_seen_at = row["seen_at"]

    # Upsert with a newer seen_at — should be preserved from the original
    import time

    time.sleep(0.01)
    updated = _job()
    db.save_job(updated, update_existing=True)

    with db._conn() as conn:
        row2 = conn.execute(
            "SELECT seen_at FROM jobs WHERE url=?", (original.url,)
        ).fetchone()
    assert row2["seen_at"] == original_seen_at


def test_is_duplicate_returns_true_for_known_url(tmp_path: Path) -> None:
    """is_duplicate detects a job by URL after save."""
    db = Database(tmp_path / "jobs.db")
    job = _job()
    db.save_job(job)
    assert db.is_duplicate(job) is True


def test_full_rerun_bypasses_dedup(tmp_path: Path) -> None:
    """In full mode all jobs are re-evaluated regardless of is_duplicate."""
    db = Database(tmp_path / "jobs.db")
    job = _job(fit_score=30, status=JobStatus.REJECTED)
    db.save_job(job)

    # Confirm it's a duplicate
    assert db.is_duplicate(job) is True

    # Simulate what _run_pipeline does with full=True: skip is_duplicate check
    updated = _job(fit_score=85, status=JobStatus.MATCHED)
    new_id = db.save_job(updated, update_existing=True)
    assert new_id > 0
    matches = db.get_recent_matches()
    assert matches[0].fit_score == 85


def test_upsert_returns_real_row_id(tmp_path: Path) -> None:
    """update_existing=True returns the real existing row id (not 0)."""
    db = Database(tmp_path / "jobs.db")
    first_id = db.save_job(_job())
    assert first_id > 0

    upsert_id = db.save_job(_job(), update_existing=True)
    assert upsert_id == first_id
