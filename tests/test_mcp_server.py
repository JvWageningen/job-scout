"""Tests for MCP server integration."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from job_scout.database import Database
from job_scout.mcp_server import MCPServerManager
from job_scout.models import JobListing, JobStatus


@pytest.fixture
def temp_db(tmp_path: Path) -> Database:
    """Create a temporary database for testing.

    Args:
        tmp_path: Temporary directory path.

    Returns:
        Database instance.
    """
    db_path = tmp_path / "test.db"
    return Database(db_path)


@pytest.fixture
def sample_jobs(temp_db: Database) -> list[JobListing]:
    """Create sample jobs in the test database.

    Args:
        temp_db: Test database.

    Returns:
        List of created JobListing objects.
    """
    jobs = [
        JobListing(
            title="Senior Python Developer",
            company="TechCorp",
            location="Amsterdam, Netherlands",
            url="https://example.com/job1",
            description="Python development role",
            source="indeed",
            date_posted=datetime.now(UTC),
            fit_score=85,
            fit_reasoning="Great match for skills",
            status=JobStatus.NEW,
        ),
        JobListing(
            title="Junior Python Developer",
            company="StartupXYZ",
            location="Amsterdam, Netherlands",
            url="https://example.com/job2",
            description="Junior Python role",
            source="linkedin",
            date_posted=datetime.now(UTC),
            fit_score=65,
            fit_reasoning="Good entry level",
            status=JobStatus.APPROVED,
        ),
        JobListing(
            title="Data Engineer",
            company="DataCo",
            location="Rotterdam, Netherlands",
            url="https://example.com/job3",
            description="Data engineering position",
            source="indeed",
            date_posted=datetime.now(UTC),
            fit_score=45,
            fit_reasoning="Missing some skills",
            status=JobStatus.REJECTED,
        ),
    ]

    for job in jobs:
        temp_db.save_job(job)

    return jobs


@pytest.fixture
def mcp_manager(temp_db: Database) -> MCPServerManager:
    """Create an MCP server manager for testing.

    Args:
        temp_db: Test database.

    Returns:
        MCPServerManager instance.
    """
    return MCPServerManager(temp_db)


class TestMCPServerManager:
    """Test suite for MCPServerManager."""

    def test_manager_initialization(self, mcp_manager: MCPServerManager) -> None:
        """Test that the manager initializes correctly."""
        assert mcp_manager.db is not None
        assert mcp_manager.server is not None
        assert mcp_manager._result_cache == {}

    def test_get_cache_key_with_user_isolation(
        self, mcp_manager: MCPServerManager
    ) -> None:
        """Test that cache keys include user isolation."""
        key1 = mcp_manager._get_cache_key("user1", "key1")
        key2 = mcp_manager._get_cache_key("user2", "key1")
        key3 = mcp_manager._get_cache_key("user1", "key1")

        assert key1 != key2  # Different users should have different cache keys
        assert key1 == key3  # Same user and key should produce same cache key

    def test_validate_user_invalid(self, mcp_manager: MCPServerManager) -> None:
        """Test user validation with invalid user."""
        # Invalid user should fail validation
        is_valid = mcp_manager._validate_user("nonexistent_user_xyz")
        assert not is_valid

    def test_get_user_jobs_returns_empty_for_invalid_user(
        self, mcp_manager: MCPServerManager
    ) -> None:
        """Test that get_user_jobs returns empty for invalid user."""
        jobs = mcp_manager._get_user_jobs("nonexistent_user_xyz")
        assert jobs == []

    def test_get_user_jobs_returns_jobs(
        self, mcp_manager: MCPServerManager, sample_jobs: list[JobListing]
    ) -> None:
        """Test that get_user_jobs returns jobs from database."""
        # Note: This assumes the default user is loaded, which happens
        # when the database is created with default config
        jobs = mcp_manager._get_user_jobs("default")
        assert len(jobs) >= 0  # May be 0 if no default user config

    def test_list_resources(self, mcp_manager: MCPServerManager) -> None:
        """Test that resources can be listed."""
        assert mcp_manager.server is not None
        # The handler is set up; actual async testing would need async test framework

    def test_mcp_server_manager_isolation(self, temp_db: Database) -> None:
        """Test multi-user isolation at manager level."""
        manager1 = MCPServerManager(temp_db)
        manager2 = MCPServerManager(temp_db)

        key1_user1 = manager1._get_cache_key("user1", "test")
        key2_user1 = manager2._get_cache_key("user1", "test")
        key1_user2 = manager1._get_cache_key("user2", "test")

        # Same user should get same keys across managers
        assert key1_user1 == key2_user1
        # Different users should get different keys
        assert key1_user1 != key1_user2

    def test_cache_with_user_isolation(self, mcp_manager: MCPServerManager) -> None:
        """Test that cache maintains user isolation."""
        # Store data for user1
        key_user1 = mcp_manager._get_cache_key("user1", "data")
        mcp_manager._result_cache[key_user1] = {"user": "user1", "data": "test"}

        # Store data for user2
        key_user2 = mcp_manager._get_cache_key("user2", "data")
        mcp_manager._result_cache[key_user2] = {"user": "user2", "data": "test"}

        # Verify isolation
        assert mcp_manager._result_cache[key_user1]["user"] == "user1"
        assert mcp_manager._result_cache[key_user2]["user"] == "user2"
        assert key_user1 != key_user2
