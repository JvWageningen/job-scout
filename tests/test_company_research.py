"""Tests for company research and hiring manager discovery."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from job_scout.company_research import (
    _build_research_prompt,
    _extract_json,
    _suggest_hiring_managers,
    research_company,
)
from job_scout.models import CompanyResearch, Config, JobListing


@pytest.fixture
def sample_job() -> JobListing:
    """Create a sample job listing for testing."""
    return JobListing(
        title="Senior Software Engineer",
        company="TechCorp",
        location="Amsterdam",
        url="https://example.com/job/123",
        source="indeed",
        description="""
        We're looking for a Senior Software Engineer to join our
        fast-growing team. You'll work with Python, Go, and
        Kubernetes in a modern DevOps culture. We offer
        competitive salary, flexible work arrangements, and
        growth opportunities.
        """,
    )


@pytest.fixture
def sample_config() -> Config:
    """Create a sample config for testing."""
    return Config(
        name="test_user",
        llm_provider="claude_cli",
        profile_description="Experienced software engineer",
    )


class TestExtractJson:
    """Tests for JSON extraction from LLM output."""

    def test_extract_json_with_fences(self) -> None:
        """Test extracting JSON with markdown code fences."""
        text = """
        Some preamble text
        ```json
        {"key": "value"}
        ```
        Some trailing text
        """
        result = _extract_json(text)
        assert result == {"key": "value"}

    def test_extract_json_without_fences(self) -> None:
        """Test extracting JSON without fences."""
        text = '{"key": "value"}'
        result = _extract_json(text)
        assert result == {"key": "value"}

    def test_extract_json_with_preamble(self) -> None:
        """Test extracting JSON with text preamble."""
        text = 'Some text before {"key": "value"} and after'
        result = _extract_json(text)
        assert result == {"key": "value"}

    def test_extract_json_invalid(self) -> None:
        """Test that invalid JSON raises error."""
        with pytest.raises(json.JSONDecodeError):
            _extract_json("not valid json")


class TestBuildResearchPrompt:
    """Tests for research prompt building."""

    def test_prompt_includes_company_name(self, sample_job: JobListing) -> None:
        """Test that prompt includes company name."""
        prompt = _build_research_prompt(sample_job)
        assert sample_job.company in prompt

    def test_prompt_includes_job_title(self, sample_job: JobListing) -> None:
        """Test that prompt includes job title."""
        prompt = _build_research_prompt(sample_job)
        assert sample_job.title in prompt

    def test_prompt_requests_json(self, sample_job: JobListing) -> None:
        """Test that prompt explicitly requests JSON format."""
        prompt = _build_research_prompt(sample_job)
        assert "JSON" in prompt
        assert "industry" in prompt
        assert "company_size" in prompt


class TestSuggestHiringManagers:
    """Tests for hiring manager suggestion."""

    @patch("job_scout.company_research.get_llm_client")
    def test_suggest_hiring_managers_success(self, mock_get_client: MagicMock) -> None:
        """Test successful hiring manager suggestions."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        response = """
        [
          {
            "name": "John Smith",
            "role": "Engineering Manager",
            "email": "john@techcorp.com",
            "linkedin_url": "https://linkedin.com/in/johnsmith",
            "confidence": 85,
            "reasoning": "Engineering role suggests tech team management"
          }
        ]
        """
        mock_client.complete.return_value = response

        sample_job = JobListing(
            title="Senior Software Engineer",
            company="TechCorp",
            url="https://example.com/job",
            source="indeed",
        )
        research_data = {"industry": "Tech", "company_size": "medium"}
        config = Config(name="test_user")

        suggestions = _suggest_hiring_managers(
            sample_job, research_data, config, mock_client
        )

        assert len(suggestions) == 1
        assert suggestions[0].name == "John Smith"
        assert suggestions[0].confidence == 85

    @patch("job_scout.company_research.get_llm_client")
    def test_suggest_hiring_managers_invalid_response(
        self, mock_get_client: MagicMock
    ) -> None:
        """Test handling of invalid response from LLM."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.complete.side_effect = Exception("LLM error")

        sample_job = JobListing(
            title="Senior Software Engineer",
            company="TechCorp",
            url="https://example.com/job",
            source="indeed",
        )
        research_data = {"industry": "Tech"}
        config = Config(name="test_user")

        suggestions = _suggest_hiring_managers(
            sample_job, research_data, config, mock_client
        )

        assert suggestions == []


class TestResearchCompany:
    """Tests for company research."""

    @patch("job_scout.company_research.get_llm_client")
    def test_research_company_success(
        self, mock_get_client: MagicMock, sample_job: JobListing, sample_config: Config
    ) -> None:
        """Test successful company research."""
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        research_response = """
        {
          "industry": "Software/Tech",
          "company_size": "medium",
          "culture_indicators": ["innovation", "fast-paced", "collaborative"],
          "tech_stack_hints": ["Python", "Kubernetes", "Go"],
          "growth_signals": "Series B funding round, expanding team",
          "research_notes": "Growing tech company with DevOps focus"
        }
        """

        manager_response = """
        [
          {
            "name": "Jane Doe",
            "role": "Head of Engineering",
            "email": "jane@techcorp.com",
            "linkedin_url": null,
            "confidence": 75,
            "reasoning": "Senior engineering role fits well"
          }
        ]
        """

        # First call for research, second for hiring managers
        mock_client.complete.side_effect = [research_response, manager_response]

        result = research_company(sample_job, sample_config)

        assert result is not None
        assert result.company_name == "TechCorp"
        assert result.industry == "Software/Tech"
        assert result.company_size == "medium"
        assert "innovation" in result.culture_indicators
        assert "Python" in result.tech_stack_hints
        assert result.hiring_managers

    @patch("job_scout.company_research.get_llm_client")
    def test_research_company_failure(
        self, mock_get_client: MagicMock, sample_job: JobListing, sample_config: Config
    ) -> None:
        """Test handling of research failure."""
        mock_get_client.side_effect = Exception("LLM unavailable")

        result = research_company(sample_job, sample_config)

        assert result is None


class TestCompanyResearchModel:
    """Tests for CompanyResearch model."""

    def test_company_research_creation(self) -> None:
        """Test creating a CompanyResearch object."""
        research = CompanyResearch(
            company_name="TechCorp",
            industry="Software",
            company_size="medium",
            culture_indicators=["innovation", "collaborative"],
            tech_stack_hints=["Python", "Kubernetes"],
            growth_signals="Series B funded",
            research_notes="Growing company",
        )

        assert research.company_name == "TechCorp"
        assert research.industry == "Software"
        assert len(research.culture_indicators) == 2
        assert research.research_timestamp is None

    def test_company_research_with_timestamp(self) -> None:
        """Test CompanyResearch with timestamp."""
        now = datetime.now(UTC)
        research = CompanyResearch(
            company_name="TechCorp",
            research_timestamp=now,
        )

        assert research.research_timestamp == now

    def test_company_research_serialization(self) -> None:
        """Test serialization of CompanyResearch."""
        research = CompanyResearch(
            company_name="TechCorp",
            industry="Tech",
        )

        data = research.model_dump()
        assert data["company_name"] == "TechCorp"
        assert data["industry"] == "Tech"

        # Should be able to reconstruct
        reconstructed = CompanyResearch.model_validate(data)
        assert reconstructed.company_name == "TechCorp"
