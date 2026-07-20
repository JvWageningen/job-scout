"""Pydantic models for job-scout data structures."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class TravelMode(StrEnum):
    """Transport modes for travel time calculation."""

    CAR = "car"
    PUBLIC_TRANSPORT = "public_transport"
    BIKE = "bike"


class JobStatus(StrEnum):
    """Processing status for a job listing."""

    NEW = "new"
    VIEWED = "viewed"
    APPROVED = "approved"
    READY = "ready"
    SUBMITTED = "submitted"
    INTERVIEWING = "interviewing"
    OFFER = "offer"
    REJECTED = "rejected"
    # Vacancy detected as filled or no longer available (auto-prune).
    EXPIRED = "expired"
    # Legacy status for backward compatibility
    MATCHED = "matched"


class TravelTime(BaseModel):
    """Travel time for a specific transport mode."""

    mode: TravelMode
    minutes: float | None = None
    available: bool = True
    error: str | None = None


class HiringManagerSuggestion(BaseModel):
    """A suggested hiring manager contact for a job listing."""

    name: str
    role: str | None = None
    email: str | None = None
    linkedin_url: str | None = None
    confidence: int = Field(default=50, ge=0, le=100)
    reasoning: str = ""


class CompanyResearch(BaseModel):
    """Company research and signals for a job listing."""

    company_name: str
    industry: str | None = None
    company_size: str | None = None
    culture_indicators: list[str] = Field(default_factory=list)
    tech_stack_hints: list[str] = Field(default_factory=list)
    growth_signals: str | None = None
    research_notes: str = ""
    hiring_managers: list[HiringManagerSuggestion] = Field(default_factory=list)
    research_timestamp: datetime | None = None


class CompanyReview(BaseModel):
    """A work-quality review of a company, synthesised from public info.

    Combines employee-review sentiment with public signals (financial health,
    growth, company age) into a single estimate of how good it is to work there.
    """

    company: str
    work_score: int | None = None  # 0-100 estimate; None if too little info
    summary: str = ""
    pros: list[str] = Field(default_factory=list)
    cons: list[str] = Field(default_factory=list)
    employee_sentiment: str | None = None
    financial_health: str | None = None
    growth: str | None = None
    company_age: str | None = None
    confidence: str = "low"  # low / medium / high
    sources: list[str] = Field(default_factory=list)
    reviewed_at: datetime | None = None


class JobListing(BaseModel):
    """A single job listing with evaluation metadata."""

    id: int | None = None
    title: str
    company: str
    location: str | None = None
    url: str
    description: str | None = None
    source: str
    date_posted: datetime | None = None
    fit_score: int | None = None
    fit_reasoning: str | None = None
    negative_match: bool = False
    negative_reasoning: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    salary_period: str | None = None
    vacation_days: int | None = None
    compensation_reasoning: str | None = None
    distance_km: float | None = None
    travel_times: list[TravelTime] = Field(default_factory=list)
    notified: bool = False
    notification_pending: bool = False
    seen_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC)  # noqa: E731
    )
    status: JobStatus = JobStatus.NEW
    location_unknown: bool = False
    approved_at: datetime | None = None
    approved_by: str | None = None
    approval_notes: str | None = None
    applied_at: datetime | None = None
    status_updated_at: datetime | None = None
    notes: str | None = None
    company_research: CompanyResearch | None = None
    company_review: CompanyReview | None = None
    official_url: str | None = None
    official_available: bool | None = None


class CustomSite(BaseModel):
    """A custom job-listing URL to scrape per user."""

    name: str
    url: str
    enabled: bool = True
    render_js: bool = False


class ExtractedJob(BaseModel):
    """A single job extracted from a custom site by the LLM."""

    title: str
    company: str = ""
    location: str | None = None
    url: str
    description: str | None = None


class ExtractedJobs(BaseModel):
    """Wrapper for LLM-extracted job listings."""

    jobs: list[ExtractedJob]


class Config(BaseModel):
    """Application configuration stored in config.yaml."""

    name: str = ""
    notification_channel: Literal["ntfy", "email", "slack", "discord"] = "ntfy"
    notification_mode: Literal["per_job", "digest"] = "per_job"
    ntfy_topic: str = "job-scout-alerts"
    ntfy_server: str = "https://ntfy.sh"
    slack_webhook_url: str = ""
    discord_webhook_url: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_from: str = ""
    smtp_to: str = ""
    smtp_username: str | None = None
    smtp_password: str | None = None
    home_address: str = ""
    max_travel_car: int = 30
    max_travel_pt: int = 60
    max_travel_bike: int = 45
    profile_description: str = ""
    negative_description: str = ""
    cv_path: str | None = None
    cv_notes: str = ""
    keywords_dutch: list[str] = Field(default_factory=list)
    keywords_english: list[str] = Field(default_factory=list)
    language_preferences: list[str] = Field(default_factory=lambda: ["nl", "en"])
    ors_api_key: str | None = None
    ns_api_key: str | None = None
    fit_score_threshold: int = 60
    max_jobs_per_source: int = 50
    title_include_keywords: list[str] = Field(default_factory=list)
    title_exclude_keywords: list[str] = Field(default_factory=list)
    max_distance_km: int | None = None
    min_salary: int | None = None
    max_salary: int | None = None
    min_vacation_days: int | None = None
    llm_provider: Literal["claude_cli", "zai", "kilo_cli", "local"] = "claude_cli"
    claude_evaluation_model: str | None = None
    claude_screening_model: str = "haiku"
    zai_api_key: str | None = None
    zai_base_url: str = "https://api.z.ai/api/coding/paas/v4"
    zai_model: str = "glm-5.1"
    zai_screening_model: str | None = "glm-4.5-air"
    zai_screening_batch_size: int = 20
    kilo_evaluation_model: str = "zai/glm-5.1"
    kilo_screening_model: str = "zai/glm-4.5-air"
    kilo_quick_eval_model: str = "zai/glm-4.5-air"
    zai_quick_eval_model: str = "glm-4.5-air"
    local_base_url: str = "http://localhost:11434/v1"
    local_api_key: str | None = None
    local_model: str = "llama3.1"
    local_screening_model: str | None = None
    local_quick_eval_model: str | None = None
    local_keywords_model: str | None = None
    local_evaluation_timeout: float = 120
    local_screening_timeout: float = 90
    quick_eval_threshold: int = 40
    quick_eval_provider: Literal["claude_cli", "zai", "kilo_cli", "local"] | None = None
    screening_provider: Literal["claude_cli", "zai", "kilo_cli", "local"] | None = None
    evaluation_provider: Literal["claude_cli", "zai", "kilo_cli", "local"] | None = None
    keywords_provider: Literal["claude_cli", "zai", "kilo_cli", "local"] | None = None
    cv_parsing_provider: Literal["claude_cli", "zai", "kilo_cli", "local"] | None = None
    llm_max_attempts: int = 3
    llm_retry_base_delay: float = 1.0
    max_parallel_evaluations: int = 5
    jobspy_keyword_limit: int = Field(default=5, ge=1, le=20)
    nvb_keyword_limit: int = Field(default=3, ge=1, le=20)
    jobspy_sites: list[str] = Field(
        default_factory=lambda: ["indeed", "linkedin"],
        description="Job sources to scrape via jobspy",
    )
    custom_sites: list[CustomSite] = Field(default_factory=list)
    dashboard_token: str | None = None
    geocode_cache_days: int = 90
    travel_cache_days: int = 14
    prune_enabled: bool = False
    prune_use_browser: bool = False
    prune_use_llm: bool = False
    verify_matches_open: bool = True
    find_official_sources: bool = True
    company_review_enabled: bool = True
    linkedin_import_allow_url_fetch: bool = False
    linkedin_profile_url: str | None = None
    schedule_hour: int = 8
    schedule_minute: int = 0
    schedule_days: str = "1-5"
    schedule_paused: bool = False
    mcp_enabled: bool = False
    mcp_port: int = 5000

    @field_validator("jobspy_sites", mode="before")
    @classmethod
    def validate_jobspy_sites(cls, v: list[str]) -> list[str]:
        """Validate that jobspy_sites contains only supported site names.

        Args:
            v: List of site names to validate.

        Returns:
            Validated list of site names.

        Raises:
            ValueError: If any site name is not supported by jobspy.
        """
        valid_sites = {
            "linkedin",
            "indeed",
            "zip_recruiter",
            "glassdoor",
            "google",
            "bayt",
            "naukri",
            "bdjobs",
        }
        if not isinstance(v, list):
            raise ValueError("jobspy_sites must be a list")
        invalid_sites = [site for site in v if site not in valid_sites]
        if invalid_sites:
            raise ValueError(
                f"Invalid jobspy sites: {invalid_sites}. "
                f"Valid options are: {sorted(valid_sites)}"
            )
        return v

    @field_validator("schedule_days", mode="before")
    @classmethod
    def validate_schedule_days(cls, v: str) -> str:
        """Validate that schedule_days uses valid cron day-of-week syntax.

        Args:
            v: Day-of-week string (cron format).

        Returns:
            Validated day-of-week string.

        Raises:
            ValueError: If the format is invalid.
        """
        valid_presets = {"*", "0", "1", "2", "3", "4", "5", "6", "0-6", "1-5", "0,6"}
        if v not in valid_presets:
            raise ValueError(
                f"Invalid schedule_days: {v!r}. "
                f"Valid presets are: {sorted(valid_presets)}"
            )
        return v


class KeywordsResult(BaseModel):
    """Generated search keywords in Dutch and English."""

    dutch: list[str]
    english: list[str]
    title_include: list[str] = Field(default_factory=list)
    title_exclude: list[str] = Field(default_factory=list)


class FitEvaluation(BaseModel):
    """Claude's evaluation of a job's fit with the candidate."""

    fit_score: int = Field(ge=0, le=100)
    reasoning: str


class NegativeEvaluation(BaseModel):
    """Claude's evaluation of a job against negative criteria."""

    matches_negative: bool
    reasoning: str


class CompensationEvaluation(BaseModel):
    """Claude's extraction of salary and vacation info from a job listing."""

    salary_min: int | None = None
    salary_max: int | None = None
    salary_period: str | None = None
    vacation_days: int | None = None
    reasoning: str = ""


class RunStats(BaseModel):
    """Statistics for a single search run."""

    scraped: int = 0
    deduplicated: int = 0
    title_filtered: int = 0
    title_screened: int = 0
    quick_filtered: int = 0
    evaluated: int = 0
    matched: int = 0
    rejected: int = 0
    notified: int = 0
    errors: list[str] = Field(default_factory=list)


class RunHistoryEntry(BaseModel):
    """A single run history entry with timing and stats."""

    started_at: datetime
    duration_seconds: float
    scraped: int
    deduplicated: int
    title_filtered: int
    title_screened: int
    quick_filtered: int
    evaluated: int
    matched: int
    rejected: int
    notified: int
    errors: int


class CvRole(BaseModel):
    """A single role in the candidate's work history."""

    title: str
    company: str
    start_date: str | None = None
    end_date: str | None = None
    description: str | None = None


class CvProfile(BaseModel):
    """Structured CV profile extracted and validated by the LLM."""

    skills: list[str] = Field(default_factory=list)
    years_experience: int | None = None
    education: list[str] = Field(default_factory=list)
    past_roles: list[CvRole] = Field(default_factory=list)


class StarStory(BaseModel):
    """A STAR (Situation, Task, Action, Result) story for interview preparation."""

    id: int | None = None
    situation: str = Field(..., description="The situation or context")
    task: str = Field(..., description="The task or challenge faced")
    action: str = Field(..., description="The specific action taken")
    result: str = Field(..., description="The measurable result achieved")
    keywords: list[str] = Field(
        default_factory=list, description="Keywords for matching to interview questions"
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC)  # noqa: E731
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC)  # noqa: E731
    )


class BehavioralQuestion(BaseModel):
    """A behavioral question extracted from a job description."""

    question: str
    keywords: list[str] = Field(
        default_factory=list, description="Key skills/concepts the question addresses"
    )


class InterviewPrep(BaseModel):
    """Interview preparation data for a job listing."""

    job_id: int | None = None
    behavioral_questions: list[BehavioralQuestion] = Field(
        default_factory=list, description="Extracted behavioral questions"
    )
    matched_stories: dict[str, list[StarStory]] = Field(
        default_factory=dict,
        description="Mapping from question to matched STAR stories",
    )
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC)  # noqa: E731
    )


class ApplicationTracker:
    """State machine for managing job application lifecycle."""

    # Valid transitions: from_state -> [to_states]
    VALID_TRANSITIONS: dict[JobStatus, list[JobStatus]] = {
        JobStatus.NEW: [JobStatus.VIEWED, JobStatus.REJECTED, JobStatus.EXPIRED],
        JobStatus.VIEWED: [JobStatus.APPROVED, JobStatus.REJECTED, JobStatus.EXPIRED],
        JobStatus.APPROVED: [JobStatus.READY, JobStatus.REJECTED, JobStatus.EXPIRED],
        JobStatus.READY: [JobStatus.SUBMITTED, JobStatus.REJECTED, JobStatus.EXPIRED],
        JobStatus.SUBMITTED: [JobStatus.INTERVIEWING, JobStatus.REJECTED],
        JobStatus.INTERVIEWING: [JobStatus.OFFER, JobStatus.REJECTED],
        JobStatus.OFFER: [JobStatus.REJECTED],
        JobStatus.REJECTED: [],
        JobStatus.EXPIRED: [],
        JobStatus.MATCHED: [JobStatus.VIEWED, JobStatus.REJECTED, JobStatus.EXPIRED],
    }

    @staticmethod
    def can_transition(from_status: JobStatus, to_status: JobStatus) -> bool:
        """Check if a transition is valid.

        Args:
            from_status: Current status.
            to_status: Desired status.

        Returns:
            True if transition is allowed, False otherwise.
        """
        return to_status in ApplicationTracker.VALID_TRANSITIONS.get(from_status, [])

    @staticmethod
    def get_valid_transitions(status: JobStatus) -> list[JobStatus]:
        """Get all valid next states for a given status.

        Args:
            status: Current status.

        Returns:
            List of valid next statuses.
        """
        return ApplicationTracker.VALID_TRANSITIONS.get(status, [])
