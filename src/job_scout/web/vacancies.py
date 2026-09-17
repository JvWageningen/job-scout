"""Searchable vacancy library and applicant-owned pins, progress and notes."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from job_scout.config import list_users, user_db_path
from job_scout.database import Database
from job_scout.models import ApplicationStage, CompanyReview, JobListing

# The same fallback as application_stage_for_status, evaluated before pagination.
_STAGE_SQL = """COALESCE(application_stage, CASE
    WHEN status IN ('approved', 'ready') THEN 'interested'
    WHEN status = 'submitted' THEN 'applied'
    WHEN status = 'interviewing' THEN 'interviewing'
    WHEN status = 'offer' THEN 'offer'
    WHEN status = 'expired' THEN 'closed'
    WHEN status = 'rejected' AND status_updated_at IS NOT NULL THEN 'closed'
    ELSE 'to_review' END)"""
_SORTS = {
    "score_desc": "fit_score DESC",
    "score_asc": "fit_score ASC",
    "date_desc": "seen_at DESC",
    "date_asc": "seen_at ASC",
}
# A dropdown, not a browser: enough room for every realistic shortlist without
# loading a whole library into the page.
_CHOICE_LIMIT = 200
_LEGACY = {
    ApplicationStage.TO_REVIEW: "matched",
    ApplicationStage.INTERESTED: "approved",
    ApplicationStage.APPLIED: "submitted",
    ApplicationStage.INTERVIEWING: "interviewing",
    ApplicationStage.OFFER: "offer",
    ApplicationStage.CLOSED: "expired",
}


class VacancyUpdate(BaseModel):
    """Only explicit applicant actions may alter these fields."""

    model_config = ConfigDict(extra="forbid")
    user: str
    pinned: StrictBool | None = None
    stage: ApplicationStage | None = None
    notes: str | None = Field(default=None, max_length=4000)


class VacancyPage(BaseModel):
    """A stable page plus filter options drawn from the entire user's library."""

    items: list[JobListing]
    total: int
    sources: list[str]


def _database(user: str) -> Database:
    """Validate an existing user before accessing its database."""
    if (
        user not in list_users()
        or user in {".", "..", "all"}
        or "/" in user
        or "\\" in user
    ):
        raise HTTPException(404, "Select a single existing user.")
    return Database(user_db_path(user))


def _fold(value: str | None) -> str:
    """Normalize Unicode text for case-insensitive literal substring searches."""
    return (value or "").casefold()


def find_vacancies(
    db: Database,
    *,
    q: str = "",
    scope: str = "all",
    pinned_only: bool = False,
    stage: ApplicationStage | None = None,
    min_score: int | None = None,
    source: str | None = None,
    sort: str = "score_desc",
    limit: int = 20,
    offset: int = 0,
) -> VacancyPage:
    """Search all stored vacancies before sorting and taking a bounded page.

    Args:
        db: Selected user's database.
        q: Literal search across title, company, location, description and URLs.
        scope: All, matched/applicant-tracked, or automatically filtered vacancies.
        pinned_only: Restrict to bookmarked vacancies.
        stage: Optional applicant progress filter.
        min_score: Minimum vacancy match score.
        source: Exact board or source name.
        sort: Supported ordering key; pins always come first.
        limit: Maximum page size.
        offset: Number of ordered matches to skip.

    Returns:
        Matching rows, filtered total, and all available source names.
    """
    where, params = _filters(q, scope, pinned_only, stage, min_score, source)
    order = _SORTS.get(sort, _SORTS["score_desc"])
    null_order = "fit_score IS NULL, " if sort.startswith("score_") else ""
    cte = f"WITH library AS (SELECT *, {_STAGE_SQL} AS stage FROM jobs) "
    with db._conn() as conn:
        conn.create_function("casefold", 1, _fold, deterministic=True)
        total = conn.execute(
            cte + f"SELECT COUNT(*) FROM library WHERE {where}", params
        ).fetchone()[0]
        rows = conn.execute(
            cte + f"SELECT * FROM library WHERE {where} "
            f"ORDER BY pinned DESC, {null_order}{order}, id DESC "
            "LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        sources = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT source FROM jobs "
                "WHERE source IS NOT NULL AND source != '' ORDER BY source"
            )
        ]
    jobs = [db._row_to_job(row) for row in rows]
    for job in jobs:
        raw = db.get_company_review(job.company.lower(), max_age_days=365)
        job.company_review = CompanyReview.model_validate_json(raw) if raw else None
    return VacancyPage(items=jobs, total=total, sources=sources)


def _filters(
    q: str,
    scope: str,
    pinned_only: bool,
    stage: ApplicationStage | None,
    min_score: int | None,
    source: str | None,
) -> tuple[str, list[Any]]:
    """Build parameterized filters; search punctuation is always literal."""
    parts = ["1=1"]
    params: list[Any] = []
    if q.strip():
        parts.append(
            "("
            + " OR ".join(
                f"instr(casefold({field}), ?) > 0"
                for field in (
                    "title",
                    "company",
                    "location",
                    "description",
                    "url",
                    "official_url",
                )
            )
            + ")"
        )
        params.extend([q.strip().casefold()] * 6)
    if scope == "matches":
        parts.append("status NOT IN ('rejected', 'expired') AND stage != 'closed'")
    elif scope == "filtered":
        parts.append("status = 'rejected' AND stage = 'to_review'")
    if pinned_only:
        parts.append("pinned = 1")
    if stage is not None:
        parts.append("stage = ?")
        params.append(stage.value)
    if min_score is not None:
        parts.append("fit_score >= ?")
        params.append(min_score)
    if source:
        parts.append("source = ?")
        params.append(source)
    return " AND ".join(parts), params


def open_vacancy_choices(
    user: str, limit: int = _CHOICE_LIMIT
) -> list[dict[str, object]]:
    """List the vacancies still worth working on, best match first.

    One definition, shared by every tool that asks the applicant to pick a
    vacancy, so the letter writer and the interview preparation can never
    disagree about which vacancies are still live. It is the library's
    "matches" scope -- nothing the pipeline rejected, nothing expired, nothing
    the applicant closed -- ordered by fit score. Vacancies without a
    description are dropped: there is nothing to ground a letter or a question
    in. Listing every vacancy ever seen buried the handful that matter.

    Args:
        user: Name of an existing user. Callers behind the API have already
            validated it; this revalidates rather than trusting them.
        limit: Largest shortlist to return.

    Returns:
        One entry per offerable vacancy, with its id, title, company, status
        and fit score. Never the description or any other private content.
    """
    page = find_vacancies(
        _database(user), scope="matches", sort="score_desc", limit=limit
    )
    return [
        {
            "id": job.id,
            "title": job.title,
            "company": job.company,
            "status": job.status.value,
            "fit_score": job.fit_score,
        }
        for job in page.items
        if job.description
    ]


def update_vacancy(db: Database, job_id: int, body: VacancyUpdate) -> JobListing:
    """Apply explicit progress changes without hidden intermediate steps.

    Args:
        db: Selected user's database.
        job_id: Vacancy to update.
        body: Explicitly supplied pin, stage and/or notes.

    Returns:
        Updated vacancy, or a 404 if it no longer exists.
    """
    fields: list[str] = []
    values: list[Any] = []
    if body.pinned is not None:
        fields.append("pinned = ?")
        values.append(int(body.pinned))
    if "notes" in body.model_fields_set:
        fields.append("notes = ?")
        values.append(body.notes)
    if body.stage is not None:
        now = datetime.now(UTC).isoformat()
        fields.extend(["application_stage = ?", "status = ?", "status_updated_at = ?"])
        values.extend([body.stage.value, _LEGACY[body.stage], now])
        if body.stage is ApplicationStage.APPLIED:
            fields.append("applied_at = COALESCE(applied_at, ?)")
            values.append(now)
        if body.stage is ApplicationStage.INTERESTED:
            fields.append("approved_at = COALESCE(approved_at, ?)")
            values.append(now)
    if not fields:
        raise HTTPException(400, "Supply a pin, progress status or note change.")
    with db._conn() as conn:
        updated = conn.execute(
            f"UPDATE jobs SET {', '.join(fields)} WHERE id = ?", [*values, job_id]
        ).rowcount
    if not updated:
        raise HTTPException(404, "Vacancy not found.")
    result = db.get_job(job_id)
    assert result is not None
    return result


def build_vacancies_router() -> APIRouter:
    """Expose the library under the existing dashboard authentication middleware."""
    router = APIRouter()

    @router.get("/api/vacancies")
    def search(
        user: str,
        q: str = Query("", max_length=300),
        scope: Literal["all", "matches", "filtered"] = "all",
        pinned_only: bool = False,
        stage: ApplicationStage | None = None,
        min_score: int | None = Query(None, ge=0, le=100),
        source: str | None = None,
        sort: Literal[
            "score_desc", "score_asc", "date_desc", "date_asc"
        ] = "score_desc",
        limit: int = Query(20, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ) -> VacancyPage:
        """Search the selected user's entire library, including older vacancies."""
        return find_vacancies(
            _database(user),
            q=q,
            scope=scope,
            pinned_only=pinned_only,
            stage=stage,
            min_score=min_score,
            source=source,
            sort=sort,
            limit=limit,
            offset=offset,
        )

    @router.patch("/api/vacancies/{job_id}")
    def update(job_id: int, body: VacancyUpdate) -> JobListing:
        """Save user-owned progress, pin or notes independently of evaluation."""
        return update_vacancy(_database(body.user), job_id, body)

    return router
