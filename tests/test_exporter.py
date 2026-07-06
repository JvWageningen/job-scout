"""Tests for job export functionality."""

from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime

import pytest

from job_scout.exporter import JobExporter
from job_scout.models import JobListing


@pytest.fixture
def sample_jobs() -> list[JobListing]:
    """Create sample job listings for testing."""
    return [
        JobListing(
            id=1,
            title="Python Developer",
            company="TechCorp",
            location="Amsterdam",
            url="https://example.com/job1",
            source="indeed",
            date_posted=datetime(2026, 7, 1, tzinfo=UTC),
            fit_score=85,
            fit_reasoning="Good skill match",
            salary_min=50000,
            salary_max=70000,
            salary_period="year",
            vacation_days=25,
            compensation_reasoning="Market rate",
            distance_km=5.0,
        ),
        JobListing(
            id=2,
            title="Data Analyst",
            company="DataInc",
            location="Rotterdam",
            url="https://example.com/job2",
            source="linkedin",
            date_posted=datetime(2026, 7, 2, tzinfo=UTC),
            fit_score=75,
            fit_reasoning="Partial match",
            salary_min=45000,
            salary_max=60000,
            salary_period="year",
            vacation_days=23,
        ),
        JobListing(
            id=3,
            title="Web Designer",
            company="CreativeStudio",
            location="Utrecht",
            url="https://example.com/job3",
            source="custom",
            fit_score=None,
        ),
    ]


class TestJobExporterCSV:
    """Tests for CSV export functionality."""

    def test_export_to_csv_basic(self, sample_jobs: list[JobListing]) -> None:
        """Test basic CSV export."""
        csv_output = JobExporter.to_csv(sample_jobs)
        assert csv_output
        assert "Python Developer" in csv_output
        assert "TechCorp" in csv_output

    def test_export_to_csv_format(self, sample_jobs: list[JobListing]) -> None:
        """Test that CSV has correct structure."""
        csv_output = JobExporter.to_csv(sample_jobs)
        reader = csv.DictReader(io.StringIO(csv_output))
        rows = list(reader)
        assert len(rows) == 3
        assert rows[0]["title"] == "Python Developer"
        assert rows[0]["company"] == "TechCorp"
        assert rows[0]["fit_score"] == "85"

    def test_export_to_csv_empty_list(self) -> None:
        """Test CSV export with empty list."""
        csv_output = JobExporter.to_csv([])
        assert csv_output == ""

    def test_export_to_csv_missing_fields(self, sample_jobs: list[JobListing]) -> None:
        """Test CSV export handles missing optional fields."""
        csv_output = JobExporter.to_csv(sample_jobs)
        reader = csv.DictReader(io.StringIO(csv_output))
        rows = list(reader)
        # Third job has no fit_score
        assert rows[2]["fit_score"] == ""
        # All jobs have URL
        assert all(row["url"] for row in rows)

    def test_export_to_csv_datetime_formatting(
        self, sample_jobs: list[JobListing]
    ) -> None:
        """Test that datetime fields are properly formatted."""
        csv_output = JobExporter.to_csv(sample_jobs)
        reader = csv.DictReader(io.StringIO(csv_output))
        rows = list(reader)
        # Should be ISO format
        assert "2026-07-01" in rows[0]["date_posted"]
        assert "2026-07-02" in rows[1]["date_posted"]


class TestJobExporterJSON:
    """Tests for JSON export functionality."""

    def test_export_to_json_basic(self, sample_jobs: list[JobListing]) -> None:
        """Test basic JSON export."""
        json_output = JobExporter.to_json(sample_jobs)
        assert json_output
        data = json.loads(json_output)
        assert len(data) == 3
        assert data[0]["title"] == "Python Developer"

    def test_export_to_json_structure(self, sample_jobs: list[JobListing]) -> None:
        """Test JSON has correct structure."""
        json_output = JobExporter.to_json(sample_jobs)
        data = json.loads(json_output)
        assert isinstance(data, list)
        assert all(isinstance(job, dict) for job in data)

    def test_export_to_json_empty_list(self) -> None:
        """Test JSON export with empty list."""
        json_output = JobExporter.to_json([])
        data = json.loads(json_output)
        assert data == []

    def test_export_to_json_datetime_formatting(
        self, sample_jobs: list[JobListing]
    ) -> None:
        """Test that datetime fields are ISO formatted in JSON."""
        json_output = JobExporter.to_json(sample_jobs)
        data = json.loads(json_output)
        # Dates should be ISO format strings
        assert isinstance(data[0]["date_posted"], str)
        assert "2026-07-01" in data[0]["date_posted"]

    def test_export_to_json_preserves_all_fields(
        self, sample_jobs: list[JobListing]
    ) -> None:
        """Test that JSON preserves all job fields."""
        json_output = JobExporter.to_json(sample_jobs)
        data = json.loads(json_output)
        job = data[0]
        assert job["id"] == 1
        assert job["title"] == "Python Developer"
        assert job["company"] == "TechCorp"
        assert job["salary_min"] == 50000
        assert job["salary_max"] == 70000


class TestJobExporterFactory:
    """Tests for the export method factory."""

    def test_export_csv_format(self, sample_jobs: list[JobListing]) -> None:
        """Test export with CSV format."""
        output = JobExporter.export(sample_jobs, format="csv")
        assert isinstance(output, str)
        assert "Python Developer" in output

    def test_export_json_format(self, sample_jobs: list[JobListing]) -> None:
        """Test export with JSON format."""
        output = JobExporter.export(sample_jobs, format="json")
        data = json.loads(output)
        assert len(data) == 3

    def test_export_unsupported_format(self, sample_jobs: list[JobListing]) -> None:
        """Test that unsupported format raises ValueError."""
        with pytest.raises(ValueError):
            JobExporter.export(sample_jobs, format="xml")

    def test_export_default_is_json(self, sample_jobs: list[JobListing]) -> None:
        """Test that default format is JSON."""
        output = JobExporter.export(sample_jobs)
        data = json.loads(output)
        assert len(data) == 3
