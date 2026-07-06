"""Tests for batch LLM-based title screening."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import patch

from job_scout.models import Config, JobListing
from job_scout.title_screener import (
    _batch_jobs,
    _build_screening_prompt,
    _screen_batch,
    screen_job_titles,
)
from tests.helpers import FakeLLMClient


def _make_jobs(n: int) -> list[JobListing]:
    """Build n minimal JobListings with numbered titles."""
    return [
        JobListing(
            title=f"Job Title {i}",
            company=f"Company {i}",
            url=f"https://example.com/job/{i}",
            source="test",
            seen_at=datetime.now(UTC),
        )
        for i in range(1, n + 1)
    ]


def _config_with_profile(profile: str = "CRO specialist") -> Config:
    """Build a Config with a profile description."""
    return Config(
        profile_description=profile,
        negative_description="Dispatching companies",
    )


# ---------------------------------------------------------------------------
# _build_screening_prompt
# ---------------------------------------------------------------------------


def test_prompt_contains_profile_and_titles() -> None:
    """Prompt includes profile, negative desc, and all title @ company."""
    jobs = _make_jobs(3)
    prompt = _build_screening_prompt(jobs, "CRO specialist", "Dispatching companies")
    assert "CRO specialist" in prompt
    assert "Dispatching companies" in prompt
    assert "1. Job Title 1 @ Company 1" in prompt
    assert "2. Job Title 2 @ Company 2" in prompt
    assert "3. Job Title 3 @ Company 3" in prompt


def test_prompt_asks_for_keep_json() -> None:
    """Prompt instructs the LLM to return a keep list."""
    prompt = _build_screening_prompt(_make_jobs(1), "profile", "negative")
    assert '"keep"' in prompt


# ---------------------------------------------------------------------------
# _batch_jobs
# ---------------------------------------------------------------------------


def test_batch_jobs_single_batch() -> None:
    """All jobs fit in one batch when under batch_size."""
    jobs = _make_jobs(5)
    batches = _batch_jobs(jobs, batch_size=10)
    assert len(batches) == 1
    assert len(batches[0]) == 5


def test_batch_jobs_multiple_batches() -> None:
    """Jobs are split into correct number of batches."""
    jobs = _make_jobs(25)
    batches = _batch_jobs(jobs, batch_size=10)
    assert len(batches) == 3
    assert len(batches[0]) == 10
    assert len(batches[1]) == 10
    assert len(batches[2]) == 5


def test_batch_jobs_empty() -> None:
    """Empty input produces empty output."""
    assert _batch_jobs([], batch_size=10) == []


# ---------------------------------------------------------------------------
# _screen_batch
# ---------------------------------------------------------------------------


def test_screen_batch_keeps_selected_indices() -> None:
    """_screen_batch returns only jobs at the kept indices."""
    jobs = _make_jobs(4)
    client = FakeLLMClient([json.dumps({"keep": [1, 3]})])
    result = _screen_batch(jobs, "profile", "negative", client)
    assert len(result) == 2
    assert result[0].title == "Job Title 1"
    assert result[1].title == "Job Title 3"


def test_screen_batch_returns_all_on_llm_error() -> None:
    """_screen_batch returns all jobs when LLM raises LLMError."""
    jobs = _make_jobs(4)
    client = FakeLLMClient([], repeat_last=False)
    result = _screen_batch(jobs, "profile", "negative", client)
    assert len(result) == 4


def test_screen_batch_returns_all_on_bad_json() -> None:
    """_screen_batch returns all jobs when response is not valid JSON."""
    jobs = _make_jobs(4)
    client = FakeLLMClient(["This is not JSON"])
    result = _screen_batch(jobs, "profile", "negative", client)
    assert len(result) == 4


def test_screen_batch_returns_all_on_missing_keep_key() -> None:
    """_screen_batch returns all jobs when 'keep' key is missing."""
    jobs = _make_jobs(4)
    client = FakeLLMClient([json.dumps({"results": [1, 2]})])
    result = _screen_batch(jobs, "profile", "negative", client)
    assert len(result) == 4


def test_screen_batch_ignores_out_of_range_indices() -> None:
    """_screen_batch silently drops indices outside valid range."""
    jobs = _make_jobs(4)
    client = FakeLLMClient([json.dumps({"keep": [1, 99, -1, 3, 0]})])
    result = _screen_batch(jobs, "profile", "negative", client)
    assert len(result) == 2
    assert result[0].title == "Job Title 1"
    assert result[1].title == "Job Title 3"


def test_screen_batch_handles_string_indices() -> None:
    """_screen_batch converts string indices to ints."""
    jobs = _make_jobs(3)
    client = FakeLLMClient([json.dumps({"keep": ["1", "2"]})])
    result = _screen_batch(jobs, "profile", "negative", client)
    assert len(result) == 2


def test_screen_batch_returns_all_on_non_list_keep() -> None:
    """_screen_batch returns all when 'keep' is not a list."""
    jobs = _make_jobs(3)
    client = FakeLLMClient([json.dumps({"keep": "all"})])
    result = _screen_batch(jobs, "profile", "negative", client)
    assert len(result) == 3


def test_screen_batch_records_screening_purpose() -> None:
    """_screen_batch calls the client with purpose='screening'."""
    jobs = _make_jobs(2)
    client = FakeLLMClient([json.dumps({"keep": [1, 2]})])
    _screen_batch(jobs, "profile", "negative", client)
    assert client.calls[0][1] == "screening"


# ---------------------------------------------------------------------------
# screen_job_titles
# ---------------------------------------------------------------------------


def test_screen_job_titles_empty_list() -> None:
    """screen_job_titles returns empty list and zero count."""
    config = _config_with_profile()
    kept, count = screen_job_titles([], config)
    assert kept == []
    assert count == 0


def test_screen_job_titles_no_profile() -> None:
    """screen_job_titles returns all jobs when profile is empty."""
    config = Config()
    jobs = _make_jobs(5)
    kept, count = screen_job_titles(jobs, config)
    assert len(kept) == 5
    assert count == 0


def test_screen_job_titles_returns_correct_count() -> None:
    """screen_job_titles reports the correct screened count."""
    config = _config_with_profile()
    jobs = _make_jobs(10)
    client = FakeLLMClient([json.dumps({"keep": [1, 5, 9]})])
    kept, count = screen_job_titles(jobs, config, client=client)
    assert len(kept) == 3
    assert count == 7


def test_screen_job_titles_batches_large_lists() -> None:
    """screen_job_titles splits large lists into batches."""
    config = _config_with_profile()
    jobs = _make_jobs(15)

    # Each batch keeps only index 1
    client = FakeLLMClient([json.dumps({"keep": [1]})])
    with patch("job_scout.title_screener.BATCH_SIZE", 10):
        kept, count = screen_job_titles(jobs, config, client=client)

    # 2 batches (10 + 5), each keeps 1 job
    assert len(kept) == 2
    assert count == 13
