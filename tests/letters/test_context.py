"""The letter writer offers vacancies worth applying to, best match first."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import job_scout.config as config
from job_scout.database import Database
from job_scout.models import ApplicationStage, JobListing, JobStatus
from job_scout.web.app import create_app
from job_scout.web.vacancies import VacancyUpdate, update_vacancy

USER = "Alex"


def add_job(db: Database, number: int, **values: object) -> int:
    """Insert a vacancy that is usable for a letter unless a test says otherwise."""
    data: dict[str, object] = dict(
        title=f"Engineer {number}",
        company="Example Instruments",
        url=f"https://example.org/jobs/{number}",
        source="board",
        description="A real description, without which no letter can be written.",
        fit_score=number,
        status=JobStatus.MATCHED,
        seen_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    data.update(values)
    return db.save_job(JobListing.model_validate(data))


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A single user whose database the individual tests fill."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.yaml")
    config.write_global_config({"llm_provider": "local"})
    config.save_user_config(USER, {})
    return TestClient(create_app())


def _titles(client: TestClient) -> list[str]:
    response = client.get(f"/api/letters/context?user={USER}")
    assert response.status_code == 200
    return [job["title"] for job in response.json()["jobs"]]


def test_vacancies_are_offered_best_match_first(client: TestClient) -> None:
    """The strongest match should be the first thing in the dropdown."""
    db = Database(config.user_db_path(USER))
    for score in (41, 93, 67):
        add_job(db, score)

    assert _titles(client) == ["Engineer 93", "Engineer 67", "Engineer 41"]


@pytest.mark.parametrize(
    ("label", "status"),
    [
        ("rejected by the pipeline", JobStatus.REJECTED),
        ("no longer advertised", JobStatus.EXPIRED),
    ],
)
def test_vacancies_the_pipeline_ruled_out_are_left_out(
    client: TestClient, label: str, status: JobStatus
) -> None:
    """Listing every vacancy ever seen buried the few that still matter."""
    db = Database(config.user_db_path(USER))
    add_job(db, 80, title="Still open")
    add_job(db, 90, title=f"Excluded: {label}", status=status)

    assert _titles(client) == ["Still open"]


def test_a_vacancy_the_applicant_closed_is_left_out(client: TestClient) -> None:
    """Closing is an applicant action, so it is made the way a person makes it.

    save_job deliberately will not write applicant-owned fields, which is the
    whole point of separating them from what the pipeline decides.
    """
    db = Database(config.user_db_path(USER))
    add_job(db, 80, title="Still open")
    closed = add_job(db, 90, title="Closed by the applicant")
    update_vacancy(db, closed, VacancyUpdate(user=USER, stage=ApplicationStage.CLOSED))

    assert _titles(client) == ["Still open"]


def test_a_vacancy_without_a_description_is_left_out(client: TestClient) -> None:
    """There is nothing to tailor a letter against without the posting text."""
    db = Database(config.user_db_path(USER))
    add_job(db, 80, title="Has a description")
    add_job(db, 95, title="No description", description=None)

    assert _titles(client) == ["Has a description"]


def test_the_score_is_returned_so_the_ordering_is_visible(client: TestClient) -> None:
    """An ordered list is only obviously ordered if the key is on screen."""
    add_job(Database(config.user_db_path(USER)), 88)

    job = client.get(f"/api/letters/context?user={USER}").json()["jobs"][0]
    assert job["fit_score"] == 88


def test_one_users_vacancies_never_reach_another(
    client: TestClient, tmp_path: Path
) -> None:
    """Two applicants share a dashboard; their shortlists must not mix."""
    config.save_user_config("Sam", {})
    add_job(Database(config.user_db_path(USER)), 70, title="Alex only")
    add_job(Database(config.user_db_path("Sam")), 99, title="Sam only")

    assert _titles(client) == ["Alex only"]
    assert [
        j["title"] for j in client.get("/api/letters/context?user=Sam").json()["jobs"]
    ] == ["Sam only"]
