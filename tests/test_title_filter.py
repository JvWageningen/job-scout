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


# ---------------------------------------------------------------------------
# morpheme-aware matching
#
# Regression tests for the Dutch-market matching rules. Naive substring
# matching let "AI" mean *Maintenance* and let the exclude keyword "bouw" kill
# *werktuigbouwkunde*; strict word boundaries instead lost *Optical* (matched
# by the stem "optica") and *Testingenieur* (a compound head).
# ---------------------------------------------------------------------------


def test_acronym_include_requires_standalone_token() -> None:
    """Short upper-case acronyms must not match inside a longer word."""
    config = _config_with_keywords(include=["AI"])
    assert passes_title_filter(_make_job("AI Engineer"), config)
    assert passes_title_filter(_make_job("AI/ML Research Engineer"), config)
    assert not passes_title_filter(
        _make_job("Electrical Maintenance Technicians"), config
    )
    assert not passes_title_filter(_make_job("Technician Repair"), config)
    assert not passes_title_filter(
        _make_job("Vacature Trainee Procestechnoloog"), config
    )


def test_acronym_matching_stays_case_insensitive() -> None:
    """Acronym matching is bounded, not case-sensitive."""
    config = _config_with_keywords(include=["CRO"])
    assert passes_title_filter(_make_job("CRO Specialist"), config)
    assert passes_title_filter(_make_job("cro manager"), config)
    assert not passes_title_filter(_make_job("Microcredential Advisor"), config)


def test_include_keyword_matches_inflected_forms() -> None:
    """Include stems still match suffixed forms (the substring behaviour)."""
    config = _config_with_keywords(
        include=["engineer", "optica", "kwaliteit", "research", "system"]
    )
    assert passes_title_filter(_make_job("Optical Designer"), config)
    assert passes_title_filter(_make_job("Stage Controls Engineering"), config)
    assert passes_title_filter(_make_job("Kwaliteitscontroleur"), config)
    assert passes_title_filter(_make_job("Medior Researcher"), config)
    assert passes_title_filter(_make_job("Systemen Specialist"), config)


def test_include_keyword_matches_compound_head() -> None:
    """Dutch/German compounds put the head noun last; it must still match."""
    config = _config_with_keywords(include=["engineer", "ingenieur", "photonics"])
    assert passes_title_filter(_make_job("Testingenieur"), config)
    assert passes_title_filter(_make_job("Projectengineer"), config)
    assert passes_title_filter(_make_job("Kwaliteitsingenieur"), config)
    assert passes_title_filter(_make_job("Prozessingenieur Validierung"), config)
    assert passes_title_filter(_make_job("PhD Position Nanophotonics"), config)


def test_exclude_keyword_anchors_to_word_start_only() -> None:
    """Exclude keywords must not veto on a mid-word collision."""
    config = _config_with_keywords(
        include=["engineer", "ingenieur", "kwaliteit", "meet"],
        exclude=["bouw", "zorg", "HR", "SAP"],
    )
    # Previously killed by a mid-word exclude match, all squarely in scope.
    assert passes_title_filter(_make_job("Werktuigbouwkundig Engineer"), config)
    assert passes_title_filter(_make_job("Machinebouw Engineer"), config)
    assert passes_title_filter(_make_job("Specialist Kwaliteitszorg"), config)
    assert passes_title_filter(_make_job("Vliegtuigbouwkundige Ingenieur"), config)
    # "SAP" hides inside hoogspanning-SAP-paratuur.
    assert passes_title_filter(
        _make_job("Meettechnicus Hoogspanningsapparatuur"), config
    )


def test_exclude_keyword_still_rejects_genuine_matches() -> None:
    """Word-initial exclude matches must keep working."""
    config = _config_with_keywords(
        include=["engineer", "manager", "specialist", "opzichter"],
        exclude=["bouw", "zorg", "HR", "SAP", "retail"],
    )
    assert not passes_title_filter(_make_job("Bouwkundig Opzichter"), config)
    assert not passes_title_filter(_make_job("Zorgmanager"), config)
    assert not passes_title_filter(_make_job("HR Manager"), config)
    assert not passes_title_filter(_make_job("SAP Specialist"), config)
    assert not passes_title_filter(_make_job("Retail Manager"), config)


def test_exclude_veto_beats_include_hits() -> None:
    """An exclude match rejects the job even when include keywords hit."""
    config = _config_with_keywords(include=["engineer"], exclude=["docent"])
    assert not passes_title_filter(_make_job("Docent Engineering"), config)


def test_blank_keywords_are_ignored() -> None:
    """Empty or whitespace-only keywords never match."""
    config = _config_with_keywords(include=["engineer"], exclude=["   ", ""])
    assert passes_title_filter(_make_job("Test Engineer"), config)
