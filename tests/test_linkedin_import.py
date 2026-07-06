"""Tests for LinkedIn profile import and merge functionality."""

from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path

import pytest

from job_scout.linkedin_import import (
    LinkedInProfileImporter,
    compute_linkedin_hash,
    merge_linkedin_into_profile,
)
from job_scout.models import CvProfile, CvRole


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
