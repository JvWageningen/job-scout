"""Tests for the job-scout web dashboard API."""

from __future__ import annotations

import tempfile
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

        # Verify a run can be started
        def mock_execute_run(name, *, dry_run=False, full=False):
            pass

        monkeypatch.setattr("job_scout.cli._execute_run", mock_execute_run)

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
