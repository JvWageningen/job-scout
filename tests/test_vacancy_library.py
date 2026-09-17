"""Regression coverage for the searchable library and applicant-owned metadata."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import job_scout.config as config
from job_scout.database import Database
from job_scout.models import ApplicationStage, CompanyReview, JobListing, JobStatus
from job_scout.web.app import create_app
from job_scout.web.vacancies import VacancyUpdate, find_vacancies, update_vacancy


def add_job(db: Database, number: int, **values: object) -> int:
    """Insert a uniquely identifiable vacancy with overridable fields."""
    data = dict(
        title=f"Engineer {number}",
        company="Example Instruments",
        url=f"https://example.org/jobs/{number}",
        source="board",
        location="Utrecht",
        fit_score=number % 100,
        status=JobStatus.MATCHED,
        seen_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=number),
    )
    data.update(values)
    return db.save_job(JobListing.model_validate(data))


@pytest.fixture
def library_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Provide two users with overlapping IDs in isolated databases."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.yaml")
    config.write_global_config({"llm_provider": "local"})
    for name in ("Alex", "Sam"):
        config.save_user_config(name, {})
        add_job(Database(config.user_db_path(name)), 1, title=f"{name} vacancy")
    return TestClient(create_app())


@pytest.mark.parametrize(
    ("status", "changed", "stage"),
    [
        ("new", None, "to_review"),
        ("viewed", None, "to_review"),
        ("matched", None, "to_review"),
        ("rejected", None, "to_review"),
        ("rejected", "2026-01-01", "closed"),
        ("approved", None, "interested"),
        ("ready", None, "interested"),
        ("submitted", None, "applied"),
        ("interviewing", None, "interviewing"),
        ("offer", None, "offer"),
        ("expired", None, "closed"),
    ],
)
def test_legacy_stage_fallback(
    tmp_db: Database,
    status: str,
    changed: str | None,
    stage: str,
) -> None:
    """SQL filters and model conversion agree without rewriting old history."""
    job_id = add_job(tmp_db, 1, status=status)
    with tmp_db._conn() as conn:
        conn.execute(
            "UPDATE jobs SET status_updated_at=? WHERE id=?", (changed, job_id)
        )
    page = find_vacancies(tmp_db, stage=ApplicationStage(stage))
    assert page.total == 1
    assert page.items[0].application_stage.value == stage
    assert page.items[0].status.value == status
    assert not page.items[0].pinned


def test_additive_migration(tmp_path: Path) -> None:
    """An old database gains metadata columns without losing stored jobs or notes."""
    path = tmp_path / "old.db"
    db = Database(path)
    job_id = add_job(db, 1, status="submitted")
    with db._conn() as conn:
        conn.execute("UPDATE jobs SET notes='Keep this contact'")
        conn.execute("ALTER TABLE jobs DROP COLUMN pinned")
        conn.execute("ALTER TABLE jobs DROP COLUMN application_stage")
    migrated = Database(path).get_job(job_id)
    assert migrated is not None
    assert migrated.notes == "Keep this contact"
    assert migrated.application_stage == ApplicationStage.APPLIED
    assert migrated.pinned is False


def test_search_all_pages_literal_and_unicode(tmp_db: Database) -> None:
    """Search older pages, all useful text fields, and literal SQL punctuation."""
    for i in range(45):
        add_job(tmp_db, i)
    special = add_job(
        tmp_db,
        99,
        title="Straße 100%_O'Brien",
        company="Unique Employer",
        location="Delft",
        description="Vacuum calibration",
        source="rare-board",
    )
    with tmp_db._conn() as conn:
        conn.execute(
            "UPDATE jobs SET official_url=? WHERE id=?",
            ("https://employer.example/special", special),
        )
    for query in (
        "strasse",
        "100%_o'brien",
        "unique employer",
        "DELFT",
        "calibration",
        "jobs/99",
        "employer.example",
    ):
        page = find_vacancies(tmp_db, q=query)
        assert page.total == 1
        assert page.items[0].id == special
        assert page.sources == ["board", "rare-board"]
    assert find_vacancies(tmp_db, q="' OR 1=1 --").total == 0
    assert find_vacancies(tmp_db, q="%").total == 1
    pages = [find_vacancies(tmp_db, offset=i) for i in (0, 20, 40)]
    assert [len(p.items) for p in pages] == [20, 20, 6]
    assert len({j.id for p in pages for j in p.items}) == 46


def test_pins_sort_and_combined_filters(tmp_db: Database) -> None:
    """Pins come first; score, progress, scope and source apply before pagination."""
    low = add_job(tmp_db, 1, fit_score=20)
    high = add_job(tmp_db, 2, fit_score=90)
    add_job(tmp_db, 3, fit_score=95, status="rejected")
    update_vacancy(
        tmp_db, low, VacancyUpdate(user="Alex", pinned=True, stage="interested")
    )
    assert [j.id for j in find_vacancies(tmp_db).items][0] == low
    assert find_vacancies(tmp_db, pinned_only=True).total == 1
    assert find_vacancies(tmp_db, scope="filtered").total == 1
    page = find_vacancies(tmp_db, scope="matches", min_score=50, source="board")
    assert [j.id for j in page.items] == [high]
    assert find_vacancies(tmp_db, stage=ApplicationStage.INTERESTED).total == 1
    update_vacancy(tmp_db, low, VacancyUpdate(user="Alex", pinned=False))
    assert find_vacancies(tmp_db, pinned_only=True).total == 0


def test_sort_unscored_dates_and_cached_company(tmp_db: Database) -> None:
    """Unscored jobs sort last by score, but date sorting ignores match scores."""
    scored = add_job(tmp_db, 1, fit_score=80)
    new = add_job(tmp_db, 2, fit_score=None)
    review = CompanyReview(company="Example Instruments", work_score=99)
    tmp_db.save_company_review("example instruments", review.model_dump_json())
    assert find_vacancies(tmp_db, sort="date_desc").items[0].id == new
    for sort in ("score_asc", "score_desc", "date_asc"):
        page = find_vacancies(tmp_db, sort=sort)
        assert page.items[0].id == scored
        assert page.items[0].company_review.work_score == 99
        assert page.items[0].fit_score == 80


@pytest.mark.parametrize("batch", [False, True])
def test_rescore_preserves_applicant_metadata(tmp_db: Database, batch: bool) -> None:
    """Both scraper upserts preserve pins, notes, stages and application dates."""
    job_id = add_job(tmp_db, 1)
    update_vacancy(tmp_db, job_id, VacancyUpdate(user="Alex", notes="Call Tuesday"))
    applied = update_vacancy(
        tmp_db, job_id, VacancyUpdate(user="Alex", stage="applied")
    )
    original_date = applied.applied_at
    update_vacancy(tmp_db, job_id, VacancyUpdate(user="Alex", pinned=True))
    raw = tmp_db.get_job(job_id).model_copy(
        update={"fit_score": 30, "status": JobStatus.REJECTED}
    )
    if batch:
        tmp_db.save_jobs_batch([raw], update_existing=True)
    else:
        tmp_db.save_job(raw, update_existing=True)
    result = find_vacancies(tmp_db, scope="matches").items[0]
    assert result.application_stage == ApplicationStage.APPLIED
    assert result.status == JobStatus.SUBMITTED
    assert result.pinned and result.notes == "Call Tuesday"
    assert result.applied_at == original_date
    assert result.fit_score == 30
    for stage in ("closed", "to_review", "applied"):
        result = update_vacancy(tmp_db, job_id, VacancyUpdate(user="Alex", stage=stage))
        assert result.application_stage.value == stage
        assert result.applied_at == original_date
        assert result.notes == "Call Tuesday" and result.pinned


def test_api_isolation_and_validation(library_client: TestClient) -> None:
    """Metadata edits target only the selected user and validate inputs."""
    client = library_client
    for body in ({"stage": "applied"}, {"pinned": True}, {"notes": "Contact Pat"}):
        response = client.patch("/api/vacancies/1", json={"user": "Alex", **body})
        assert response.status_code == 200
    alex = client.get("/api/vacancies", params={"user": "Alex"}).json()["items"][0]
    sam = client.get("/api/vacancies", params={"user": "Sam"}).json()["items"][0]
    assert alex["pinned"] and alex["notes"] == "Contact Pat"
    assert alex["application_stage"] == "applied"
    assert not sam["pinned"] and sam["notes"] is None
    assert sam["application_stage"] == "to_review"
    for name in ("../Alex", "..\\Alex", "missing", "all"):
        assert client.get("/api/vacancies", params={"user": name}).status_code == 404
        assert (
            client.patch(
                "/api/vacancies/1", json={"user": name, "pinned": True}
            ).status_code
            == 404
        )
    for body in (
        {"stage": "ready"},
        {"pinned": "false"},
        {"status": "submitted"},
        {"notes": "x" * 4001},
    ):
        assert (
            client.patch("/api/vacancies/1", json={"user": "Alex", **body}).status_code
            == 422
        )
    assert client.patch("/api/vacancies/1", json={"user": "Alex"}).status_code == 400
    assert (
        client.patch(
            "/api/vacancies/999", json={"user": "Alex", "pinned": True}
        ).status_code
        == 404
    )
    for params in (
        {"limit": 101},
        {"offset": -1},
        {"sort": "title;DROP TABLE jobs"},
        {"stage": "new"},
        {"q": "a" * 301},
    ):
        assert (
            client.get("/api/vacancies", params={"user": "Alex", **params}).status_code
            == 422
        )
    assert (
        client.patch("/api/vacancies/1", json={"user": "Alex", "notes": None}).json()[
            "notes"
        ]
        is None
    )


def test_api_shared_token(library_client: TestClient) -> None:
    """Both new routes use the same configured dashboard authentication."""
    config.update_secrets({"dashboard_token": "fictional-test-token"})
    client = TestClient(create_app())
    assert client.get("/api/vacancies?user=Alex").status_code == 401
    assert (
        client.patch(
            "/api/vacancies/1", json={"user": "Alex", "pinned": True}
        ).status_code
        == 401
    )
    headers = {"Authorization": "Bearer fictional-test-token"}
    assert client.get("/api/vacancies?user=Alex", headers=headers).status_code == 200
    assert (
        client.patch(
            "/api/vacancies/1", json={"user": "Alex", "pinned": True}, headers=headers
        ).status_code
        == 200
    )
