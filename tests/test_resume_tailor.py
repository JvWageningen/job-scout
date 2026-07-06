"""Tests for resume tailoring and PDF generation."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from job_scout.models import CvProfile, CvRole
from job_scout.resume_tailor import (
    _format_cv_profile_summary,
    _parse_json_response,
    extract_resume_keywords,
    generate_resume_pdf,
    tailor_resume_text,
)


class TestExtractResumeKeywords:
    """Test keyword extraction from job descriptions."""

    def test_extract_keywords_with_mock_client(self) -> None:
        """Test keyword extraction with mocked LLM client."""
        mock_client = MagicMock()
        mock_client.complete.return_value = (
            '{"keywords": ["Python", "FastAPI", "REST API"]}'
        )

        keywords = extract_resume_keywords(
            "Looking for Python developer with FastAPI experience",
            client=mock_client,
        )

        assert keywords == ["Python", "FastAPI", "REST API"]
        mock_client.complete.assert_called_once()

    def test_extract_keywords_empty_response(self) -> None:
        """Test handling of empty keywords response."""
        mock_client = MagicMock()
        mock_client.complete.return_value = '{"keywords": []}'

        keywords = extract_resume_keywords("Some job description", client=mock_client)

        assert keywords == []

    def test_extract_keywords_with_markdown_fences(self) -> None:
        """Test parsing keywords from response with markdown code fences."""
        mock_client = MagicMock()
        mock_client.complete.return_value = (
            'Some text\n```json\n{"keywords": ["Skill1", "Skill2"]}\n```'
        )

        keywords = extract_resume_keywords("Job desc", client=mock_client)

        assert keywords == ["Skill1", "Skill2"]


class TestTailorResumeText:
    """Test resume tailoring functionality."""

    def test_tailor_resume_with_profile(self) -> None:
        """Test resume tailoring with a CvProfile."""
        mock_client = MagicMock()
        mock_client.complete.return_value = "Tailored resume content here..."

        cv_text = "Original CV content"
        profile = CvProfile(
            skills=["Python", "JavaScript"],
            years_experience=5,
            education=["BS Computer Science"],
            past_roles=[
                CvRole(
                    title="Senior Developer",
                    company="TechCorp",
                    start_date="2020",
                    end_date="2023",
                )
            ],
        )

        tailored = tailor_resume_text(
            cv_text,
            profile,
            "Looking for Python developer",
            keywords=["Python", "REST API"],
            client=mock_client,
        )

        assert "Tailored resume content" in tailored
        mock_client.complete.assert_called_once()

    def test_tailor_resume_extracts_keywords_if_not_provided(self) -> None:
        """Test that keywords are extracted if not provided."""
        mock_client = MagicMock()
        mock_client.complete.side_effect = [
            '{"keywords": ["Keyword1"]}',
            "Tailored content",
        ]

        cv_text = "Original CV"
        profile = CvProfile(skills=["Skill1"])

        tailored = tailor_resume_text(
            cv_text,
            profile,
            "Job description",
            client=mock_client,
        )

        assert "Tailored content" in tailored
        # Once for keywords, once for tailoring
        assert mock_client.complete.call_count == 2


class TestFormatCvProfileSummary:
    """Test CV profile formatting for LLM context."""

    def test_format_with_all_fields(self) -> None:
        """Test formatting profile with all fields populated."""
        profile = CvProfile(
            skills=["Python", "JavaScript"],
            years_experience=5,
            education=["BS Computer Science"],
            past_roles=[
                CvRole(
                    title="Developer",
                    company="TechCorp",
                    start_date="2020",
                    end_date="2023",
                )
            ],
        )

        summary = _format_cv_profile_summary(profile)

        assert "Python" in summary
        assert "5 years" in summary
        assert "BS Computer Science" in summary
        assert "Developer" in summary

    def test_format_with_empty_profile(self) -> None:
        """Test formatting empty profile."""
        profile = CvProfile()

        summary = _format_cv_profile_summary(profile)

        assert "(Profile data unavailable)" in summary

    def test_format_with_partial_fields(self) -> None:
        """Test formatting profile with only some fields."""
        profile = CvProfile(
            skills=["Python"],
            years_experience=3,
        )

        summary = _format_cv_profile_summary(profile)

        assert "Python" in summary
        assert "3 years" in summary
        assert "Education:" not in summary


class TestParseJsonResponse:
    """Test JSON parsing from LLM responses."""

    def test_parse_with_markdown_json_fence(self) -> None:
        """Test parsing JSON with markdown code fence."""
        response = 'Some text\n```json\n{"key": "value"}\n```'

        result = _parse_json_response(response)

        assert result == {"key": "value"}

    def test_parse_with_generic_code_fence(self) -> None:
        """Test parsing JSON with generic code fence."""
        response = 'Some text\n```\n{"key": "value"}\n```'

        result = _parse_json_response(response)

        assert result == {"key": "value"}

    def test_parse_with_braces_fallback(self) -> None:
        """Test parsing JSON using brace fallback."""
        response = 'Some preamble {"key": "value"} and some epilogue'

        result = _parse_json_response(response)

        assert result == {"key": "value"}

    def test_parse_invalid_json_raises(self) -> None:
        """Test that invalid JSON raises ValueError."""
        response = "No JSON here"

        with pytest.raises(ValueError, match="No JSON object found"):
            _parse_json_response(response)


class TestGenerateResumePdf:
    """Test PDF generation."""

    def test_generate_pdf_to_file(self) -> None:
        """Test generating PDF to a file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "resume.pdf"
            resume_text = (
                "This is a test resume\nWith multiple lines\n\nSection\nContent"
            )

            result = generate_resume_pdf(resume_text, output_path=output_path)

            assert result is None
            assert output_path.exists()
            assert output_path.stat().st_size > 0

    def test_generate_pdf_to_bytes(self) -> None:
        """Test generating PDF as bytes."""
        resume_text = "Test resume content\n\nWith sections"

        pdf_bytes = generate_resume_pdf(resume_text)

        assert pdf_bytes is not None
        assert isinstance(pdf_bytes, bytes)
        assert len(pdf_bytes) > 0
        assert pdf_bytes.startswith(b"%PDF")  # PDF magic number

    def test_generate_pdf_with_sections(self) -> None:
        """Test PDF generation with section headings."""
        resume_text = (
            "JOHN DOE\n\n"
            "EXPERIENCE\n"
            "Senior Developer at TechCorp\n"
            "Developed REST APIs\n\n"
            "SKILLS\n"
            "Python, JavaScript, SQL"
        )

        pdf_bytes = generate_resume_pdf(resume_text)

        assert pdf_bytes is not None
        assert len(pdf_bytes) > 0

    def test_generate_pdf_empty_content(self) -> None:
        """Test PDF generation with empty content."""
        pdf_bytes = generate_resume_pdf("")

        assert pdf_bytes is not None
        assert len(pdf_bytes) > 0
