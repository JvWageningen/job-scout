"""Tests for deterministic salary extraction and the compensation backstop."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from job_scout.models import Config, JobListing
from job_scout.salary import extract_salary_range


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (r"Een salaris tussen de € 3\.255,55 en € 4\.651,00", (3255, 4651)),
        ("bruto maandsalaris tussen de €3500 en €4500 per maand", (3500, 4500)),
        (r"Salaris tussen €4\.160 en €5\.648 per maand", (4160, 5648)),
        ("salaris tussen de €2.800 en €3.200 bruto per maand", (2800, 3200)),
        ("Een goed salaris van €45.000 per jaar plus bonus", (3750, 3750)),
        ("a competitive salary of EUR 3200 per month", (3200, 3200)),
        ("Wij bieden een marktconform salaris afhankelijk van ervaring", (None, None)),
        ("25 vakantiedagen, 8% vakantiegeld, opleidingsbudget van €1000", (None, None)),
        ("", (None, None)),
    ],
)
def test_extract_salary_range(
    text: str, expected: tuple[int | None, int | None]
) -> None:
    """Salary is parsed from varied Dutch/English formats; noise is ignored."""
    assert extract_salary_range(text) == expected


def test_extract_salary_none_for_missing_text() -> None:
    """None input yields no salary."""
    assert extract_salary_range(None) == (None, None)


def _job_with_desc(desc: str) -> JobListing:
    return JobListing(
        title="CRO Specialist",
        company="Acme",
        location="Amsterdam",
        url="https://example.com/1",
        source="indeed",
        description=desc,
        seen_at=datetime.now(UTC),
    )


def test_compensation_backstop_rejects_below_min_when_llm_missed() -> None:
    """A stated below-min salary the LLM missed is caught via the description."""
    from job_scout.cli import _passes_compensation_filter

    config = Config(min_salary=3500)
    # LLM left salary null; description states €2.800-€3.200 (max below 3500).
    job = _job_with_desc("Prima functie. bruto maandsalaris tussen de €2.800 en €3.200")
    assert job.salary_min is None and job.salary_max is None

    assert _passes_compensation_filter(job, config) is False
    # Backstop also persists the extracted figures onto the job.
    assert job.salary_max == 3200


def test_compensation_backstop_passes_when_range_reaches_min() -> None:
    """A range whose upper bound meets the minimum passes."""
    from job_scout.cli import _passes_compensation_filter

    config = Config(min_salary=3500)
    job = _job_with_desc("Salaris tussen €3.255 en €4.651 per maand")
    assert _passes_compensation_filter(job, config) is True
    assert job.salary_max == 4651


def test_compensation_single_value_below_min_rejected() -> None:
    """A single salary figure in salary_min (max None) below floor is rejected."""
    from job_scout.cli import _passes_compensation_filter

    config = Config(min_salary=3500)
    job = _job_with_desc("no salary here")
    job.salary_min = 3000
    job.salary_max = None
    assert _passes_compensation_filter(job, config) is False


def test_compensation_unknown_salary_passes_fail_open() -> None:
    """Genuinely unknown compensation still passes (fail-open)."""
    from job_scout.cli import _passes_compensation_filter

    config = Config(min_salary=3500)
    job = _job_with_desc("Marktconform salaris, afhankelijk van ervaring.")
    assert _passes_compensation_filter(job, config) is True
