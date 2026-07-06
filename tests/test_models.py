"""Tests for Pydantic data models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from job_scout.models import (
    CompensationEvaluation,
    Config,
    FitEvaluation,
    JobListing,
    JobStatus,
    KeywordsResult,
    NegativeEvaluation,
    RunStats,
    TravelMode,
    TravelTime,
)


def test_job_listing_defaults(sample_job: JobListing) -> None:
    """New jobs default to NEW status with no evaluation data."""
    assert sample_job.status == JobStatus.NEW
    assert sample_job.fit_score is None
    assert sample_job.travel_times == []
    assert sample_job.notified is False
    assert sample_job.location_unknown is False


def test_config_defaults() -> None:
    """Config defaults match the spec."""
    config = Config()
    assert config.max_travel_car == 30
    assert config.max_travel_pt == 60
    assert config.max_travel_bike == 45
    assert config.fit_score_threshold == 60
    assert config.home_address == ""


def test_travel_time_car_mode() -> None:
    """TravelTime with CAR mode stores minutes correctly."""
    tt = TravelTime(mode=TravelMode.CAR, minutes=20.0)
    assert tt.available is True
    assert tt.minutes == 20.0
    assert tt.error is None


def test_travel_time_unavailable() -> None:
    """TravelTime can represent an unavailable mode."""
    tt = TravelTime(mode=TravelMode.BIKE, available=False, error="No API key")
    assert tt.minutes is None
    assert tt.available is False


def test_fit_evaluation_score_bounds() -> None:
    """FitEvaluation rejects scores outside 0-100."""
    fit = FitEvaluation(fit_score=85, reasoning="Good match")
    assert fit.fit_score == 85

    with pytest.raises(ValidationError):
        FitEvaluation(fit_score=101, reasoning="Too high")

    with pytest.raises(ValidationError):
        FitEvaluation(fit_score=-1, reasoning="Too low")


def test_negative_evaluation() -> None:
    """NegativeEvaluation captures the rejection flag and reasoning."""
    neg = NegativeEvaluation(matches_negative=True, reasoning="Social media role")
    assert neg.matches_negative is True


def test_keywords_result() -> None:
    """KeywordsResult stores Dutch and English keyword lists."""
    kw = KeywordsResult(dutch=["software engineer"], english=["developer"])
    assert len(kw.dutch) == 1
    assert len(kw.english) == 1


def test_keywords_result_title_keywords() -> None:
    """KeywordsResult stores title include/exclude keyword lists."""
    kw = KeywordsResult(
        dutch=["dev"],
        english=["dev"],
        title_include=["CRO", "conversie"],
        title_exclude=["SAP", "payroll"],
    )
    assert kw.title_include == ["CRO", "conversie"]
    assert kw.title_exclude == ["SAP", "payroll"]


def test_keywords_result_title_keywords_default_empty() -> None:
    """KeywordsResult defaults title keyword lists to empty."""
    kw = KeywordsResult(dutch=[], english=[])
    assert kw.title_include == []
    assert kw.title_exclude == []


def test_run_stats_defaults() -> None:
    """RunStats initialises all counters to zero."""
    stats = RunStats()
    assert stats.scraped == 0
    assert stats.title_filtered == 0
    assert stats.matched == 0
    assert stats.errors == []


def test_job_listing_serialisation(sample_job: JobListing) -> None:
    """JobListing round-trips through model_dump/model_validate."""
    data = sample_job.model_dump()
    restored = JobListing(**data)
    assert restored.url == sample_job.url
    assert restored.status == sample_job.status


def test_travel_mode_str_enum() -> None:
    """TravelMode values are plain strings for JSON serialisation."""
    assert TravelMode.CAR.value == "car"
    assert TravelMode.PUBLIC_TRANSPORT.value == "public_transport"
    assert TravelMode.BIKE.value == "bike"


def test_job_status_values() -> None:
    """JobStatus enum has the expected string values."""
    assert JobStatus.NEW.value == "new"
    assert JobStatus.MATCHED.value == "matched"
    assert JobStatus.REJECTED.value == "rejected"


def test_config_ntfy_server_default() -> None:
    """Config defaults ntfy_server to https://ntfy.sh."""
    config = Config()
    assert config.ntfy_server == "https://ntfy.sh"


def test_config_empty_keywords_by_default() -> None:
    """Config starts with empty keyword lists."""
    config = Config()
    assert config.keywords_dutch == []
    assert config.keywords_english == []
    assert config.title_include_keywords == []
    assert config.title_exclude_keywords == []


def test_job_listing_negative_match_defaults_false() -> None:
    """JobListing.negative_match defaults to False."""
    job = JobListing(title="Dev", company="Co", url="https://x.com", source="test")
    assert job.negative_match is False
    assert job.negative_reasoning is None


def test_job_listing_salary_defaults_none() -> None:
    """JobListing salary and vacation fields default to None."""
    job = JobListing(title="Dev", company="Co", url="https://x.com", source="test")
    assert job.salary_min is None
    assert job.salary_max is None
    assert job.salary_period is None
    assert job.vacation_days is None


def test_compensation_evaluation_defaults() -> None:
    """CompensationEvaluation defaults all fields to None/empty."""
    comp = CompensationEvaluation()
    assert comp.salary_min is None
    assert comp.salary_max is None
    assert comp.vacation_days is None
    assert comp.reasoning == ""


def test_compensation_evaluation_stores_values() -> None:
    """CompensationEvaluation stores salary and vacation data."""
    comp = CompensationEvaluation(
        salary_min=3500,
        salary_max=4500,
        salary_period="monthly",
        vacation_days=25,
        reasoning="Listed in description",
    )
    assert comp.salary_min == 3500
    assert comp.salary_max == 4500
    assert comp.vacation_days == 25


def test_config_salary_defaults_none() -> None:
    """Config salary and vacation limits default to None."""
    config = Config()
    assert config.min_salary is None
    assert config.max_salary is None
    assert config.min_vacation_days is None


def test_travel_time_error_field() -> None:
    """TravelTime.error stores a descriptive error string."""
    tt = TravelTime(  # noqa: E501
        mode=TravelMode.PUBLIC_TRANSPORT, available=False, error="No NS API key"
    )
    assert tt.error == "No NS API key"
    assert tt.available is False


def test_config_jobspy_sites_defaults() -> None:
    """Config.jobspy_sites defaults to indeed and linkedin."""
    config = Config()
    assert config.jobspy_sites == ["indeed", "linkedin"]


def test_config_jobspy_sites_custom() -> None:
    """Config.jobspy_sites accepts custom site lists."""
    config = Config(jobspy_sites=["indeed", "glassdoor", "zip_recruiter"])
    assert config.jobspy_sites == ["indeed", "glassdoor", "zip_recruiter"]


def test_config_jobspy_sites_valid() -> None:
    """Config.jobspy_sites validates against known site names."""
    config = Config(
        jobspy_sites=[
            "indeed",
            "linkedin",
            "glassdoor",
            "zip_recruiter",
            "google",
            "bayt",
            "naukri",
            "bdjobs",
        ]
    )
    assert len(config.jobspy_sites) == 8


def test_config_jobspy_sites_invalid() -> None:
    """Config.jobspy_sites rejects invalid site names."""
    with pytest.raises(ValidationError):
        Config(jobspy_sites=["indeed", "invalid_site"])


def test_config_jobspy_sites_multiple_invalid() -> None:
    """Config.jobspy_sites reports all invalid site names."""
    with pytest.raises(ValidationError) as exc_info:
        Config(jobspy_sites=["indeed", "fake1", "fake2"])
    error_msg = str(exc_info.value)
    assert "fake1" in error_msg
    assert "fake2" in error_msg


def test_config_jobspy_sites_empty_list() -> None:
    """Config.jobspy_sites accepts empty lists."""
    config = Config(jobspy_sites=[])
    assert config.jobspy_sites == []


def test_run_stats_error_list_grows() -> None:
    """RunStats.errors accumulates error messages."""
    stats = RunStats()
    stats.errors.append("first error")
    stats.errors.append("second error")
    assert len(stats.errors) == 2


def test_config_max_jobs_per_source_default() -> None:
    """Config.max_jobs_per_source defaults to 50."""
    config = Config()
    assert config.max_jobs_per_source == 50


def test_config_jobspy_keyword_limit_default() -> None:
    """Config.jobspy_keyword_limit defaults to 5."""
    config = Config()
    assert config.jobspy_keyword_limit == 5


def test_config_nvb_keyword_limit_default() -> None:
    """Config.nvb_keyword_limit defaults to 3."""
    config = Config()
    assert config.nvb_keyword_limit == 3


def test_config_jobspy_keyword_limit_valid_range() -> None:
    """Config.jobspy_keyword_limit accepts values 1-20."""
    config = Config(jobspy_keyword_limit=10)
    assert config.jobspy_keyword_limit == 10

    config = Config(jobspy_keyword_limit=1)
    assert config.jobspy_keyword_limit == 1

    config = Config(jobspy_keyword_limit=20)
    assert config.jobspy_keyword_limit == 20


def test_config_jobspy_keyword_limit_below_minimum() -> None:
    """Config.jobspy_keyword_limit rejects values below 1."""
    with pytest.raises(ValidationError):
        Config(jobspy_keyword_limit=0)


def test_config_jobspy_keyword_limit_above_maximum() -> None:
    """Config.jobspy_keyword_limit rejects values above 20."""
    with pytest.raises(ValidationError):
        Config(jobspy_keyword_limit=21)


def test_config_nvb_keyword_limit_valid_range() -> None:
    """Config.nvb_keyword_limit accepts values 1-20."""
    config = Config(nvb_keyword_limit=10)
    assert config.nvb_keyword_limit == 10

    config = Config(nvb_keyword_limit=1)
    assert config.nvb_keyword_limit == 1

    config = Config(nvb_keyword_limit=20)
    assert config.nvb_keyword_limit == 20


def test_config_nvb_keyword_limit_below_minimum() -> None:
    """Config.nvb_keyword_limit rejects values below 1."""
    with pytest.raises(ValidationError):
        Config(nvb_keyword_limit=0)


def test_config_nvb_keyword_limit_above_maximum() -> None:
    """Config.nvb_keyword_limit rejects values above 20."""
    with pytest.raises(ValidationError):
        Config(nvb_keyword_limit=21)


def test_cv_profile_defaults() -> None:
    """CvProfile defaults all fields to empty/None."""
    from job_scout.models import CvProfile

    profile = CvProfile()
    assert profile.skills == []
    assert profile.years_experience is None
    assert profile.education == []
    assert profile.past_roles == []


def test_cv_profile_with_data() -> None:
    """CvProfile stores skills, experience, education, and roles."""
    from job_scout.models import CvProfile

    profile = CvProfile(
        skills=["Python", "Kubernetes"],
        years_experience=7,
        education=["BSc CS", "MSc AI"],
        past_roles=["SWE at Google", "TL at Meta"],
    )
    assert len(profile.skills) == 2
    assert profile.years_experience == 7
    assert len(profile.education) == 2
    assert len(profile.past_roles) == 2


def test_cv_profile_serialisation() -> None:
    """CvProfile round-trips through model_dump/model_validate."""
    from job_scout.models import CvProfile

    profile = CvProfile(
        skills=["Go", "Rust"],
        years_experience=5,
        education=["PhD Computer Science"],
        past_roles=["Principal Engineer"],
    )
    data = profile.model_dump()
    restored = CvProfile(**data)
    assert restored.skills == profile.skills
    assert restored.years_experience == profile.years_experience
    assert restored.education == profile.education
    assert restored.past_roles == profile.past_roles
