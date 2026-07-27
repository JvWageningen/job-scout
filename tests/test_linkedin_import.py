"""Tests for LinkedIn profile import and merge functionality."""

from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from job_scout.linkedin_import import (
    LinkedInProfileImporter,
    _clean_ld_text,
    _extract_profile_from_html,
    _find_person_ld,
    _is_redacted,
    compute_linkedin_hash,
    merge_linkedin_into_profile,
)
from job_scout.models import CvProfile, CvRole

# Mirrors LinkedIn's real anonymous-visitor response: the current employer and
# one school survive, but historical titles/companies are asterisk-masked.
_SAMPLE_LD_HTML = """
<html><head>
<script type="application/ld+json">
{
  "@context": "http://schema.org",
  "@graph": [
    {"@type": "WebPage", "url": "https://nl.linkedin.com/in/example"},
    {
      "@type": "Person",
      "name": "Jane Doe",
      "alumniOf": [
        {
          "@type": "EducationalOrganization",
          "name": "Technische Universiteit Delft",
          "member": {"@type": "OrganizationRole", "startDate": 2018, "endDate": 2019}
        },
        {
          "@type": "EducationalOrganization",
          "name": "***** ****** *** ***",
          "member": {"@type": "OrganizationRole", "startDate": 2014, "endDate": 2018}
        }
      ],
      "knowsAbout": ["Python", "*********"],
      "jobTitle": ["******** ******", "********"]
    }
  ]
}
</script>
</head><body></body></html>
"""

_FULLY_REDACTED_HTML = """
<script type="application/ld+json">
{"@graph": [{"@type": "Person", "name": "****", "alumniOf": [], "knowsAbout": []}]}
</script>
"""


class TestLinkedInProfileImporter:
    """Test LinkedIn profile import methods."""

    def test_parse_pasted_text_extracts_skills(self) -> None:
        """parse_pasted_text should extract skills from pasted text."""
        text = """
        John Doe
        Senior Software Engineer

        Skills
        Python, Java, JavaScript, React, AWS

        Experience
        Senior Engineer at TechCorp (2020 - present)
        """
        result = LinkedInProfileImporter.parse_pasted_text(text)
        assert "Python" in result["skills"]
        assert "Java" in result["skills"]

    def test_parse_pasted_text_extracts_roles(self) -> None:
        """parse_pasted_text should extract work experience."""
        text = """
        Experience
        Senior Engineer at TechCorp
        Junior Developer at StartupX
        """
        result = LinkedInProfileImporter.parse_pasted_text(text)
        # Should have extracted roles
        assert len(result["past_roles"]) > 0

    def test_parse_pasted_text_empty_returns_empty_dict(self) -> None:
        """parse_pasted_text should handle empty text gracefully."""
        result = LinkedInProfileImporter.parse_pasted_text("")
        assert result["skills"] == []
        assert result["education"] == []
        assert result["past_roles"] == []

    def test_parse_export_zip_invalid_file(self) -> None:
        """parse_export should raise FileNotFoundError for missing file."""
        with pytest.raises(FileNotFoundError):
            LinkedInProfileImporter.parse_export("/nonexistent/file.zip")

    def test_parse_export_zip_invalid_format(self) -> None:
        """parse_export should raise ValueError for non-ZIP files."""
        with tempfile.NamedTemporaryFile(suffix=".zip") as tmp:
            # Write invalid ZIP data
            tmp.write(b"This is not a ZIP file")
            tmp.flush()

            with pytest.raises(ValueError):
                LinkedInProfileImporter.parse_export(tmp.name)

    def test_parse_export_zip_empty(self) -> None:
        """parse_export should handle empty ZIP gracefully."""
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
            with zipfile.ZipFile(tmp.name, "w") as zf:
                zf.writestr("dummy.txt", "dummy content")
            tmp.flush()

            result = LinkedInProfileImporter.parse_export(tmp.name)
            assert result["skills"] == []
            assert result["education"] == []
            assert result["past_roles"] == []

            Path(tmp.name).unlink()

    def test_fetch_profile_url_requires_allow_fetch(self) -> None:
        """fetch_profile_url should raise ValueError if allow_fetch is False."""
        with pytest.raises(ValueError, match="disabled by default"):
            LinkedInProfileImporter.fetch_profile_url(
                "https://www.linkedin.com/in/example", allow_fetch=False
            )


class TestMergeLinkedInIntoProfile:
    """Test merging LinkedIn data into existing CvProfile."""

    def test_merge_adds_new_skills(self) -> None:
        """merge should add new skills not in existing profile."""
        existing = CvProfile(
            skills=["Python", "JavaScript"],
            education=["MIT"],
            past_roles=[],
        )
        linkedin_data = {
            "skills": ["Java", "Rust", "Go"],
            "education": [],
            "past_roles": [],
        }

        merged, diff = merge_linkedin_into_profile(existing, linkedin_data)

        assert "Python" in merged.skills
        assert "JavaScript" in merged.skills
        assert "Java" in merged.skills
        assert "Rust" in merged.skills
        assert "Go" in merged.skills
        assert len(diff["added_skills"]) == 3

    def test_merge_skips_duplicate_skills(self) -> None:
        """merge should not add duplicate skills (case-insensitive)."""
        existing = CvProfile(
            skills=["Python", "Java"],
            education=[],
            past_roles=[],
        )
        linkedin_data = {
            "skills": ["PYTHON", "java", "Rust"],
            "education": [],
            "past_roles": [],
        }

        merged, diff = merge_linkedin_into_profile(existing, linkedin_data)

        # Should have 3 unique skills (Python, Java, Rust)
        assert len(merged.skills) == 3
        assert "Rust" in merged.skills
        # Only Rust should be in added
        assert len(diff["added_skills"]) == 1
        assert "Rust" in diff["added_skills"]

    def test_merge_adds_new_education(self) -> None:
        """merge should add new education not in existing profile."""
        existing = CvProfile(
            skills=[],
            education=["MIT"],
            past_roles=[],
        )
        linkedin_data = {
            "skills": [],
            "education": ["Stanford", "Oxford"],
            "past_roles": [],
        }

        merged, diff = merge_linkedin_into_profile(existing, linkedin_data)

        assert "MIT" in merged.education
        assert "Stanford" in merged.education
        assert "Oxford" in merged.education
        assert len(diff["added_education"]) == 2

    def test_merge_adds_new_roles(self) -> None:
        """merge should add new roles not in existing profile."""
        existing = CvProfile(
            skills=[],
            education=[],
            past_roles=[
                CvRole(title="Engineer", company="TechCorp", start_date="2020-01")
            ],
        )
        linkedin_data = {
            "skills": [],
            "education": [],
            "past_roles": [
                {
                    "title": "Developer",
                    "company": "StartupX",
                    "start_date": "2018-05",
                    "end_date": "2019-12",
                    "description": None,
                }
            ],
        }

        merged, diff = merge_linkedin_into_profile(existing, linkedin_data)

        assert len(merged.past_roles) == 2
        assert merged.past_roles[0].title == "Engineer"
        assert merged.past_roles[1].title == "Developer"
        assert len(diff["added_roles"]) == 1

    def test_merge_skips_duplicate_roles(self) -> None:
        """merge should not add duplicate roles (by company+title)."""
        existing = CvProfile(
            skills=[],
            education=[],
            past_roles=[
                CvRole(
                    title="Senior Engineer",
                    company="TechCorp",
                    start_date="2020-01",
                )
            ],
        )
        linkedin_data = {
            "skills": [],
            "education": [],
            "past_roles": [
                {
                    "title": "SENIOR ENGINEER",
                    "company": "techcorp",  # Different case
                    "start_date": "2020-01",
                    "end_date": None,
                    "description": None,
                }
            ],
        }

        merged, diff = merge_linkedin_into_profile(existing, linkedin_data)

        # Should not have added the duplicate (case-insensitive match)
        assert len(merged.past_roles) == 1
        assert len(diff["added_roles"]) == 0

    def test_merge_preserves_years_experience(self) -> None:
        """merge should preserve years_experience from existing profile."""
        existing = CvProfile(
            skills=[],
            education=[],
            past_roles=[],
            years_experience=5,
        )
        linkedin_data = {
            "skills": ["Java"],
            "education": [],
            "past_roles": [],
        }

        merged, diff = merge_linkedin_into_profile(existing, linkedin_data)

        assert merged.years_experience == 5


class TestComputeLinkedInHash:
    """Test hashing of LinkedIn data."""

    def test_compute_hash_consistent(self) -> None:
        """compute_linkedin_hash should return consistent hash."""
        data = {"skills": ["Python", "Java"], "education": [], "past_roles": []}
        hash1 = compute_linkedin_hash(data)
        hash2 = compute_linkedin_hash(data)
        assert hash1 == hash2

    def test_compute_hash_different_for_different_data(self) -> None:
        """compute_linkedin_hash should return different hashes for different data."""
        data1 = {"skills": ["Python"], "education": [], "past_roles": []}
        data2 = {"skills": ["Java"], "education": [], "past_roles": []}
        hash1 = compute_linkedin_hash(data1)
        hash2 = compute_linkedin_hash(data2)
        assert hash1 != hash2

    def test_compute_hash_order_independent(self) -> None:
        """compute_linkedin_hash should be order-independent (sorted keys)."""
        # Note: skill order might matter, but dict key order shouldn't
        data1 = {"skills": ["A"], "education": ["B"], "past_roles": []}
        data2 = {"education": ["B"], "past_roles": [], "skills": ["A"]}
        hash1 = compute_linkedin_hash(data1)
        hash2 = compute_linkedin_hash(data2)
        assert hash1 == hash2


class TestRedactionDetection:
    """Test detection of LinkedIn's asterisk-masked placeholder fields."""

    def test_all_asterisks_is_redacted(self) -> None:
        """A fully masked value is recognised as redacted."""
        assert _is_redacted("******** ******") is True

    def test_blank_is_redacted(self) -> None:
        """Empty/whitespace values count as redacted."""
        assert _is_redacted("") is True
        assert _is_redacted("   ") is True

    def test_real_text_is_not_redacted(self) -> None:
        """Genuine text is not treated as redacted."""
        assert _is_redacted("Technische Universiteit Delft") is False

    def test_clean_ld_text_drops_redacted(self) -> None:
        """_clean_ld_text returns None for masked values."""
        assert _clean_ld_text("*****") is None

    def test_clean_ld_text_drops_non_strings(self) -> None:
        """_clean_ld_text returns None for non-string values."""
        assert _clean_ld_text(None) is None
        assert _clean_ld_text(123) is None

    def test_clean_ld_text_strips_real_value(self) -> None:
        """_clean_ld_text returns stripped text for genuine values."""
        assert _clean_ld_text("  Acme Corp  ") == "Acme Corp"


class TestFindPersonLd:
    """Test locating the schema.org Person node inside a profile page."""

    def test_finds_person_node_in_graph(self) -> None:
        """The Person node is extracted from the JSON-LD @graph."""
        person = _find_person_ld(_SAMPLE_LD_HTML)
        assert person is not None
        assert person["name"] == "Jane Doe"

    def test_returns_none_when_no_ld_json(self) -> None:
        """Pages without JSON-LD yield None instead of raising."""
        assert _find_person_ld("<html><body>no data here</body></html>") is None

    def test_returns_none_on_malformed_json(self) -> None:
        """Malformed JSON-LD is skipped rather than raising."""
        html = '<script type="application/ld+json">{not valid json</script>'
        assert _find_person_ld(html) is None


class TestExtractProfileFromHtml:
    """Test redaction-aware extraction used by the URL-fetch path."""

    def test_extracts_unredacted_education_with_dates(self) -> None:
        """Visible schools are extracted with their date range."""
        data = _extract_profile_from_html(_SAMPLE_LD_HTML)
        assert any("Technische Universiteit Delft" in e for e in data["education"])
        assert any("2018" in e and "2019" in e for e in data["education"])

    def test_skips_redacted_education_entries(self) -> None:
        """Masked school names are dropped, not imported as asterisks."""
        data = _extract_profile_from_html(_SAMPLE_LD_HTML)
        assert len(data["education"]) == 1
        assert all("*" not in e for e in data["education"])

    def test_extracts_unredacted_skills_only(self) -> None:
        """Masked skills are dropped; visible ones are kept."""
        data = _extract_profile_from_html(_SAMPLE_LD_HTML)
        assert data["skills"] == ["Python"]

    def test_never_returns_roles_from_url_fetch(self) -> None:
        """Job titles are always masked for anonymous requests, so roles is empty."""
        data = _extract_profile_from_html(_SAMPLE_LD_HTML)
        assert data["past_roles"] == []

    def test_fully_redacted_profile_returns_all_empty(self) -> None:
        """A wholly masked profile yields no data rather than junk."""
        data = _extract_profile_from_html(_FULLY_REDACTED_HTML)
        assert data == {"skills": [], "education": [], "past_roles": []}

    def test_no_person_node_returns_all_empty(self) -> None:
        """Pages without a Person node degrade to empty data."""
        data = _extract_profile_from_html("<html>nothing</html>")
        assert data == {"skills": [], "education": [], "past_roles": []}


class TestFetchProfileUrlWithMockedRequest:
    """Test fetch_profile_url end-to-end against a mocked HTTP response."""

    def test_fetch_returns_extracted_data_when_allowed(self) -> None:
        """An allowed fetch parses the response into profile data."""
        mock_resp = MagicMock()
        mock_resp.text = _SAMPLE_LD_HTML
        mock_resp.raise_for_status.return_value = None
        with patch("requests.get", return_value=mock_resp):
            data = LinkedInProfileImporter.fetch_profile_url(
                "https://www.linkedin.com/in/example", allow_fetch=True
            )
        assert data["skills"] == ["Python"]
        assert len(data["education"]) == 1
        assert data["past_roles"] == []

    def test_fetch_raises_runtime_error_on_request_failure(self) -> None:
        """Network failures surface as RuntimeError with a clear message."""
        import requests

        with (
            patch("requests.get", side_effect=requests.ConnectionError("boom")),
            pytest.raises(RuntimeError, match="Failed to fetch"),
        ):
            LinkedInProfileImporter.fetch_profile_url(
                "https://www.linkedin.com/in/example", allow_fetch=True
            )


class TestFetchProfileBlockedStatus:
    """Test handling of LinkedIn's HTTP 999 bot-block responses."""

    def test_retries_past_transient_block(self) -> None:
        """A 999 block is retried, and a later 200 succeeds."""
        blocked = MagicMock()
        blocked.status_code = 999
        blocked.text = "blocked stub"
        ok = MagicMock()
        ok.status_code = 200
        ok.text = _SAMPLE_LD_HTML
        ok.raise_for_status.return_value = None

        with (
            patch("requests.get", side_effect=[blocked, ok]) as get,
            patch("job_scout.linkedin_import.sleep"),
        ):
            data = LinkedInProfileImporter.fetch_profile_url(
                "https://www.linkedin.com/in/example", allow_fetch=True
            )
        assert get.call_count == 2
        assert data["skills"] == ["Python"]

    def test_all_attempts_blocked_raises_actionable_error(self) -> None:
        """Persistent blocking raises rather than silently returning nothing."""
        blocked = MagicMock()
        blocked.status_code = 999
        blocked.text = "blocked stub"

        with (
            patch("requests.get", return_value=blocked),
            patch("job_scout.linkedin_import.sleep"),
            pytest.raises(RuntimeError, match="blocked every fetch attempt"),
        ):
            LinkedInProfileImporter.fetch_profile_url(
                "https://www.linkedin.com/in/example", allow_fetch=True
            )

    def test_blocked_error_points_to_safer_methods(self) -> None:
        """The error tells the user which import methods actually work."""
        blocked = MagicMock()
        blocked.status_code = 999
        blocked.text = "blocked stub"

        with (
            patch("requests.get", return_value=blocked),
            patch("job_scout.linkedin_import.sleep"),
            pytest.raises(RuntimeError) as exc_info,
        ):
            LinkedInProfileImporter.fetch_profile_url(
                "https://www.linkedin.com/in/example", allow_fetch=True
            )
        assert "paste" in str(exc_info.value).lower()
