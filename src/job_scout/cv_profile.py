"""CV profile extraction and caching."""

from __future__ import annotations

from loguru import logger

from job_scout.cv_parser import compute_cv_hash, parse_cv_structured
from job_scout.database import Database
from job_scout.llm.base import LLMClient
from job_scout.models import CvProfile


def get_or_parse_cv_profile(
    raw_cv_text: str, client: LLMClient, db: Database
) -> CvProfile:
    """Get CV profile from cache or parse fresh, with automatic caching.

    Checks if the CV text hash exists in the database cache. If found, returns
    the cached profile. Otherwise, parses the CV using the LLM, caches the result,
    and returns the profile.

    Args:
        raw_cv_text: Raw text extracted from the CV file.
        client: LLMClient to use for structured parsing if cache miss.
        db: Database instance for caching operations.

    Returns:
        Parsed CvProfile (either from cache or freshly parsed).

    Raises:
        ValueError: If LLM parsing fails.
    """
    import json

    cv_hash = compute_cv_hash(raw_cv_text)

    # Check cache
    cached_json = db.get_cached_cv_profile(cv_hash)
    if cached_json:
        try:
            data = json.loads(cached_json)
            profile = CvProfile(**data)
            logger.debug(f"Retrieved CV profile from cache (hash={cv_hash[:8]}...)")
            return profile
        except Exception as e:
            logger.warning(f"Failed to deserialize cached CV profile: {e}")

    # Cache miss: parse fresh
    logger.debug(f"Parsing CV (hash={cv_hash[:8]}...) with LLM")
    profile = parse_cv_structured(raw_cv_text, client)

    # Cache the result
    cache_json = json.dumps(profile.model_dump())
    db.save_cv_profile_cache(cv_hash, cache_json)

    return profile
