"""Tests for job scraping utilities."""

from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import MagicMock

import pytest

from job_scout.models import JobListing
from job_scout.scraper import (
    _deduplicate,
    _normalize_date,
    _parse_nvb_job,
    _scrape_nvb,
)


def _make_job(
    url: str = "https://example.com/job/1",
    title: str = "Dev",
    company: str = "Co",
) -> JobListing:
    """Build a minimal JobListing for scraper tests."""
    return JobListing(
        title=title, company=company, url=url, source="test", seen_at=datetime.now(UTC)
    )


# ---------------------------------------------------------------------------
# _deduplicate
# ---------------------------------------------------------------------------


def test_deduplicate_empty_list() -> None:
    """_deduplicate returns empty list for empty input."""
    assert _deduplicate([]) == []


def test_deduplicate_removes_duplicate_urls() -> None:
    """_deduplicate keeps first occurrence of a duplicated URL."""
    job1 = _make_job("https://example.com/1", title="Job A")
    job2 = _make_job("https://example.com/1", title="Job B")
    result = _deduplicate([job1, job2])
    assert len(result) == 1
    assert result[0].title == "Job A"


def test_deduplicate_keeps_unique_urls() -> None:
    """_deduplicate keeps all jobs when every URL is unique and title+company differ."""
    jobs = [_make_job(f"https://example.com/{i}", title=f"Job{i}") for i in range(5)]
    assert len(_deduplicate(jobs)) == 5


def test_deduplicate_preserves_order() -> None:
    """_deduplicate maintains the original order of first-seen URLs."""
    jobs = [_make_job(f"https://example.com/{i}", title=f"Job{i}") for i in range(3)]
    result = _deduplicate(jobs)
    assert [j.title for j in result] == ["Job0", "Job1", "Job2"]


def test_deduplicate_removes_duplicate_title_company() -> None:
    """_deduplicate removes jobs with identical normalized title+company.

    Even jobs with different URLs are treated as duplicates if they have
    the same normalized title and company name.
    """
    job1 = _make_job("https://example.com/1", title="Dev Role", company="Tech Co")
    job2 = _make_job("https://example.com/2", title="Dev Role", company="Tech Co")
    job3 = _make_job("https://example.com/3", title="Data Role", company="Tech Co")
    result = _deduplicate([job1, job2, job3])
    assert len(result) == 2
    assert result[0].url == "https://example.com/1"
    assert result[1].url == "https://example.com/3"


def test_deduplicate_title_company_case_insensitive() -> None:
    """_deduplicate treats title+company comparison as case-insensitive."""
    job1 = _make_job("https://example.com/1", title="Dev Role", company="Tech Co")
    job2 = _make_job("https://example.com/2", title="DEV ROLE", company="TECH CO")
    result = _deduplicate([job1, job2])
    assert len(result) == 1
    assert result[0].url == "https://example.com/1"


def test_deduplicate_title_company_with_multiple_spaces() -> None:
    """_deduplicate removes jobs with same title+company but different whitespace.

    This catches cross-posted jobs that may have been formatted differently
    by different job boards (e.g., one with "E-Commerce  Specialist" and
    another with "E-Commerce Specialist").
    """
    job1 = _make_job(
        "https://example.com/1",
        title="E-Commerce  Specialist",
        company="Vespo",
    )
    job2 = _make_job(
        "https://example.com/2",
        title="E-Commerce Specialist",
        company="Vespo",
    )
    job3 = _make_job(
        "https://example.com/3",
        title="E-Commerce   Specialist",
        company="Vespo  Inc",
    )
    result = _deduplicate([job1, job2, job3])
    assert len(result) == 2
    assert result[0].url == "https://example.com/1"
    assert result[1].url == "https://example.com/3"


# ---------------------------------------------------------------------------
# _normalize_date
# ---------------------------------------------------------------------------


def test_normalize_date_none_returns_none() -> None:
    """_normalize_date returns None for None input."""
    assert _normalize_date(None) is None


def test_normalize_date_datetime_passthrough() -> None:
    """_normalize_date returns the same datetime instance unchanged."""
    dt = datetime(2024, 6, 1, 12, 0)
    assert _normalize_date(dt) == dt


def test_normalize_date_date_converts_to_midnight_datetime() -> None:
    """_normalize_date converts a date object to a datetime at midnight."""
    d = date(2024, 6, 1)
    result = _normalize_date(d)
    assert result == datetime(2024, 6, 1, 0, 0)


def test_normalize_date_pandas_timestamp() -> None:
    """_normalize_date converts a pandas Timestamp to a datetime."""
    import pandas as pd

    ts = pd.Timestamp("2024-06-15 10:30:00")
    result = _normalize_date(ts)
    assert isinstance(result, datetime)
    assert result.year == 2024
    assert result.month == 6
    assert result.day == 15


def test_normalize_date_pandas_nat_returns_none() -> None:
    """_normalize_date returns None for pandas NaT."""
    import pandas as pd

    result = _normalize_date(pd.NaT)
    assert result is None


# ---------------------------------------------------------------------------
# _parse_nvb_job (JSON API)
# ---------------------------------------------------------------------------


def test_parse_nvb_job_valid_item() -> None:
    """_parse_nvb_job extracts title, company, location, and URL."""
    item = {
        "title": "CRO Specialist",
        "company": {"name": "TechCo"},
        "workLocation": {"city": "Amsterdam", "displayName": "Amsterdam"},
        "_links": {
            "detail": {"href": "https://www.nationalevacaturebank.nl/vacature/123/cro"}
        },
        "description": "Job description here",
    }
    result = _parse_nvb_job(item)
    assert result is not None
    assert result.title == "CRO Specialist"
    assert result.company == "TechCo"
    assert result.location == "Amsterdam"
    assert "123" in result.url
    assert result.source == "nationalevacaturebank"


def test_parse_nvb_job_missing_detail_url_returns_none() -> None:
    """_parse_nvb_job returns None when _links.detail.href is missing."""
    item = {"title": "Dev", "company": {"name": "Co"}, "_links": {}}
    assert _parse_nvb_job(item) is None


def test_parse_nvb_job_missing_company_defaults_unknown() -> None:
    """_parse_nvb_job uses 'Unknown' when company name is absent."""
    item = {
        "title": "Dev",
        "company": {},
        "workLocation": {},
        "_links": {"detail": {"href": "https://nvb.nl/job/1"}},
    }
    result = _parse_nvb_job(item)
    assert result is not None
    assert result.company == "Unknown"


def test_parse_nvb_job_falls_back_to_display_name() -> None:
    """_parse_nvb_job uses displayName when city is missing."""
    item = {
        "title": "Dev",
        "company": {"name": "Co"},
        "workLocation": {"displayName": "Noord-Holland"},
        "_links": {"detail": {"href": "https://nvb.nl/job/2"}},
    }
    result = _parse_nvb_job(item)
    assert result is not None
    assert result.location == "Noord-Holland"


def test_parse_nvb_job_uses_function_title_fallback() -> None:
    """_parse_nvb_job falls back to functionTitle when title is missing."""
    item = {
        "functionTitle": "Data Analyst",
        "company": {"name": "Co"},
        "_links": {"detail": {"href": "https://nvb.nl/job/3"}},
    }
    result = _parse_nvb_job(item)
    assert result is not None
    assert result.title == "Data Analyst"


# ---------------------------------------------------------------------------
# _scrape_nvb (network error handling)
# ---------------------------------------------------------------------------


def test_scrape_nvb_request_error_returns_empty(  # noqa: E501
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_scrape_nvb returns empty list when the HTTP request raises."""
    import requests

    def raise_error(*args: object, **kwargs: object) -> None:
        raise requests.ConnectionError("Connection refused")

    monkeypatch.setattr("requests.get", raise_error)
    result = _scrape_nvb("developer", 10)
    assert result == []


def test_scrape_nvb_http_error_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """_scrape_nvb returns empty list when the server returns an HTTP error."""
    import requests

    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = requests.HTTPError("500 Server Error")
    monkeypatch.setattr("requests.get", lambda *a, **kw: mock_resp)
    result = _scrape_nvb("developer", 10)
    assert result == []


# ---------------------------------------------------------------------------
# Keyword limit configuration
# ---------------------------------------------------------------------------


def test_scrape_all_jobs_respects_jobspy_keyword_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """scrape_all_jobs uses jobspy_keyword_limit to limit jobspy keywords."""
    from job_scout.models import Config
    from job_scout.scraper import scrape_all_jobs

    config = Config(
        keywords_dutch=["dev_nl_1", "dev_nl_2", "dev_nl_3"],
        keywords_english=["dev_en_1", "dev_en_2", "dev_en_3"],
        jobspy_keyword_limit=2,
        nvb_keyword_limit=1,
    )

    # Mock the scraping functions to track which keywords are used
    scraped_jobspy_keywords = []
    scraped_nvb_keywords = []

    def mock_jobspy(keyword: str, config: Config) -> list[JobListing]:  # noqa: E501
        scraped_jobspy_keywords.append(keyword)
        return []

    def mock_nvb(keyword: str, max_results: int) -> list[JobListing]:  # noqa: E501
        scraped_nvb_keywords.append(keyword)
        return []

    def mock_rate_limit_jobspy(keyword: str, config: Config) -> list[JobListing]:  # noqa: E501
        return mock_jobspy(keyword, config)

    def mock_rate_limit_nvb(keyword: str, max_results: int) -> list[JobListing]:  # noqa: E501
        return mock_nvb(keyword, max_results)

    monkeypatch.setattr(
        "job_scout.scraper._scrape_jobspy_with_rate_limit", mock_rate_limit_jobspy
    )
    monkeypatch.setattr(
        "job_scout.scraper._scrape_nvb_with_rate_limit", mock_rate_limit_nvb
    )

    scrape_all_jobs(config, None)

    # Verify that only jobspy_keyword_limit keywords were used for jobspy
    assert len(scraped_jobspy_keywords) == 2
    # Verify that only nvb_keyword_limit keywords were used for nvb
    assert len(scraped_nvb_keywords) == 1


def test_scrape_all_jobs_respects_nvb_keyword_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """scrape_all_jobs uses nvb_keyword_limit to limit nvb keywords."""
    from job_scout.models import Config
    from job_scout.scraper import scrape_all_jobs

    config = Config(
        keywords_dutch=["dev_nl_1", "dev_nl_2", "dev_nl_3"],
        keywords_english=["dev_en_1", "dev_en_2"],
        jobspy_keyword_limit=1,
        nvb_keyword_limit=2,
    )

    # Mock the scraping functions to track which keywords are used
    scraped_jobspy_keywords = []
    scraped_nvb_keywords = []

    def mock_jobspy(keyword: str, config: Config) -> list[JobListing]:  # noqa: E501
        scraped_jobspy_keywords.append(keyword)
        return []

    def mock_nvb(keyword: str, max_results: int) -> list[JobListing]:  # noqa: E501
        scraped_nvb_keywords.append(keyword)
        return []

    def mock_rate_limit_jobspy(keyword: str, config: Config) -> list[JobListing]:  # noqa: E501
        return mock_jobspy(keyword, config)

    def mock_rate_limit_nvb(keyword: str, max_results: int) -> list[JobListing]:  # noqa: E501
        return mock_nvb(keyword, max_results)

    monkeypatch.setattr(
        "job_scout.scraper._scrape_jobspy_with_rate_limit", mock_rate_limit_jobspy
    )
    monkeypatch.setattr(
        "job_scout.scraper._scrape_nvb_with_rate_limit", mock_rate_limit_nvb
    )

    scrape_all_jobs(config, None)

    # Verify that only jobspy_keyword_limit keywords were used for jobspy
    assert len(scraped_jobspy_keywords) == 1
    # Verify that only nvb_keyword_limit keywords were used for nvb
    assert len(scraped_nvb_keywords) == 2
