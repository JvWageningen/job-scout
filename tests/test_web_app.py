"""Tests for the job-scout web dashboard API."""

from __future__ import annotations

import tempfile
import threading
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from job_scout.config import user_db_path, user_logs_dir
from job_scout.database import Database
from job_scout.models import JobListing, JobStatus
from job_scout.web.app import create_app


@pytest.fixture
def temp_data_dir(monkeypatch) -> Generator[Path, None, None]:  # noqa: ANN001
    """Create a temporary data directory for testing.

    Yields:
        Path to temporary data directory.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)

        # Set environment variable and reload config
        monkeypatch.setenv("JOB_SCOUT_DATA_DIR", str(tmp_path))

        # Reload config module to pick up environment variable
        import importlib

        import job_scout.config as config_module

        importlib.reload(config_module)

        # Initialize global config
        config_module.write_global_config({"llm_provider": "zai"})

        yield tmp_path

        # Restore the original config module state by reloading it
        importlib.reload(config_module)


@pytest.fixture
def client(temp_data_dir: Path) -> TestClient:
    """Create a FastAPI test client.

    Args:
        temp_data_dir: Temporary data directory.

    Returns:
        FastAPI TestClient.
    """
    app = create_app()
    return TestClient(app)


@pytest.fixture
def test_user(temp_data_dir: Path) -> str:
    """Create a test user with sample data.

    Args:
        temp_data_dir: Temporary data directory.

    Returns:
        User name.
    """
    from job_scout.config import save_user_config, user_dir

    user_name = "testuser"
    user_dir(user_name).mkdir(parents=True, exist_ok=True)

    # Save user config
    user_config = {
        "name": user_name,
        "profile_description": "Test profile",
        "email": "test@example.com",
    }
    save_user_config(user_name, user_config)

    return user_name


class TestGetUsers:
    """Tests for GET /api/users endpoint."""

    def test_get_users_empty(self, client: TestClient) -> None:
        """Test getting users when none exist."""
        response = client.get("/api/users")
        assert response.status_code == 200
        assert response.json() == []

    def test_get_users_with_users(self, client: TestClient, test_user: str) -> None:
        """Test getting users when some exist."""
        response = client.get("/api/users")
        assert response.status_code == 200
        users = response.json()
        assert test_user in users


class TestGetConfig:
    """Tests for GET /api/config endpoint."""

    def test_get_config_without_user(self, client: TestClient) -> None:
        """Test getting global config without user parameter."""
        response = client.get("/api/config")
        assert response.status_code == 200
        config = response.json()
        assert "llm_provider" in config

    def test_get_config_with_user(self, client: TestClient, test_user: str) -> None:
        """Test getting effective config for a specific user."""
        response = client.get(f"/api/config?user={test_user}")
        assert response.status_code == 200
        config = response.json()
        assert "name" in config

    def test_get_config_nonexistent_user(self, client: TestClient) -> None:
        """Test getting config for nonexistent user."""
        response = client.get("/api/config?user=nonexistent")
        assert response.status_code == 404

    def test_global_initialized_false_before_setup(self, monkeypatch) -> None:  # noqa: ANN001
        """global_initialized must be false before any global config is written.

        Uses its own isolated data dir rather than the shared ``client``
        fixture, since that fixture pre-writes a global config for the
        convenience of other tests.
        """
        import importlib

        import job_scout.config as config_module

        with tempfile.TemporaryDirectory() as tmpdir:
            monkeypatch.setenv("JOB_SCOUT_DATA_DIR", tmpdir)
            importlib.reload(config_module)
            try:
                app = create_app()
                response = TestClient(app).get("/api/config")
                assert response.status_code == 200
                assert response.json()["global_initialized"] is False
            finally:
                importlib.reload(config_module)

    def test_global_initialized_true_after_setup(self, client: TestClient) -> None:
        """global_initialized must be true once global config has been written."""
        init_response = client.post(
            "/api/global-init", json={"llm_provider": "claude_cli"}
        )
        assert init_response.status_code == 200

        response = client.get("/api/config")
        assert response.status_code == 200
        assert response.json()["global_initialized"] is True

    def test_global_initialized_absent_for_per_user_config(
        self, client: TestClient, test_user: str
    ) -> None:
        """global_initialized is a global-only concept; per-user config omits it."""
        response = client.get(f"/api/config?user={test_user}")
        assert response.status_code == 200
        assert "global_initialized" not in response.json()


class TestGetMatchedJobs:
    """Tests for GET /api/jobs/matched endpoint."""

    def test_get_matched_jobs_no_user(self, client: TestClient) -> None:
        """Test getting matched jobs without user parameter."""
        response = client.get("/api/jobs/matched")
        assert response.status_code == 400

    def test_get_matched_jobs_nonexistent_user(self, client: TestClient) -> None:
        """Test getting matched jobs for nonexistent user."""
        response = client.get("/api/jobs/matched?user=nonexistent")
        assert response.status_code == 404

    def test_get_matched_jobs_empty(self, client: TestClient, test_user: str) -> None:
        """Test getting matched jobs when none exist."""
        response = client.get(f"/api/jobs/matched?user={test_user}")
        assert response.status_code == 200
        assert response.json() == []

    def test_get_matched_jobs_with_data(
        self, client: TestClient, test_user: str, temp_data_dir: Path
    ) -> None:
        """Test getting matched jobs when some exist."""
        # Create a job in the database
        db_path = user_db_path(test_user)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db = Database(db_path)

        job = JobListing(
            title="Test Job",
            company="Test Corp",
            url="https://example.com",
            location="Amsterdam",
            source="test-source",
            status=JobStatus.MATCHED,
            fit_score=85,
        )
        db.save_job(job)

        response = client.get(f"/api/jobs/matched?user={test_user}&limit=10")
        assert response.status_code == 200
        jobs = response.json()
        assert len(jobs) == 1
        assert jobs[0]["title"] == "Test Job"
        assert jobs[0]["fit_score"] == 85


class TestGetRejectedJobs:
    """Tests for GET /api/jobs/rejected endpoint."""

    def test_get_rejected_jobs_no_user(self, client: TestClient) -> None:
        """Test getting rejected jobs without user parameter."""
        response = client.get("/api/jobs/rejected")
        assert response.status_code == 400

    def test_get_rejected_jobs_nonexistent_user(self, client: TestClient) -> None:
        """Test getting rejected jobs for nonexistent user."""
        response = client.get("/api/jobs/rejected?user=nonexistent")
        assert response.status_code == 404

    def test_get_rejected_jobs_empty(self, client: TestClient, test_user: str) -> None:
        """Test getting rejected jobs when none exist."""
        response = client.get(f"/api/jobs/rejected?user={test_user}")
        assert response.status_code == 200
        assert response.json() == []

    def test_get_rejected_jobs_with_data(
        self, client: TestClient, test_user: str, temp_data_dir: Path
    ) -> None:
        """Test getting rejected jobs when some exist."""
        # Create a rejected job in the database
        db_path = user_db_path(test_user)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db = Database(db_path)

        job = JobListing(
            title="Rejected Job",
            company="Bad Corp",
            url="https://example.com",
            location="Moscow",
            source="test-source",
            status=JobStatus.REJECTED,
            fit_score=20,
            fit_reasoning="Low fit score",
        )
        db.save_job(job)

        response = client.get(f"/api/jobs/rejected?user={test_user}&limit=10")
        assert response.status_code == 200
        jobs = response.json()
        assert len(jobs) == 1
        assert jobs[0]["title"] == "Rejected Job"
        assert jobs[0]["fit_score"] == 20


class TestGetMatchedJobsFiltering:
    """Tests for filtering and sorting in GET /api/jobs/matched endpoint."""

    def test_get_matched_jobs_with_min_score_filter(
        self, client: TestClient, test_user: str, temp_data_dir: Path
    ) -> None:
        """Test filtering matched jobs by minimum score."""
        db_path = user_db_path(test_user)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db = Database(db_path)

        # Create jobs with different scores
        jobs_data = [
            ("High Score", 85),
            ("Low Score", 30),
            ("Medium Score", 60),
        ]

        for title, score in jobs_data:
            job = JobListing(
                title=title,
                company="Test Corp",
                url=f"https://example.com/{title.lower().replace(' ', '-')}",
                source="indeed",
                status=JobStatus.MATCHED,
                fit_score=score,
            )
            db.save_job(job)

        # Get all matched jobs
        response = client.get(f"/api/jobs/matched?user={test_user}&limit=20")
        assert response.status_code == 200
        assert len(response.json()) == 3

        # Filter by min_score = 70
        response = client.get(
            f"/api/jobs/matched?user={test_user}&limit=20&min_score=70"
        )
        assert response.status_code == 200
        results = response.json()
        assert len(results) == 1
        assert results[0]["title"] == "High Score"
        assert results[0]["fit_score"] == 85

    def test_get_matched_jobs_with_source_filter(
        self, client: TestClient, test_user: str, temp_data_dir: Path
    ) -> None:
        """Test filtering matched jobs by source."""
        db_path = user_db_path(test_user)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db = Database(db_path)

        # Create jobs with different sources
        jobs_data = [
            ("Indeed Job", "indeed"),
            ("LinkedIn Job", "linkedin"),
            ("Another Indeed Job", "indeed"),
        ]

        for title, source in jobs_data:
            job = JobListing(
                title=title,
                company="Test Corp",
                url=f"https://example.com/{title.lower().replace(' ', '-')}",
                source=source,
                status=JobStatus.MATCHED,
                fit_score=75,
            )
            db.save_job(job)

        # Get all matched jobs
        response = client.get(f"/api/jobs/matched?user={test_user}&limit=20")
        assert response.status_code == 200
        assert len(response.json()) == 3

        # Filter by source = indeed
        response = client.get(
            f"/api/jobs/matched?user={test_user}&limit=20&source=indeed"
        )
        assert response.status_code == 200
        results = response.json()
        assert len(results) == 2
        assert all(j["source"] == "indeed" for j in results)

    def test_get_matched_jobs_sort_by_score_desc(
        self, client: TestClient, test_user: str, temp_data_dir: Path
    ) -> None:
        """Test sorting matched jobs by score descending."""
        db_path = user_db_path(test_user)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db = Database(db_path)

        # Create jobs with different scores
        scores = [50, 90, 70]
        for idx, score in enumerate(scores):
            job = JobListing(
                title=f"Job {idx}",
                company="Test Corp",
                url=f"https://example.com/job-{idx}",
                source="indeed",
                status=JobStatus.MATCHED,
                fit_score=score,
            )
            db.save_job(job)

        # Sort by score descending
        response = client.get(
            f"/api/jobs/matched?user={test_user}&limit=20&sort=score_desc"
        )
        assert response.status_code == 200
        results = response.json()
        assert len(results) == 3
        assert results[0]["fit_score"] == 90
        assert results[1]["fit_score"] == 70
        assert results[2]["fit_score"] == 50

    def test_get_matched_jobs_sort_by_score_asc(
        self, client: TestClient, test_user: str, temp_data_dir: Path
    ) -> None:
        """Test sorting matched jobs by score ascending."""
        db_path = user_db_path(test_user)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db = Database(db_path)

        # Create jobs with different scores
        scores = [50, 90, 70]
        for idx, score in enumerate(scores):
            job = JobListing(
                title=f"Job {idx}",
                company="Test Corp",
                url=f"https://example.com/job-{idx}",
                source="indeed",
                status=JobStatus.MATCHED,
                fit_score=score,
            )
            db.save_job(job)

        # Sort by score ascending
        response = client.get(
            f"/api/jobs/matched?user={test_user}&limit=20&sort=score_asc"
        )
        assert response.status_code == 200
        results = response.json()
        assert len(results) == 3
        assert results[0]["fit_score"] == 50
        assert results[1]["fit_score"] == 70
        assert results[2]["fit_score"] == 90

    def test_get_matched_jobs_invalid_sort_parameter(
        self, client: TestClient, test_user: str, temp_data_dir: Path
    ) -> None:
        """Test that invalid sort parameter returns 400."""
        # Create an empty database
        db_path = user_db_path(test_user)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        Database(db_path)

        response = client.get(
            f"/api/jobs/matched?user={test_user}&limit=20&sort=invalid_sort"
        )
        assert response.status_code == 400

    def test_get_matched_jobs_sql_injection_in_source(
        self, client: TestClient, test_user: str, temp_data_dir: Path
    ) -> None:
        """Test that source filter is safe from SQL injection."""
        db_path = user_db_path(test_user)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db = Database(db_path)

        job = JobListing(
            title="Real Job",
            company="Test Corp",
            url="https://example.com/real",
            source="indeed",
            status=JobStatus.MATCHED,
            fit_score=75,
        )
        db.save_job(job)

        # Try SQL injection
        malicious_source = "' OR '1'='1"
        response = client.get(
            f"/api/jobs/matched?user={test_user}&limit=20&source={malicious_source}"
        )
        assert response.status_code == 200
        results = response.json()
        # Should return no results (no jobs with that source)
        assert len(results) == 0

        # Verify original job still exists
        response = client.get(f"/api/jobs/matched?user={test_user}&limit=20")
        assert len(response.json()) == 1

    def test_get_matched_jobs_combined_filters(
        self, client: TestClient, test_user: str, temp_data_dir: Path
    ) -> None:
        """Test applying multiple filters together."""
        db_path = user_db_path(test_user)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db = Database(db_path)

        # Create jobs with various combinations
        test_data = [
            ("Indeed High", "indeed", 85),
            ("Indeed Low", "indeed", 40),
            ("LinkedIn High", "linkedin", 80),
        ]

        for title, source, score in test_data:
            job = JobListing(
                title=title,
                company="Test Corp",
                url=f"https://example.com/{title.lower().replace(' ', '-')}",
                source=source,
                status=JobStatus.MATCHED,
                fit_score=score,
            )
            db.save_job(job)

        # Filter by source=indeed AND min_score=70
        response = client.get(
            f"/api/jobs/matched?user={test_user}&limit=20&source=indeed&min_score=70"
        )
        assert response.status_code == 200
        results = response.json()
        assert len(results) == 1
        assert results[0]["title"] == "Indeed High"
        assert results[0]["source"] == "indeed"
        assert results[0]["fit_score"] >= 70


class TestGetRejectedJobsFiltering:
    """Tests for filtering and sorting in GET /api/jobs/rejected endpoint."""

    def test_get_rejected_jobs_with_min_score_filter(
        self, client: TestClient, test_user: str, temp_data_dir: Path
    ) -> None:
        """Test filtering rejected jobs by minimum score."""
        db_path = user_db_path(test_user)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db = Database(db_path)

        # Create rejected jobs with different scores
        jobs_data = [
            ("Higher Score", 45),
            ("Lower Score", 20),
        ]

        for title, score in jobs_data:
            job = JobListing(
                title=title,
                company="Test Corp",
                url=f"https://example.com/{title.lower().replace(' ', '-')}",
                source="indeed",
                status=JobStatus.REJECTED,
                fit_score=score,
            )
            db.save_job(job)

        # Get all rejected jobs
        response = client.get(f"/api/jobs/rejected?user={test_user}&limit=20")
        assert response.status_code == 200
        assert len(response.json()) == 2

        # Filter by min_score = 30
        response = client.get(
            f"/api/jobs/rejected?user={test_user}&limit=20&min_score=30"
        )
        assert response.status_code == 200
        results = response.json()
        assert len(results) == 1
        assert results[0]["title"] == "Higher Score"
        assert results[0]["fit_score"] == 45

    def test_get_rejected_jobs_with_source_filter(
        self, client: TestClient, test_user: str, temp_data_dir: Path
    ) -> None:
        """Test filtering rejected jobs by source."""
        db_path = user_db_path(test_user)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db = Database(db_path)

        # Create rejected jobs with different sources
        jobs_data = [
            ("Indeed Rejected", "indeed"),
            ("LinkedIn Rejected", "linkedin"),
        ]

        for title, source in jobs_data:
            job = JobListing(
                title=title,
                company="Test Corp",
                url=f"https://example.com/{title.lower().replace(' ', '-')}",
                source=source,
                status=JobStatus.REJECTED,
                fit_score=25,
            )
            db.save_job(job)

        # Filter by source = indeed
        response = client.get(
            f"/api/jobs/rejected?user={test_user}&limit=20&source=indeed"
        )
        assert response.status_code == 200
        results = response.json()
        assert len(results) == 1
        assert results[0]["source"] == "indeed"

    def test_get_rejected_jobs_sort_by_score_desc(
        self, client: TestClient, test_user: str, temp_data_dir: Path
    ) -> None:
        """Test sorting rejected jobs by score descending."""
        db_path = user_db_path(test_user)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db = Database(db_path)

        # Create rejected jobs with different scores
        scores = [20, 50, 35]
        for idx, score in enumerate(scores):
            job = JobListing(
                title=f"Rejected {idx}",
                company="Test Corp",
                url=f"https://example.com/rejected-{idx}",
                source="indeed",
                status=JobStatus.REJECTED,
                fit_score=score,
            )
            db.save_job(job)

        # Sort by score descending
        response = client.get(
            f"/api/jobs/rejected?user={test_user}&limit=20&sort=score_desc"
        )
        assert response.status_code == 200
        results = response.json()
        assert len(results) == 3
        assert results[0]["fit_score"] == 50
        assert results[1]["fit_score"] == 35
        assert results[2]["fit_score"] == 20

    def test_get_rejected_jobs_invalid_sort_parameter(
        self, client: TestClient, test_user: str, temp_data_dir: Path
    ) -> None:
        """Test that invalid sort parameter returns 400."""
        # Create an empty database
        db_path = user_db_path(test_user)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        Database(db_path)

        response = client.get(
            f"/api/jobs/rejected?user={test_user}&limit=20&sort=bad_sort"
        )
        assert response.status_code == 400

    def test_get_rejected_jobs_sql_injection_in_source(
        self, client: TestClient, test_user: str, temp_data_dir: Path
    ) -> None:
        """Test that source filter is safe from SQL injection."""
        db_path = user_db_path(test_user)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db = Database(db_path)

        job = JobListing(
            title="Real Rejected Job",
            company="Test Corp",
            url="https://example.com/real-rejected",
            source="indeed",
            status=JobStatus.REJECTED,
            fit_score=25,
        )
        db.save_job(job)

        # Try SQL injection with quote
        malicious_source = "'; DROP TABLE jobs; --"
        response = client.get(
            f"/api/jobs/rejected?user={test_user}&limit=20&source={malicious_source}"
        )
        assert response.status_code == 200
        results = response.json()
        # Should return no results
        assert len(results) == 0

        # Verify job table still exists
        response = client.get(f"/api/jobs/rejected?user={test_user}&limit=20")
        assert len(response.json()) == 1


class TestGetRunsHistory:
    """Tests for GET /api/runs/history endpoint."""

    def test_get_runs_history_no_user(self, client: TestClient) -> None:
        """Test that /api/runs/history requires user parameter."""
        response = client.get("/api/runs/history")
        assert response.status_code == 400
        data = response.json()
        assert "User is required" in data["detail"]

    def test_get_runs_history_nonexistent_user(self, client: TestClient) -> None:
        """Test that /api/runs/history returns 404 for nonexistent user."""
        response = client.get("/api/runs/history?user=nonexistent")
        assert response.status_code == 404

    def test_get_runs_history_empty(
        self, client: TestClient, test_user: str, temp_data_dir: Path
    ) -> None:
        """Test that /api/runs/history returns empty list for new user."""
        db_path = user_db_path(test_user)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        Database(db_path)  # Create the database

        response = client.get(f"/api/runs/history?user={test_user}")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 0

    def test_get_runs_history_with_data(
        self, client: TestClient, test_user: str, temp_data_dir: Path
    ) -> None:
        """Test that /api/runs/history returns run history data."""
        from datetime import datetime as dt

        from job_scout.models import RunStats

        db_path = user_db_path(test_user)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db = Database(db_path)

        # Add a run
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
            errors=["Error 1"],
        )
        now = dt.now()
        db.save_run_stats(stats, now, 45.5)

        response = client.get(f"/api/runs/history?user={test_user}")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1

        entry = data[0]
        assert entry["scraped"] == 100
        assert entry["matched"] == 5
        assert entry["rejected"] == 20
        assert entry["notified"] == 5
        assert entry["errors"] == 1
        assert abs(entry["duration_seconds"] - 45.5) < 0.1

    def test_get_runs_history_limit_parameter(
        self, client: TestClient, test_user: str, temp_data_dir: Path
    ) -> None:
        """Test that /api/runs/history respects limit parameter."""
        from datetime import datetime as dt

        from job_scout.models import RunStats

        db_path = user_db_path(test_user)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db = Database(db_path)

        # Add 5 runs
        for i in range(5):
            stats = RunStats(
                scraped=100 + i,
                matched=5 + i,
                rejected=20,
                notified=5,
                errors=[],
            )
            db.save_run_stats(stats, dt.now(), 30.0)

        # Request with limit=2
        response = client.get(f"/api/runs/history?user={test_user}&limit=2")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2

    def test_get_runs_history_ordering(
        self, client: TestClient, test_user: str, temp_data_dir: Path
    ) -> None:
        """Test that /api/runs/history returns runs in reverse chronological order."""
        from datetime import datetime as dt
        from datetime import timedelta

        from job_scout.models import RunStats

        db_path = user_db_path(test_user)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db = Database(db_path)

        # Add 3 runs with different timestamps
        base_time = dt.now()
        for i in range(3):
            stats = RunStats(
                scraped=100 + i,
                matched=5,
                rejected=20,
                notified=5,
                errors=[],
            )
            run_time = base_time + timedelta(hours=i)
            db.save_run_stats(stats, run_time, 30.0)

        response = client.get(f"/api/runs/history?user={test_user}&limit=10")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 3
        # Should be newest first
        assert data[0]["scraped"] == 102  # Last run added
        assert data[1]["scraped"] == 101
        assert data[2]["scraped"] == 100


class TestGetScheduleStatus:
    """Tests for GET /api/schedule/status endpoint."""

    def test_get_schedule_status(self, client: TestClient) -> None:
        """Test getting schedule status."""
        response = client.get("/api/schedule/status")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data
        assert isinstance(data["status"], str)


class TestGetLogs:
    """Tests for GET /api/logs endpoint."""

    def test_get_logs_no_user(self, client: TestClient) -> None:
        """Test getting logs without user parameter."""
        response = client.get("/api/logs")
        assert response.status_code == 400

    def test_get_logs_nonexistent_user(self, client: TestClient) -> None:
        """Test getting logs for nonexistent user."""
        response = client.get("/api/logs?user=nonexistent")
        assert response.status_code == 404

    def test_get_logs_empty(self, client: TestClient, test_user: str) -> None:
        """Test getting logs when none exist."""
        response = client.get(f"/api/logs?user={test_user}")
        assert response.status_code == 200
        assert response.json() == []

    def test_get_logs_with_data(
        self, client: TestClient, test_user: str, temp_data_dir: Path
    ) -> None:
        """Test getting logs when some exist."""
        logs_dir = user_logs_dir(test_user)
        logs_dir.mkdir(parents=True, exist_ok=True)

        # Create a test log file
        log_file = logs_dir / "test.log"
        log_file.write_text("Test log content\n")

        response = client.get(f"/api/logs?user={test_user}")
        assert response.status_code == 200
        logs = response.json()
        assert len(logs) >= 1
        assert any(log["name"] == "test.log" for log in logs)


class TestGetLogFile:
    """Tests for GET /api/logs/{filename} endpoint."""

    def test_get_log_file_no_user(self, client: TestClient) -> None:
        """Test getting log file without user parameter."""
        response = client.get("/api/logs/test.log")
        assert response.status_code == 400

    def test_get_log_file_nonexistent_user(self, client: TestClient) -> None:
        """Test getting log file for nonexistent user."""
        response = client.get("/api/logs/test.log?user=nonexistent")
        assert response.status_code == 404

    def test_get_log_file_not_found(self, client: TestClient, test_user: str) -> None:
        """Test getting nonexistent log file."""
        response = client.get(f"/api/logs/nonexistent.log?user={test_user}")
        assert response.status_code == 404

    def test_get_log_file_path_traversal_attack(
        self, client: TestClient, test_user: str
    ) -> None:
        """Test that path traversal attacks are blocked."""
        response = client.get(f"/api/logs/..%2Fetc%2Fpasswd?user={test_user}")
        # Should reject path traversal attempts with either 400 or 404
        assert response.status_code in (400, 404)

    def test_get_log_file_path_separator_attack(
        self, client: TestClient, test_user: str
    ) -> None:
        """Test that absolute paths are blocked."""
        response = client.get(f"/api/logs/subdir%2Ffile.log?user={test_user}")
        # Should reject path separator attempts with either 400 or 404
        assert response.status_code in (400, 404)

    def test_get_log_file_success(
        self, client: TestClient, test_user: str, temp_data_dir: Path
    ) -> None:
        """Test successfully getting log file content."""
        logs_dir = user_logs_dir(test_user)
        logs_dir.mkdir(parents=True, exist_ok=True)

        # Create a test log file
        log_file = logs_dir / "test.log"
        log_content = "Line 1\nLine 2\nLine 3\n"
        log_file.write_text(log_content)

        response = client.get(f"/api/logs/test.log?user={test_user}&lines=10")
        assert response.status_code == 200
        data = response.json()
        assert data["filename"] == "test.log"
        assert "Line 1" in data["content"]
        assert data["total_lines"] == 3

    def test_get_log_file_tail_lines(
        self, client: TestClient, test_user: str, temp_data_dir: Path
    ) -> None:
        """Test tailing only the last N lines."""
        logs_dir = user_logs_dir(test_user)
        logs_dir.mkdir(parents=True, exist_ok=True)

        # Create a test log file with multiple lines
        log_file = logs_dir / "test.log"
        lines = [f"Line {i}\n" for i in range(1, 11)]
        log_file.write_text("".join(lines))

        response = client.get(f"/api/logs/test.log?user={test_user}&lines=3")
        assert response.status_code == 200
        data = response.json()
        # Should get the last 3 lines
        assert "Line 8\n" in data["content"]
        assert "Line 9\n" in data["content"]
        assert "Line 10\n" in data["content"]
        assert "Line 1\n" not in data["content"]


class TestStaticFiles:
    """Tests for static file serving."""

    def test_serve_index(self, client: TestClient) -> None:
        """Test serving index.html."""
        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "job-scout" in response.text

    def test_serve_app_js(self, client: TestClient) -> None:
        """Test serving app.js."""
        response = client.get("/app.js")
        assert response.status_code == 200
        assert "javascript" in response.headers["content-type"]

    def test_serve_style_css(self, client: TestClient) -> None:
        """Test serving style.css."""
        response = client.get("/style.css")
        assert response.status_code == 200
        assert "css" in response.headers["content-type"]


class TestGlobalInit:
    """Tests for POST /api/global-init endpoint."""

    def test_initialize_global_success(self, client: TestClient) -> None:
        """Test initializing global configuration."""
        response = client.post(
            "/api/global-init",
            json={"llm_provider": "zai"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "initialized" in data["status"].lower()

    def test_initialize_global_update(self, client: TestClient) -> None:
        """Test that initializing global config can be updated."""
        # First initialization
        response1 = client.post(
            "/api/global-init",
            json={"llm_provider": "zai"},
        )
        assert response1.status_code == 200

        # Second initialization with different values should succeed (update)
        response2 = client.post(
            "/api/global-init",
            json={"llm_provider": "claude_cli"},
        )
        assert response2.status_code == 200

        # Verify the update took effect
        config = client.get("/api/config").json()
        assert config["llm_provider"] == "claude_cli"


class TestPostUsers:
    """Tests for POST /api/users endpoint."""

    def test_create_user_success(self, client: TestClient) -> None:
        """Test creating a new user."""
        response = client.post("/api/users", json={"name": "newuser"})
        assert response.status_code == 200
        data = response.json()
        assert "created successfully" in data["status"]
        # Verify user can be listed
        users = client.get("/api/users").json()
        assert "newuser" in users

    def test_create_user_no_name(self, client: TestClient) -> None:
        """Test creating user without name."""
        response = client.post("/api/users", json={})
        assert response.status_code == 400

    def test_create_user_duplicate(self, client: TestClient, test_user: str) -> None:
        """Test creating a user that already exists."""
        response = client.post("/api/users", json={"name": test_user})
        assert response.status_code == 409

    def test_create_user_named_all(self, client: TestClient) -> None:
        """Test creating a user named 'all' (reserved)."""
        response = client.post("/api/users", json={"name": "all"})
        assert response.status_code == 400


class TestPostConfig:
    """Tests for POST /api/config endpoint."""

    def test_update_config_success(self, client: TestClient, test_user: str) -> None:
        """Test updating user configuration."""
        response = client.post(
            "/api/config",
            json={"user": test_user, "values": {"profile_description": "Engineer"}},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"

        # Verify the update
        config = client.get(f"/api/config?user={test_user}").json()
        assert config["profile_description"] == "Engineer"

    def test_update_config_invalid_key(
        self, client: TestClient, test_user: str
    ) -> None:
        """Test updating with invalid config key (validation)."""
        response = client.post(
            "/api/config",
            json={"user": test_user, "values": {"invalid_key_xyz": "value"}},
        )
        assert response.status_code == 200
        data = response.json()
        # Should have errors for the invalid key
        assert "errors" in data

    def test_update_config_secret_key_rejected(
        self, client: TestClient, test_user: str
    ) -> None:
        """Test that secret keys cannot be set via config endpoint."""
        response = client.post(
            "/api/config",
            json={"user": test_user, "values": {"zai_api_key": "secret"}},
        )
        assert response.status_code == 200
        data = response.json()
        # Should have an error
        assert "errors" in data or data["status"] != "success"

    def test_update_config_nonexistent_user(self, client: TestClient) -> None:
        """Test updating config for nonexistent user."""
        response = client.post(
            "/api/config",
            json={"user": "nonexistent", "values": {"profile_description": "Test"}},
        )
        assert response.status_code == 404

    def test_update_config_get_config_never_returns_raw_secret(
        self, client: TestClient, test_user: str
    ) -> None:
        """Test that GET /api/config never returns raw secret values."""
        # Try to update a secret (which should fail in POST)
        # but verify GET never returns it unmasked
        response = client.get(f"/api/config?user={test_user}")
        assert response.status_code == 200
        config = response.json()
        # If any secrets are present, they should be masked
        for key, value in config.items():
            if "key" in key.lower() and value:
                # Should be masked like ****last4chars
                assert value.startswith("***") or not value.startswith("actual_")


class TestPostSecrets:
    """Tests for POST /api/secrets endpoint."""

    def test_update_secrets_success(self, client: TestClient) -> None:
        """Test updating secrets."""
        response = client.post("/api/secrets", json={"zai_api_key": "test-key-123"})
        assert response.status_code == 200
        data = response.json()
        assert "updated" in data["status"].lower() or "success" in data["status"]

    def test_update_secrets_no_changes(self, client: TestClient) -> None:
        """Test update with no actual secret values."""
        response = client.post("/api/secrets", json={})
        assert response.status_code == 200
        data = response.json()
        assert "no changes" in data["status"].lower()


class TestSites:
    """Tests for /api/sites endpoints."""

    def test_get_sites_empty(self, client: TestClient, test_user: str) -> None:
        """Test getting sites when none exist."""
        response = client.get(f"/api/sites?user={test_user}")
        assert response.status_code == 200
        assert response.json() == []

    def test_add_site_success(self, client: TestClient, test_user: str) -> None:
        """Test adding a custom site."""
        response = client.post(
            "/api/sites",
            json={
                "user": test_user,
                "url": "https://example.com/jobs",
                "name": "Example Jobs",
            },
        )
        assert response.status_code == 200

        # Verify site was added
        sites = client.get(f"/api/sites?user={test_user}").json()
        assert len(sites) == 1
        assert sites[0]["url"] == "https://example.com/jobs"
        assert sites[0]["name"] == "Example Jobs"

    def test_add_site_duplicate_url(self, client: TestClient, test_user: str) -> None:
        """Test adding a site with duplicate URL."""
        url = "https://example.com/jobs"
        client.post(
            "/api/sites",
            json={"user": test_user, "url": url, "name": "First"},
        )
        response = client.post(
            "/api/sites",
            json={"user": test_user, "url": url, "name": "Second"},
        )
        assert response.status_code == 409

    def test_add_site_no_user(self, client: TestClient) -> None:
        """Test adding site without user parameter."""
        response = client.post(
            "/api/sites",
            json={"url": "https://example.com/jobs"},
        )
        assert response.status_code == 400

    def test_remove_site_success(self, client: TestClient, test_user: str) -> None:
        """Test removing a site by URL."""
        url = "https://example.com/jobs"
        client.post(
            "/api/sites",
            json={"user": test_user, "url": url, "name": "Test"},
        )

        response = client.delete(
            f"/api/sites?user={test_user}&identifier={url}",
        )
        assert response.status_code == 200

        # Verify site was removed
        sites = client.get(f"/api/sites?user={test_user}").json()
        assert len(sites) == 0

    def test_remove_site_by_name(self, client: TestClient, test_user: str) -> None:
        """Test removing a site by name."""
        client.post(
            "/api/sites",
            json={
                "user": test_user,
                "url": "https://example.com/jobs",
                "name": "Example",
            },
        )

        response = client.delete(
            f"/api/sites?user={test_user}&identifier=Example",
        )
        assert response.status_code == 200

    def test_remove_site_not_found(self, client: TestClient, test_user: str) -> None:
        """Test removing a nonexistent site."""
        response = client.delete(
            f"/api/sites?user={test_user}&identifier=nonexistent",
        )
        assert response.status_code == 404


class TestSchedule:
    """Tests for /api/schedule endpoints."""

    def test_install_schedule_success(self, client: TestClient) -> None:
        """Test installing a schedule."""
        response = client.post("/api/schedule", json={"hour": 9, "minute": 30})
        assert response.status_code == 200
        data = response.json()
        assert "09:30" in data["status"]

    def test_install_schedule_invalid_hour(self, client: TestClient) -> None:
        """Test installing schedule with invalid hour."""
        response = client.post("/api/schedule", json={"hour": 25, "minute": 0})
        assert response.status_code == 400

    def test_remove_schedule(self, client: TestClient) -> None:
        """Test removing a schedule."""
        # First install one
        client.post("/api/schedule", json={"hour": 8, "minute": 0})

        # Then remove it
        response = client.delete("/api/schedule")
        assert response.status_code == 200
        data = response.json()
        assert "removed" in data["status"].lower()


class TestLLMTestConnection:
    """Tests for POST /api/llm/test-connection endpoint."""

    def test_test_connection_no_provider(self, client: TestClient) -> None:
        """Test without provider parameter."""
        response = client.post("/api/llm/test-connection", json={"model": "test"})
        assert response.status_code == 400

    def test_test_connection_invalid_provider(self, client: TestClient) -> None:
        """Test with invalid provider."""
        response = client.post(
            "/api/llm/test-connection",
            json={"provider": "invalid", "model": "test"},
        )
        assert response.status_code == 400

    def test_test_connection_claude_cli(self, client: TestClient) -> None:
        """Test connection for claude_cli (path check only)."""
        response = client.post(
            "/api/llm/test-connection",
            json={"provider": "claude_cli"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "ok" in data
        assert "message" in data

    def test_test_connection_local(self, client: TestClient) -> None:
        """Test connection for local provider."""
        response = client.post(
            "/api/llm/test-connection",
            json={
                "provider": "local",
                "model": "llama3.1",
                "base_url": "http://localhost:11434/v1",
            },
        )
        assert response.status_code == 200
        data = response.json()
        # Might fail (if no local LLM running), but should return ok + message
        assert "ok" in data
        assert "message" in data


class TestRun:
    """Tests for /api/run endpoints."""

    def test_run_pipeline_no_user(self, client: TestClient, monkeypatch) -> None:
        """Test pipeline run returns error for missing user selection."""

        # Mock to avoid actual execution
        def mock_execute_run_global(*, dry_run=False, full=False):
            pass

        monkeypatch.setattr(
            "job_scout.cli._execute_run_global", mock_execute_run_global
        )

        response = client.post("/api/run", json={"user": None, "dry_run": True})
        # Should either succeed (global run) or give clear error
        assert response.status_code in (200, 400, 500)

    def test_run_all_users_success(
        self, client: TestClient, temp_data_dir: Path, monkeypatch
    ) -> None:
        """Test running all users sequentially."""
        from job_scout.config import save_user_config, user_dir

        # Create two test users
        for user_name in ["user1", "user2"]:
            user_dir(user_name).mkdir(parents=True, exist_ok=True)
            save_user_config(
                user_name,
                {
                    "name": user_name,
                    "profile_description": "Test profile",
                },
            )

        # Mock execution to avoid real LLM/network calls
        def mock_execute_run(name, *, dry_run=False, full=False):
            pass

        monkeypatch.setattr("job_scout.cli._execute_run", mock_execute_run)
        monkeypatch.setattr(
            "job_scout.evaluator.check_llm_available", lambda config: (True, None)
        )

        response = client.post(
            "/api/run",
            json={"all": True, "dry_run": True, "full": False},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "running"
        assert "all 2 users" in data["message"].lower()

    def test_run_all_users_no_users_configured(self, client: TestClient) -> None:
        """Test running all users when none are configured."""
        response = client.post(
            "/api/run",
            json={"all": True, "dry_run": True},
        )
        assert response.status_code == 400
        assert "No users configured" in response.json()["detail"]

    def test_run_user_and_all_conflicting(self, client: TestClient) -> None:
        """Test that specifying both user and all returns an error."""
        response = client.post(
            "/api/run",
            json={"user": "testuser", "all": True, "dry_run": True},
        )
        assert response.status_code == 400
        assert "Cannot specify both" in response.json()["detail"]

    def test_run_pipeline_nonexistent_user(self, client: TestClient) -> None:
        """Test pipeline run with nonexistent user."""
        response = client.post(
            "/api/run",
            json={"user": "nonexistent", "dry_run": True},
        )
        assert response.status_code == 404

    def test_run_pipeline_success(
        self, client: TestClient, test_user: str, monkeypatch
    ) -> None:
        """Test successful pipeline run start."""

        # Mock the actual execution to avoid real LLM/network calls
        def mock_execute_run(name, *, dry_run=False, full=False):
            pass

        monkeypatch.setattr("job_scout.cli._execute_run", mock_execute_run)

        response = client.post(
            "/api/run",
            json={"user": test_user, "dry_run": True, "full": False},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "running"
        assert "started" in data["message"].lower()

    def test_run_status_no_run(self, client: TestClient, test_user: str) -> None:
        """Test getting run status when no run is in progress."""
        # Clear the global registry to ensure clean state
        from job_scout.web.app import _registry_lock, _run_registry

        with _registry_lock:
            _run_registry.clear()

        response = client.get(f"/api/run/status?user={test_user}")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "idle"

    def test_run_status_running(
        self, client: TestClient, test_user: str, monkeypatch
    ) -> None:
        """Test getting run status while run is in progress."""
        # Start a run
        client.post(
            "/api/run",
            json={"user": test_user, "dry_run": True},
        )

        response = client.get(f"/api/run/status?user={test_user}")
        assert response.status_code == 200
        data = response.json()
        # Status should be running or already done (depending on timing)
        assert data["status"] in ("running", "done", "error")

    def test_run_deduplication(
        self, client: TestClient, test_user: str, monkeypatch
    ) -> None:
        """Test that run status is properly tracked in registry."""

        # Block the background thread inside the mocked execution so the
        # "running" state can be observed deterministically, rather than
        # racing the (otherwise near-instant) background thread to completion.
        release_run = threading.Event()

        def mock_execute_run(name, *, dry_run=False, full=False):
            release_run.wait(timeout=5)

        monkeypatch.setattr("job_scout.cli._execute_run", mock_execute_run)
        monkeypatch.setattr(
            "job_scout.evaluator.check_llm_available", lambda config: (True, None)
        )

        # Clear registry
        from job_scout.web.app import _registry_lock, _run_registry

        with _registry_lock:
            _run_registry.clear()

        # Start a run
        response = client.post(
            "/api/run",
            json={"user": test_user, "dry_run": True},
        )
        assert response.status_code == 200

        # Check that status starts as running
        with _registry_lock:
            entry = _run_registry.get(test_user)
            assert entry is not None
            assert entry["status"] == "running"

        release_run.set()


class TestKeywords:
    """Tests for /api/keywords endpoints."""

    def test_get_keywords_no_user(self, client: TestClient) -> None:
        """Test getting keywords with no user specified."""
        response = client.get("/api/keywords")
        assert response.status_code == 200
        data = response.json()
        assert "include" in data or "dutch" in data

    def test_get_keywords_nonexistent_user(self, client: TestClient) -> None:
        """Test getting keywords for nonexistent user."""
        response = client.get("/api/keywords?user=nonexistent")
        assert response.status_code == 404

    def test_get_keywords_with_user(self, client: TestClient, test_user: str) -> None:
        """Test getting keywords for valid user."""
        response = client.get(f"/api/keywords?user={test_user}")
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data.get("dutch"), list)
        assert isinstance(data.get("english"), list)
        assert isinstance(data.get("title_include"), list)
        assert isinstance(data.get("title_exclude"), list)

    def test_refresh_keywords_no_user(self, client: TestClient) -> None:
        """Test refreshing keywords with no user specified."""
        response = client.post(
            "/api/keywords/refresh",
            json={"user": None},
        )
        # Might fail due to no profile configured
        assert response.status_code in (200, 400, 500)

    def test_refresh_keywords_nonexistent_user(self, client: TestClient) -> None:
        """Test refreshing keywords for nonexistent user."""
        response = client.post(
            "/api/keywords/refresh",
            json={"user": "nonexistent"},
        )
        assert response.status_code == 404

    def test_refresh_keywords_success(
        self, client: TestClient, test_user: str, monkeypatch
    ) -> None:
        """Test successfully refreshing keywords."""
        from job_scout.llm.base import LLMClient
        from job_scout.models import KeywordsResult

        # Mock the LLM client to avoid network calls
        class MockLLMClient(LLMClient):
            def complete(
                self, prompt: str, *, purpose: str = "", timeout: float = 30.0
            ) -> str:
                return ""

            def check_available(self) -> str | None:
                return None

        # Mock get_llm_client to return the mock client
        def mock_get_llm_client(config):
            return MockLLMClient()

        monkeypatch.setattr("job_scout.web.app.get_llm_client", mock_get_llm_client)

        # Mock the generate_keywords function
        def mock_generate_keywords(profile, cv_text, *, client=None):
            return KeywordsResult(
                dutch=["test_nl"],
                english=["test_en"],
                title_include=["title1"],
                title_exclude=["exclude1"],
            )

        monkeypatch.setattr(
            "job_scout.evaluator.generate_keywords",
            mock_generate_keywords,
        )

        response = client.post(
            "/api/keywords/refresh",
            json={"user": test_user},
        )
        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert data["dutch"] == ["test_nl"]
        assert data["english"] == ["test_en"]
        assert data["title_include"] == ["title1"]
        assert data["title_exclude"] == ["exclude1"]


class TestTokenAuthentication:
    """Tests for dashboard token authentication."""

    def test_no_token_configured_api_requests_allowed(
        self, temp_data_dir: Path, monkeypatch
    ) -> None:
        """Test that API requests succeed when no token is configured."""
        from job_scout.config import write_global_config
        from job_scout.web.app import create_app

        # Ensure no token is configured
        write_global_config({})

        # Create a fresh app and client
        app = create_app()
        client = TestClient(app)

        # API request without token should succeed
        response = client.get("/api/users")
        assert response.status_code == 200

    def test_static_files_accessible_without_token(
        self, temp_data_dir: Path, monkeypatch
    ) -> None:
        """Test that static files are accessible without token."""
        from job_scout.config import write_global_config
        from job_scout.web.app import create_app

        # Ensure no token is configured
        write_global_config({})

        # Create a fresh app and client
        app = create_app()
        client = TestClient(app)

        # Static files should be accessible without token
        response = client.get("/")
        assert response.status_code == 200

        response = client.get("/app.js")
        assert response.status_code == 200

        response = client.get("/style.css")
        assert response.status_code == 200

    def test_token_configured_api_requests_without_token_rejected(
        self, temp_data_dir: Path, monkeypatch
    ) -> None:
        """Test that API requests are rejected without token when configured."""
        from job_scout.config import update_secrets, write_global_config
        from job_scout.web.app import create_app

        # Configure a token
        write_global_config({})
        update_secrets({"dashboard_token": "test-token-123"})

        # Reload the secrets module to pick up the new token
        import importlib

        import job_scout.config as config_module

        importlib.reload(config_module)

        # Create a fresh app and client
        app = create_app()
        client = TestClient(app)

        # API request without token should fail with 401
        response = client.get("/api/users")
        assert response.status_code == 401
        assert "detail" in response.json()

    def test_token_configured_api_requests_with_correct_token_succeed(
        self, temp_data_dir: Path, monkeypatch
    ) -> None:
        """Test that API requests succeed with correct token."""
        from job_scout.config import update_secrets, write_global_config
        from job_scout.web.app import create_app

        # Configure a token
        write_global_config({})
        update_secrets({"dashboard_token": "test-token-123"})

        # Reload the secrets module to pick up the new token
        import importlib

        import job_scout.config as config_module

        importlib.reload(config_module)

        # Create a fresh app and client
        app = create_app()
        client = TestClient(app)

        # API request with correct token should succeed
        response = client.get(
            "/api/users", headers={"Authorization": "Bearer test-token-123"}
        )
        assert response.status_code == 200

    def test_token_configured_api_requests_with_wrong_token_rejected(
        self, temp_data_dir: Path, monkeypatch
    ) -> None:
        """Test that API requests are rejected with wrong token."""
        from job_scout.config import update_secrets, write_global_config
        from job_scout.web.app import create_app

        # Configure a token
        write_global_config({})
        update_secrets({"dashboard_token": "test-token-123"})

        # Reload the secrets module to pick up the new token
        import importlib

        import job_scout.config as config_module

        importlib.reload(config_module)

        # Create a fresh app and client
        app = create_app()
        client = TestClient(app)

        # API request with wrong token should fail with 401
        response = client.get(
            "/api/users", headers={"Authorization": "Bearer wrong-token"}
        )
        assert response.status_code == 401

    def test_token_configured_invalid_auth_header_format(
        self, temp_data_dir: Path, monkeypatch
    ) -> None:
        """Test that requests with invalid auth header format are rejected."""
        from job_scout.config import update_secrets, write_global_config
        from job_scout.web.app import create_app

        # Configure a token
        write_global_config({})
        update_secrets({"dashboard_token": "test-token-123"})

        # Reload the secrets module to pick up the new token
        import importlib

        import job_scout.config as config_module

        importlib.reload(config_module)

        # Create a fresh app and client
        app = create_app()
        client = TestClient(app)

        # API request with invalid header format should fail with 401
        response = client.get("/api/users", headers={"Authorization": "Invalid"})
        assert response.status_code == 401

        response = client.get("/api/users", headers={"Authorization": "Basic token"})
        assert response.status_code == 401

    def test_token_configured_static_files_still_accessible(
        self, temp_data_dir: Path, monkeypatch
    ) -> None:
        """Test that static files remain accessible when token is configured."""
        from job_scout.config import update_secrets, write_global_config
        from job_scout.web.app import create_app

        # Configure a token
        write_global_config({})
        update_secrets({"dashboard_token": "test-token-123"})

        # Reload the secrets module to pick up the new token
        import importlib

        import job_scout.config as config_module

        importlib.reload(config_module)

        # Create a fresh app and client
        app = create_app()
        client = TestClient(app)

        # Static files should still be accessible without token
        response = client.get("/")
        assert response.status_code == 200

        response = client.get("/app.js")
        assert response.status_code == 200

        response = client.get("/style.css")
        assert response.status_code == 200


class TestJobStatusUpdate:
    """Tests for POST /api/jobs/{job_id}/status endpoint."""

    def test_update_job_status_missing_user(self, client: TestClient) -> None:
        """Updating job status without user parameter fails."""
        response = client.post(
            "/api/jobs/1/status",
            json={"status": "viewed"},
        )
        assert response.status_code == 400
        assert "user is required" in response.json()["detail"]

    def test_update_job_status_invalid_user(self, client: TestClient) -> None:
        """Updating job status for nonexistent user fails."""
        response = client.post(
            "/api/jobs/1/status",
            json={"status": "viewed", "user": "nonexistent"},
        )
        assert response.status_code == 404

    def test_update_job_status_missing_status(
        self, client: TestClient, test_user: str
    ) -> None:
        """Updating job status without status parameter fails."""
        response = client.post(
            "/api/jobs/1/status",
            json={"user": test_user},
        )
        assert response.status_code == 400
        assert "status is required" in response.json()["detail"]

    def test_update_job_status_invalid_status(
        self, client: TestClient, test_user: str
    ) -> None:
        """Updating job status with invalid status value fails."""
        response = client.post(
            "/api/jobs/1/status",
            json={"status": "invalid_status", "user": test_user},
        )
        assert response.status_code == 400
        assert "Invalid status" in response.json()["detail"]

    def test_update_job_status_nonexistent_job(
        self, client: TestClient, test_user: str
    ) -> None:
        """Updating status of nonexistent job fails."""
        response = client.post(
            "/api/jobs/9999/status",
            json={"status": "viewed", "user": test_user},
        )
        assert response.status_code == 400
        assert "Invalid status transition or job not found" in response.json()["detail"]

    def test_update_job_status_valid_transition(
        self, client: TestClient, test_user: str, sample_job: JobListing
    ) -> None:
        """Updating job status with valid transition succeeds."""
        from job_scout.config import user_db_path
        from job_scout.database import Database

        # Create a job
        db = Database(user_db_path(test_user))
        job = sample_job.model_copy(update={"status": JobStatus.NEW})
        job_id = db.save_job(job)

        # Update status
        response = client.post(
            f"/api/jobs/{job_id}/status",
            json={"status": "viewed", "user": test_user},
        )
        assert response.status_code == 200
        assert "status updated" in response.json()["message"].lower()

        # Verify in database
        jobs = db.get_jobs_by_status(JobStatus.VIEWED)
        assert len(jobs) == 1
        assert jobs[0].id == job_id

    def test_update_job_status_with_notes(
        self, client: TestClient, test_user: str, sample_job: JobListing
    ) -> None:
        """Updating job status with notes saves the notes."""
        from job_scout.config import user_db_path
        from job_scout.database import Database

        # Create a job
        db = Database(user_db_path(test_user))
        job = sample_job.model_copy(update={"status": JobStatus.NEW})
        job_id = db.save_job(job)

        # Update status with notes
        response = client.post(
            f"/api/jobs/{job_id}/status",
            json={
                "status": "viewed",
                "notes": "Very interesting opportunity",
                "user": test_user,
            },
        )
        assert response.status_code == 200

        # Verify notes are saved
        jobs = db.get_jobs_by_status(JobStatus.VIEWED)
        assert len(jobs) == 1
        assert jobs[0].notes == "Very interesting opportunity"


class TestDetectLocalModels:
    """Tests for POST /api/llm/detect-models endpoint."""

    def test_detect_models_missing_base_url(self, client: TestClient) -> None:
        """detect_models fails when base_url is missing."""
        response = client.post("/api/llm/detect-models", json={})
        assert response.status_code == 200
        result = response.json()
        assert not result["ok"]
        assert "base_url is required" in result["message"]

    def test_detect_models_empty_base_url(self, client: TestClient) -> None:
        """detect_models fails when base_url is empty."""
        response = client.post("/api/llm/detect-models", json={"base_url": ""})
        assert response.status_code == 200
        result = response.json()
        assert not result["ok"]
        assert "base_url is required" in result["message"]

    def test_detect_models_connection_error(self, client: TestClient) -> None:
        """detect_models handles connection errors gracefully."""
        response = client.post(
            "/api/llm/detect-models", json={"base_url": "http://invalid.invalid"}
        )
        assert response.status_code == 200
        result = response.json()
        assert not result["ok"]
        assert "Connection failed" in result["message"] or "Error" in result["message"]
        assert result["models"] == []

    def test_detect_models_success_mock(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """detect_models returns model list on success."""
        from unittest.mock import MagicMock

        # Mock the openai.OpenAI client
        mock_model_1 = MagicMock()
        mock_model_1.id = "llama3.1"
        mock_model_2 = MagicMock()
        mock_model_2.id = "llama2"

        mock_models_list = MagicMock()
        mock_models_list.data = [mock_model_1, mock_model_2]

        mock_client = MagicMock()
        mock_client.models.list.return_value = mock_models_list

        def mock_openai_init(*args, **kwargs) -> MagicMock:
            return mock_client

        import openai

        monkeypatch.setattr(openai, "OpenAI", mock_openai_init)

        response = client.post(
            "/api/llm/detect-models",
            json={"base_url": "http://localhost:11434/v1"},
        )
        assert response.status_code == 200
        result = response.json()
        assert result["ok"]
        assert result["models"] == ["llama3.1", "llama2"]
        assert "Found 2" in result["message"]

    def test_detect_models_with_api_key(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """detect_models passes api_key to OpenAI client."""
        from unittest.mock import MagicMock

        mock_model = MagicMock()
        mock_model.id = "test-model"
        mock_models_list = MagicMock()
        mock_models_list.data = [mock_model]

        mock_client = MagicMock()
        mock_client.models.list.return_value = mock_models_list

        mock_init = MagicMock(return_value=mock_client)

        import openai

        monkeypatch.setattr(openai, "OpenAI", mock_init)

        response = client.post(
            "/api/llm/detect-models",
            json={"base_url": "http://localhost:11434/v1", "api_key": "test-key"},
        )
        assert response.status_code == 200
        result = response.json()
        assert result["ok"]

        # Verify that the api_key was passed to the OpenAI constructor
        mock_init.assert_called_once()
        call_kwargs = mock_init.call_args[1]
        assert call_kwargs["api_key"] == "test-key"
        assert call_kwargs["base_url"] == "http://localhost:11434/v1"
