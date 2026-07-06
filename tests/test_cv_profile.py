"""Tests for CV profile extraction and caching."""

from __future__ import annotations

from unittest.mock import MagicMock

from job_scout.cv_profile import get_or_parse_cv_profile
from job_scout.models import CvProfile


def test_get_or_parse_cv_profile_uses_cache() -> None:
    """get_or_parse_cv_profile returns cached profile without LLM call."""
    cv_text = "Sample CV text"
    client = MagicMock()
    db = MagicMock()

    # Mock the cache to return a cached profile
    cached_json = (
        '{"skills": ["Python"], "years_experience": 5, '
        '"education": [], "past_roles": []}'
    )
    db.get_cached_cv_profile.return_value = cached_json

    profile = get_or_parse_cv_profile(cv_text, client, db)

    assert isinstance(profile, CvProfile)
    assert profile.skills == ["Python"]
    assert profile.years_experience == 5
    # LLM client should not be called
    client.complete.assert_not_called()


def test_get_or_parse_cv_profile_parses_and_caches() -> None:
    """get_or_parse_cv_profile parses CV and caches result on cache miss."""
    from job_scout.cv_parser import compute_cv_hash

    cv_text = "Sample CV text"
    client = MagicMock()
    client.complete.return_value = """
    ```json
    {
        "skills": ["Python", "Go"],
        "years_experience": 7,
        "education": ["MSc AI"],
        "past_roles": ["Engineer"]
    }
    ```
    """
    db = MagicMock()
    db.get_cached_cv_profile.return_value = None  # Cache miss

    profile = get_or_parse_cv_profile(cv_text, client, db)

    assert isinstance(profile, CvProfile)
    assert profile.skills == ["Python", "Go"]
    assert profile.years_experience == 7
    # Should have cached the result
    cv_hash = compute_cv_hash(cv_text)
    db.save_cv_profile_cache.assert_called_once()
    assert db.save_cv_profile_cache.call_args[0][0] == cv_hash


def test_get_or_parse_cv_profile_handles_corrupt_cache() -> None:
    """get_or_parse_cv_profile falls back to parsing if cached JSON is corrupt."""
    cv_text = "Sample CV text"
    client = MagicMock()
    client.complete.return_value = """
    {"skills": ["Java"], "years_experience": 3, "education": [], "past_roles": []}
    """
    db = MagicMock()
    db.get_cached_cv_profile.return_value = "{invalid json"  # Corrupt cache

    profile = get_or_parse_cv_profile(cv_text, client, db)

    # Should still parse successfully
    assert profile.skills == ["Java"]
    assert profile.years_experience == 3
    # Should re-cache the result
    db.save_cv_profile_cache.assert_called_once()
