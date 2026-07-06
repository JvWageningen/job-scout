"""Shared pytest fixtures for job_scout tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from job_scout.database import Database
from job_scout.models import Config, JobListing, JobStatus


@pytest.fixture()
def data_dir() -> Path:
    """Return the path to the test data directory."""
    d = Path(__file__).parent / "data"
    d.mkdir(exist_ok=True)
    return d


@pytest.fixture()
def tmp_db(tmp_path: Path) -> Database:
    """Return a temporary in-process SQLite database.

    Args:
        tmp_path: Pytest-provided temporary directory.
    """
    return Database(tmp_path / "test.db")


@pytest.fixture()
def base_config() -> Config:
    """Return a Config with sensible test defaults."""
    return Config(
        ntfy_topic="test-alerts",
        home_address="Teststraat 1, Amsterdam, 1234AB",
        max_travel_car=30,
        max_travel_pt=60,
        max_travel_bike=45,
        fit_score_threshold=60,
    )


@pytest.fixture()
def sample_job() -> JobListing:
    """Return a minimal valid JobListing for testing."""
    return JobListing(
        title="Software Engineer",
        company="ACME Corp",
        location="Amsterdam",
        url="https://example.com/jobs/1",
        source="indeed",
        seen_at=datetime.now(UTC),
    )


@pytest.fixture()
def matched_job() -> JobListing:
    """Return a JobListing in MATCHED state with a fit score."""
    return JobListing(
        title="Data Analyst",
        company="Widgets BV",
        location="Haarlem",
        url="https://example.com/jobs/2",
        source="linkedin",
        status=JobStatus.MATCHED,
        fit_score=75,
        fit_reasoning="Strong alignment with analytics background",
        seen_at=datetime.now(UTC),
    )
