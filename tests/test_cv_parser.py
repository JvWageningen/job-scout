"""Tests for CV PDF parsing utilities."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from job_scout.cv_parser import parse_cv


def test_parse_cv_missing_file_raises(tmp_path: Path) -> None:
    """parse_cv raises FileNotFoundError for a non-existent path."""
    with pytest.raises(FileNotFoundError):
        parse_cv(tmp_path / "no_such.pdf")


def test_parse_cv_error_message_contains_init_hint(tmp_path: Path) -> None:
    """FileNotFoundError message mentions 'job-scout init'."""
    with pytest.raises(FileNotFoundError, match="job-scout init"):
        parse_cv(tmp_path / "missing.pdf")


def test_parse_cv_returns_extracted_text(tmp_path: Path) -> None:
    """parse_cv returns text extracted from a readable PDF."""
    pdf_file = tmp_path / "cv.pdf"
    pdf_file.write_bytes(b"fake pdf content")

    mock_page = MagicMock()
    mock_page.extract_text.return_value = "Hello world"
    mock_reader = MagicMock()
    mock_reader.pages = [mock_page]

    with patch("PyPDF2.PdfReader", return_value=mock_reader):
        result = parse_cv(pdf_file)

    assert "Hello world" in result


def test_parse_cv_accepts_string_path(tmp_path: Path) -> None:
    """parse_cv accepts a string path in addition to Path objects."""
    pdf_file = tmp_path / "cv.pdf"
    pdf_file.write_bytes(b"fake pdf content")

    mock_page = MagicMock()
    mock_page.extract_text.return_value = "CV content"
    mock_reader = MagicMock()
    mock_reader.pages = [mock_page]

    with patch("PyPDF2.PdfReader", return_value=mock_reader):
        result = parse_cv(str(pdf_file))

    assert result == "CV content"


def test_parse_cv_corrupt_file_returns_empty_string(tmp_path: Path) -> None:
    """parse_cv returns empty string when PDF parsing raises an exception."""
    bad_pdf = tmp_path / "bad.pdf"
    bad_pdf.write_bytes(b"not a pdf at all")

    with patch("PyPDF2.PdfReader", side_effect=Exception("corrupt")):
        result = parse_cv(bad_pdf)

    assert result == ""


def test_parse_cv_skips_pages_with_no_text(tmp_path: Path) -> None:
    """parse_cv ignores pages where extract_text() returns None/falsy."""
    pdf_file = tmp_path / "cv.pdf"
    pdf_file.write_bytes(b"fake pdf content")

    empty_page = MagicMock()
    empty_page.extract_text.return_value = None
    text_page = MagicMock()
    text_page.extract_text.return_value = "Real text"
    mock_reader = MagicMock()
    mock_reader.pages = [empty_page, text_page]

    with patch("PyPDF2.PdfReader", return_value=mock_reader):
        result = parse_cv(pdf_file)

    assert result == "Real text"


def test_parse_cv_multiple_pages_joined(tmp_path: Path) -> None:
    """parse_cv joins text from multiple pages with newlines."""
    pdf_file = tmp_path / "cv.pdf"
    pdf_file.write_bytes(b"fake pdf content")

    pages = [MagicMock() for _ in range(3)]
    for i, page in enumerate(pages):
        page.extract_text.return_value = f"Page {i}"
    mock_reader = MagicMock()
    mock_reader.pages = pages

    with patch("PyPDF2.PdfReader", return_value=mock_reader):
        result = parse_cv(pdf_file)

    assert "Page 0" in result
    assert "Page 1" in result
    assert "Page 2" in result


def test_parse_cv_structured_extracts_profile(tmp_path: Path) -> None:
    """parse_cv_structured sends CV to LLM and returns CvProfile."""
    from unittest.mock import MagicMock

    from job_scout.cv_parser import parse_cv_structured
    from job_scout.models import CvProfile

    cv_text = "10 years of Python programming..."
    client = MagicMock()
    client.complete.return_value = """
    ```json
    {
        "skills": ["Python", "SQL", "Docker"],
        "years_experience": 10,
        "education": ["BSc Computer Science"],
        "past_roles": ["Senior Engineer at TechCorp"]
    }
    ```
    """

    profile = parse_cv_structured(cv_text, client)

    assert isinstance(profile, CvProfile)
    assert profile.skills == ["Python", "SQL", "Docker"]
    assert profile.years_experience == 10
    assert profile.education == ["BSc Computer Science"]
    assert profile.past_roles == ["Senior Engineer at TechCorp"]
    client.complete.assert_called_once()
    assert client.complete.call_args[1]["purpose"] == "cv_parsing"


def test_parse_cv_structured_handles_missing_fields() -> None:
    """parse_cv_structured returns empty arrays for missing optional fields."""
    from unittest.mock import MagicMock

    from job_scout.cv_parser import parse_cv_structured
    from job_scout.models import CvProfile

    client = MagicMock()
    client.complete.return_value = '{"skills": []}'

    profile = parse_cv_structured("some cv text", client)

    assert isinstance(profile, CvProfile)
    assert profile.skills == []
    assert profile.years_experience is None
    assert profile.education == []
    assert profile.past_roles == []


def test_compute_cv_hash() -> None:
    """compute_cv_hash returns consistent SHA256 hash."""
    from job_scout.cv_parser import compute_cv_hash

    text1 = "CV content"
    text2 = "CV content"
    text3 = "Different content"

    hash1 = compute_cv_hash(text1)
    hash2 = compute_cv_hash(text2)
    hash3 = compute_cv_hash(text3)

    assert hash1 == hash2
    assert hash1 != hash3
    assert len(hash1) == 64  # SHA256 hex is 64 chars
