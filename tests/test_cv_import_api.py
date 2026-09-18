"""The dashboard imports an applicant's own CV into CV Builder."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import job_scout.config as config
import job_scout.web.cv_import as cv_import
from job_scout.cv.models import CVDocument, ExperienceEntry, ExperienceSection
from job_scout.cv.storage import ProfileStore
from job_scout.cv_parser import compute_cv_hash
from job_scout.database import Database
from tests.helpers import FakeLLMClient

USER = "sanne"
CV_TEXT = (
    "Sanne Voorbeeld\nWerkervaring\n"
    "Communicatieadviseur, Echte Werkgever B.V., 2021 - heden\n"
)
REPLY = json.dumps(
    {
        "full_name": "Sanne Voorbeeld",
        "experience": [
            {
                "title": "Communicatieadviseur",
                "organisation": "Echte Werkgever B.V.",
                "period": "2021 - heden",
            }
        ],
    }
)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """The import router on its own, with a user whose Settings hold a CV."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.yaml")
    config.write_global_config({"llm_provider": "local"})
    cv_file = tmp_path / "CV Sanne Voorbeeld 2026.pdf"
    cv_file.write_bytes(b"%PDF")
    config.save_user_config(USER, {"cv_path": str(cv_file)})
    monkeypatch.setattr(cv_import, "parse_cv", lambda _path: CV_TEXT)
    fake = FakeLLMClient([REPLY])
    monkeypatch.setattr(cv_import, "get_llm_client", lambda _config: fake)
    app = FastAPI()
    app.include_router(cv_import.build_cv_import_router(), prefix="/api/cv")
    test_client = TestClient(app)
    test_client.fake = fake  # type: ignore[attr-defined]
    return test_client


def _store() -> ProfileStore:
    return ProfileStore(config.user_cv_dir(USER))


def test_options_name_the_settings_cv(client: TestClient) -> None:
    body = client.get(f"/api/cv/import/options?user={USER}").json()

    assert body == {"settings_cv": "CV Sanne Voorbeeld 2026.pdf", "linkedin": False}


def test_the_settings_cv_is_imported_as_a_new_profile(client: TestClient) -> None:
    response = client.post(
        f"/api/cv/import?user={USER}",
        data={"source": "settings", "language": "NL", "name": "Mijn CV"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["slug"] == "mijn-cv"
    assert body["warnings"] == []
    assert body["sources_used"] == ["your uploaded CV (CV Sanne Voorbeeld 2026.pdf)"]
    assert _store().load("mijn-cv").full_name == "Sanne Voorbeeld"


def test_a_linkedin_import_is_offered_to_the_model(client: TestClient) -> None:
    Database(config.user_db_path(USER)).save_cv_profile_cache(
        compute_cv_hash(CV_TEXT),
        json.dumps({"past_roles": [{"title": "Stagiair", "company": "LinkedIn B.V."}]}),
    )

    body = client.post(
        f"/api/cv/import?user={USER}",
        data={"source": "settings", "language": "NL", "name": "met-linkedin"},
    ).json()

    assert "LinkedIn B.V." in client.fake.calls[0][0]  # type: ignore[attr-defined]
    assert len(body["sources_used"]) == 2


def test_an_uploaded_file_is_imported(client: TestClient) -> None:
    response = client.post(
        f"/api/cv/import?user={USER}",
        data={"source": "upload", "language": "EN", "name": "upload"},
        files={"file": ("cv.txt", CV_TEXT.encode(), "text/plain")},
    )

    assert response.status_code == 200
    assert response.json()["sources_used"] == ["the uploaded file (cv.txt)"]


def test_an_empty_profile_is_filled_but_a_written_one_is_never_overwritten(
    client: TestClient,
) -> None:
    store = _store()
    store.save("leeg", CVDocument())
    written = CVDocument(
        full_name="Someone",
        main=[ExperienceSection(entries=[ExperienceEntry(organisation="Kept B.V.")])],
    )
    store.save("bestaand", written)

    def post(name: str) -> int:
        return client.post(
            f"/api/cv/import?user={USER}",
            data={"source": "settings", "language": "NL", "name": name},
        ).status_code

    assert post("leeg") == 200
    assert post("bestaand") == 409
    assert store.load("bestaand") == written


def test_an_unknown_user_is_refused(client: TestClient) -> None:
    assert client.get("/api/cv/import/options?user=nobody").status_code == 400


def test_the_dashboard_serves_the_import_routes(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The editor shows its Import button only when this route answers."""
    from job_scout.web.app import create_app  # noqa: PLC0415

    dashboard = TestClient(create_app())

    response = dashboard.get(f"/api/cv/import/options?user={USER}")

    assert response.status_code == 200
    assert response.json()["settings_cv"] == "CV Sanne Voorbeeld 2026.pdf"
