"""Tests for cover letter and screening question generation."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from job_scout.cover_letter_generator import (
    _format_cv_profile_summary,
    _parse_json_response,
    answer_screening_questions,
    extract_screening_questions,
    generate_cover_letter,
)
from job_scout.models import CvProfile, CvRole


class TestExtractScreeningQuestions:
    """Test screening question extraction from job descriptions."""

    def test_extract_questions_with_mock_client(self) -> None:
        """Test screening question extraction with mocked LLM client."""
        mock_client = MagicMock()
        mock_client.complete.return_value = (
            '{"questions": '
            '["What is your experience with Python?", '
            '"Can you describe your leadership experience?"]}'
        )

        questions = extract_screening_questions(
            "Looking for Python developer with leadership skills",
            client=mock_client,
        )

        assert len(questions) == 2
        assert "What is your experience with Python?" in questions
        mock_client.complete.assert_called_once()

    def test_extract_questions_empty_response(self) -> None:
        """Test handling of empty questions response."""
        mock_client = MagicMock()
        mock_client.complete.return_value = '{"questions": []}'

        questions = extract_screening_questions(
            "Some job description",
            client=mock_client,
        )

        assert questions == []

    def test_extract_questions_with_markdown_fences(self) -> None:
        """Test parsing questions from response with markdown code fences."""
        mock_client = MagicMock()
        mock_client.complete.return_value = (
            'Some text\n```json\n{"questions": ["Question 1", "Question 2"]}\n```'
        )

        questions = extract_screening_questions("Job desc", client=mock_client)

        assert questions == ["Question 1", "Question 2"]


class TestGenerateCoverLetter:
    """Test cover letter generation functionality."""

    def test_generate_cover_letter_with_profile(self) -> None:
        """Test cover letter generation with a CvProfile."""
        mock_client = MagicMock()
        mock_client.complete.return_value = "Dear Hiring Manager,\n\nI am writing..."

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

        cover_letter = generate_cover_letter(
            profile,
            "Looking for Python developer",
            "Senior Developer",
            "Acme Inc",
            client=mock_client,
        )

        assert "Dear Hiring Manager" in cover_letter
        mock_client.complete.assert_called_once()

    def test_generate_cover_letter_empty_profile(self) -> None:
        """Test cover letter generation with empty profile."""
        mock_client = MagicMock()
        mock_client.complete.return_value = "Standard cover letter text"

        profile = CvProfile()

        cover_letter = generate_cover_letter(
            profile,
            "Job description",
            "Developer",
            "Company",
            client=mock_client,
        )

        assert "Standard cover letter" in cover_letter
        mock_client.complete.assert_called_once()

    def test_generate_cover_letter_client_error(self) -> None:
        """Test cover letter generation when LLM client raises exception."""
        mock_client = MagicMock()
        mock_client.complete.side_effect = RuntimeError("LLM unavailable")

        profile = CvProfile(skills=["Python"])

        cover_letter = generate_cover_letter(
            profile,
            "Job description",
            "Developer",
            "Company",
            client=mock_client,
        )

        assert cover_letter == ""


class TestAnswerScreeningQuestions:
    """Test screening question answering functionality."""

    def test_answer_questions_with_profile(self) -> None:
        """Test answering screening questions with a CvProfile."""
        mock_client = MagicMock()
        q = "What is your Python experience?"
        a = "I have 5 years of Python experience..."
        mock_client.complete.return_value = f'{{"answers": {{"{q}": "{a}"}}}}'

        profile = CvProfile(
            skills=["Python", "JavaScript"],
            years_experience=5,
            education=["BS Computer Science"],
        )
        questions = ["What is your Python experience?"]

        answers = answer_screening_questions(
            questions,
            profile,
            "Job description",
            client=mock_client,
        )

        assert "What is your Python experience?" in answers
        assert "5 years" in answers["What is your Python experience?"]

    def test_answer_questions_empty_list(self) -> None:
        """Test that empty question list returns empty answers."""
        mock_client = MagicMock()
        profile = CvProfile(skills=["Python"])

        answers = answer_screening_questions(
            [],
            profile,
            "Job description",
            client=mock_client,
        )

        assert answers == {}
        mock_client.complete.assert_not_called()

    def test_answer_questions_multiple_questions(self) -> None:
        """Test answering multiple screening questions."""
        mock_client = MagicMock()
        mock_client.complete.return_value = (
            '{"answers": {"Question 1?": "Answer 1", "Question 2?": "Answer 2"}}'
        )

        profile = CvProfile(skills=["Skill1", "Skill2"])
        questions = ["Question 1?", "Question 2?"]

        answers = answer_screening_questions(
            questions,
            profile,
            "Job description",
            client=mock_client,
        )

        assert len(answers) == 2
        assert answers["Question 1?"] == "Answer 1"
        assert answers["Question 2?"] == "Answer 2"

    def test_answer_questions_client_error(self) -> None:
        """Test question answering when LLM client raises exception."""
        mock_client = MagicMock()
        mock_client.complete.side_effect = RuntimeError("LLM unavailable")

        profile = CvProfile(skills=["Python"])
        questions = ["What is your experience?"]

        answers = answer_screening_questions(
            questions,
            profile,
            "Job description",
            client=mock_client,
        )

        assert answers == {}


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
        assert "TechCorp" in summary

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

    def test_format_with_roles_without_dates(self) -> None:
        """Test formatting with roles that have no dates."""
        profile = CvProfile(
            skills=["Python"],
            past_roles=[
                CvRole(
                    title="Developer",
                    company="SomeCorp",
                    start_date=None,
                    end_date=None,
                )
            ],
        )

        summary = _format_cv_profile_summary(profile)

        assert "Developer" in summary
        assert "SomeCorp" in summary


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

    def test_parse_complex_json_structure(self) -> None:
        """Test parsing complex JSON structures."""
        response = (
            "Analysis:\n```json\n"
            '{"questions": ["Q1", "Q2"], "metadata": {"count": 2}}\n```'
        )

        result = _parse_json_response(response)

        assert result["questions"] == ["Q1", "Q2"]
        assert result["metadata"]["count"] == 2
