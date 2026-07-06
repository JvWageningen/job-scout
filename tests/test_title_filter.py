"""Tests for title-based pre-filtering."""

from __future__ import annotations

from datetime import UTC, datetime

from job_scout.models import Config, JobListing
from job_scout.title_filter import filter_jobs_by_title, passes_title_filter


def _make_job(title: str) -> JobListing:
    """Build a minimal JobListing with the given title."""
    return JobListing(
        title=title,
        company="TestCo",
        url=f"https://example.com/{title.replace(' ', '-')}",
        source="test",
        seen_at=datetime.now(UTC),
    )


def _config_with_keywords(
    include: list[str] | None = None,
    exclude: list[str] | None = None,
) -> Config:
    """Build a Config with title filter keywords."""
    return Config(
        title_include_keywords=include or [],
        title_exclude_keywords=exclude or [],
    )


# ---------------------------------------------------------------------------
# passes_title_filter
# ---------------------------------------------------------------------------


def test_no_keywords_passes_everything() -> None:
    """All jobs pass when no title keywords are configured."""
    config = _config_with_keywords()
    assert passes_title_filter(_make_job("SAP Basis Beheerder"), config)
    assert passes_title_filter(_make_job("CRO Specialist"), config)


def test_include_keyword_matches_case_insensitive() -> None:
    """Include keywords match regardless of case."""
    config = _config_with_keywords(include=["CRO", "conversie"])
    assert passes_title_filter(_make_job("CRO Specialist"), config)
    assert passes_title_filter(_make_job("cro manager"), config)
    assert passes_title_filter(_make_job("Conversie Analist"), config)


def test_include_keyword_rejects_non_matching() -> None:
    """Jobs without any include keyword in the title are filtered out."""
    config = _config_with_keywords(include=["CRO", "conversie", "conversion"])
    assert not passes_title_filter(_make_job("SAP Basis Beheerder"), config)
    assert not passes_title_filter(_make_job("Payroll Specialist"), config)
    assert not passes_title_filter(_make_job("AFAS Consultant"), config)


def test_exclude_keyword_rejects_matching() -> None:
    """Jobs with an exclude keyword in the title are filtered out."""
    config = _config_with_keywords(exclude=["SAP", "payroll"])
    assert not passes_title_filter(_make_job("Senior SAP Beheerder"), config)
    assert not passes_title_filter(_make_job("Payroll Specialist"), config)


def test_exclude_takes_priority_over_include() -> None:
    """A job matching both include and exclude is rejected."""
    config = _config_with_keywords(include=["specialist"], exclude=["payroll"])
    assert not passes_title_filter(_make_job("Payroll Specialist"), config)


def test_include_partial_match() -> None:
    """Include keywords match as substrings."""
    config = _config_with_keywords(include=["market"])
    assert passes_title_filter(_make_job("Online Marketeer"), config)
    assert passes_title_filter(_make_job("Marketing Manager"), config)


# ---------------------------------------------------------------------------
# filter_jobs_by_title
# ---------------------------------------------------------------------------


def test_filter_returns_all_when_no_keywords() -> None:
    """filter_jobs_by_title returns all jobs when no keywords are set."""
    config = _config_with_keywords()
    jobs = [_make_job("Dev"), _make_job("Manager")]
    passed, filtered = filter_jobs_by_title(jobs, config)
    assert len(passed) == 2
    assert filtered == 0


def test_filter_counts_filtered_jobs() -> None:
    """filter_jobs_by_title returns correct filtered count."""
    config = _config_with_keywords(include=["CRO"])
    jobs = [
        _make_job("CRO Specialist"),
        _make_job("SAP Beheerder"),
        _make_job("CRO Manager"),
        _make_job("Payroll Specialist"),
    ]
    passed, filtered = filter_jobs_by_title(jobs, config)
    assert len(passed) == 2
    assert filtered == 2
    assert all("CRO" in j.title for j in passed)


def test_filter_empty_list() -> None:
    """filter_jobs_by_title handles empty input."""
    config = _config_with_keywords(include=["CRO"])
    passed, filtered = filter_jobs_by_title([], config)
    assert passed == []
    assert filtered == 0
