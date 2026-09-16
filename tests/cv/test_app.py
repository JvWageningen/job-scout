"""HTTP API behaviour, standalone and mounted inside job-scout's dashboard."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient

import job_scout.config
from job_scout.cv.app import STATIC_DIR, _download_name
from job_scout.cv.models import CVDocument
from job_scout.cv.sample import sample_cv
from job_scout.cv.storage import ProfileStore
from job_scout.web.app import create_app as create_dashboard
from tests.cv.conftest import make_image

# The seeded profiles are the bundled sample, so the identity the API reports
# back is whatever sample.py ships. Read it from there rather than repeating it:
# these tests are about the plumbing, and a change of sample person must not
# read as an API regression.
SAMPLE_NAME = sample_cv("EN").full_name

# The dashboard fixture's users. Two of them, because the mounted API is
# multi-user and one user's profiles must never appear in another's list.
MOUNTED_USER = "mounted-tester"
OTHER_USER = "someone-else"


@pytest.fixture
def dashboard_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point job-scout's config at a throwaway data directory with two users.

    Args:
        tmp_path: Pytest-provided temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        The temporary data directory.
    """
    monkeypatch.setattr(job_scout.config, "DATA_DIR", tmp_path)
    # A token in the developer's own environment would otherwise 401 every
    # request these tests make without one.
    monkeypatch.delenv("JOB_SCOUT_DASHBOARD_TOKEN", raising=False)
    for name in (MOUNTED_USER, OTHER_USER):
        (tmp_path / "users" / name).mkdir(parents=True)
    return tmp_path


@pytest.fixture
def dashboard(dashboard_data: Path) -> Iterator[TestClient]:
    """A client for the job-scout dashboard, with the CV API mounted.

    Args:
        dashboard_data: The temporary data directory fixture.

    Yields:
        A test client for the dashboard application.
    """
    with TestClient(create_dashboard()) as test_client:
        yield test_client


def test_index_serves_the_dashboard(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "CV Builder" in response.text
    assert "/static/app.js" in response.text


def test_static_assets_are_served(client: TestClient) -> None:
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/style.css").status_code == 200


def test_dashboard_assets_must_be_revalidated(client: TestClient) -> None:
    """Otherwise an edited app.js keeps serving from cache until a hard refresh."""
    assert client.get("/static/app.js").headers["cache-control"] == "no-cache"
    assert client.get("/").headers["cache-control"] == "no-cache"


def test_api_responses_are_not_marked_no_cache(client: TestClient) -> None:
    assert "cache-control" not in client.get("/api/meta").headers


def test_meta_describes_the_vocabulary(client: TestClient) -> None:
    body = client.get("/api/meta").json()
    assert "envelope" in body["icons"]
    assert "experience" in body["section_kinds"]
    assert body["theme_defaults"]["sidebar_bg"] == "#0B3D2C"


def test_first_listing_seeds_both_language_profiles(client: TestClient) -> None:
    profiles = client.get("/api/profiles").json()
    assert [p["slug"] for p in profiles] == ["default", "nederlands"]
    assert all(p["full_name"] == SAMPLE_NAME for p in profiles)
    assert client.get("/api/profiles/default").json()["language"] == "EN"
    assert client.get("/api/profiles/nederlands").json()["language"] == "NL"


def test_get_profile_returns_the_document(client: TestClient) -> None:
    client.get("/api/profiles")
    body = client.get("/api/profiles/default").json()
    assert body["full_name"] == SAMPLE_NAME
    assert any(section["kind"] == "experience" for section in body["main"])


def test_get_unknown_profile_is_404(client: TestClient) -> None:
    assert client.get("/api/profiles/nope").status_code == 404


def test_save_round_trips(client: TestClient) -> None:
    client.get("/api/profiles")
    doc = client.get("/api/profiles/default").json()
    doc["full_name"] = "Renamed Person"
    assert client.put("/api/profiles/default", json=doc).status_code == 200
    assert client.get("/api/profiles/default").json()["full_name"] == "Renamed Person"


def test_save_rejects_an_invalid_document(client: TestClient) -> None:
    client.get("/api/profiles")
    doc = client.get("/api/profiles/default").json()
    doc["theme"]["accent"] = "chartreuse"
    assert client.put("/api/profiles/default", json=doc).status_code == 422


def test_save_rejects_an_unknown_section_kind(client: TestClient) -> None:
    client.get("/api/profiles")
    doc = client.get("/api/profiles/default").json()
    doc["main"].append({"kind": "wat", "title": "Nope"})
    assert client.put("/api/profiles/default", json=doc).status_code == 422


def test_create_blank_profile(client: TestClient) -> None:
    response = client.post("/api/profiles", json={"name": "Second CV"})
    assert response.status_code == 201
    assert response.json()["slug"] == "second-cv"
    doc = client.get("/api/profiles/second-cv").json()
    assert doc["full_name"] == "Second CV"
    assert doc["main"][0]["kind"] == "text"


def test_create_seeded_profile(client: TestClient) -> None:
    client.post("/api/profiles", json={"name": "Copy", "seed": True})
    doc = client.get("/api/profiles/copy").json()
    experience = next(s for s in doc["main"] if s["kind"] == "experience")
    seeded = next(s for s in sample_cv("EN").main if s.kind == "experience")
    assert len(experience["entries"]) == len(seeded.entries) == 5
    assert experience["entries"][0]["organisation"] == seeded.entries[0].organisation


def test_duplicate_profile_is_rejected(client: TestClient) -> None:
    client.post("/api/profiles", json={"name": "Dup"})
    assert client.post("/api/profiles", json={"name": "Dup"}).status_code == 409


def test_unusable_profile_name_is_rejected(client: TestClient) -> None:
    assert client.post("/api/profiles", json={"name": "!!!"}).status_code == 400


def test_delete_profile(client: TestClient) -> None:
    client.post("/api/profiles", json={"name": "Temp"})
    assert client.delete("/api/profiles/temp").status_code == 204
    assert client.get("/api/profiles/temp").status_code == 404


def test_delete_unknown_profile_is_404(client: TestClient) -> None:
    assert client.delete("/api/profiles/ghost").status_code == 404


def test_pdf_download_has_attachment_headers(client: TestClient) -> None:
    client.get("/api/profiles")
    response = client.get("/api/profiles/default/pdf")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert "attachment" in response.headers["content-disposition"]
    assert response.content.startswith(b"%PDF")


def test_download_filename_carries_the_date_and_language() -> None:
    doc = sample_cv("EN")
    assert _download_name(doc, date(2026, 8, 9)) == f"20260809 {SAMPLE_NAME} CV EN.pdf"
    assert (
        _download_name(sample_cv("NL"), date(2026, 12, 31))
        == f"20261231 {SAMPLE_NAME} CV NL.pdf"
    )


def test_download_filename_date_is_zero_padded() -> None:
    doc = sample_cv("EN")
    assert _download_name(doc, date(2026, 1, 5)).startswith("20260105 ")


def test_download_filename_survives_missing_pieces() -> None:
    doc = CVDocument(full_name="", language="")
    name = _download_name(doc, date(2026, 8, 9))
    assert name == "20260809 CV.pdf"


def test_live_download_filename_matches_today(client: TestClient) -> None:
    client.get("/api/profiles")
    disposition = client.get("/api/profiles/default/pdf").headers["content-disposition"]
    stamp = date.today().strftime("%Y%m%d")
    assert f'filename="{stamp} {SAMPLE_NAME} CV EN.pdf"' in disposition


def test_dutch_profile_downloads_with_its_own_name(client: TestClient) -> None:
    client.get("/api/profiles")
    disposition = client.get("/api/profiles/nederlands/pdf").headers[
        "content-disposition"
    ]
    assert " CV NL.pdf" in disposition


def test_download_filename_is_ascii_safe(client: TestClient) -> None:
    client.post("/api/profiles", json={"name": "Zoe"})
    doc = client.get("/api/profiles/zoe").json()
    doc["full_name"] = "Zoë Müller / ../etc"
    client.put("/api/profiles/zoe", json=doc)
    disposition = client.get("/api/profiles/zoe/pdf").headers["content-disposition"]
    assert "/" not in disposition.split("filename=")[1]
    disposition.encode("ascii")


def test_preview_renders_unsaved_edits(client: TestClient) -> None:
    client.get("/api/profiles")
    doc = client.get("/api/profiles/default").json()
    doc["full_name"] = "Preview Only Name"

    response = client.post("/api/profiles/default/preview", json=doc)
    assert response.status_code == 200
    assert "inline" in response.headers["content-disposition"]
    with pymupdf.open(stream=response.content, filetype="pdf") as document:
        assert "P R E V I E W" in document[0].get_text()

    # The edit must not have been persisted as a side effect.
    assert (
        client.get("/api/profiles/default").json()["full_name"] != "Preview Only Name"
    )


def test_preview_rejects_an_invalid_document(client: TestClient) -> None:
    client.get("/api/profiles")
    assert (
        client.post(
            "/api/profiles/default/preview", json={"theme": {"accent": "x"}}
        ).status_code
        == 422
    )


def test_photo_upload_download_and_delete(client: TestClient) -> None:
    client.post("/api/profiles", json={"name": "Photo"})
    assert client.get("/api/profiles/photo/photo").status_code == 404

    response = client.post(
        "/api/profiles/photo/photo",
        files={"file": ("me.png", make_image(300, 200, (9, 9, 9)), "image/png")},
    )
    assert response.status_code == 200
    assert response.json()["photo"] == "portrait.png"

    fetched = client.get("/api/profiles/photo/photo")
    assert fetched.status_code == 200
    assert fetched.headers["content-type"] == "image/png"
    assert client.get("/api/profiles/photo").json()["photo"] == "portrait.png"

    assert client.delete("/api/profiles/photo/photo").status_code == 204
    assert client.get("/api/profiles/photo/photo").status_code == 404
    assert client.get("/api/profiles/photo").json()["photo"] == ""


def test_photo_upload_rejects_non_images(client: TestClient) -> None:
    client.post("/api/profiles", json={"name": "Bad"})
    response = client.post(
        "/api/profiles/bad/photo",
        files={"file": ("x.png", b"not an image at all", "image/png")},
    )
    assert response.status_code == 400


def test_photo_upload_rejects_empty_files(client: TestClient) -> None:
    client.post("/api/profiles", json={"name": "Empty"})
    response = client.post(
        "/api/profiles/empty/photo", files={"file": ("x.png", b"", "image/png")}
    )
    assert response.status_code == 400


def test_uploaded_photo_reaches_the_pdf(client: TestClient) -> None:
    client.post("/api/profiles", json={"name": "With Photo"})
    client.post(
        "/api/profiles/with-photo/photo",
        files={"file": ("me.png", make_image(300, 300, (200, 30, 30)), "image/png")},
    )
    response = client.get("/api/profiles/with-photo/pdf")
    with pymupdf.open(stream=response.content, filetype="pdf") as document:
        assert document[0].get_images()


def test_unreadable_profile_is_skipped_in_listing(
    client: TestClient, store: ProfileStore
) -> None:
    client.get("/api/profiles")
    (store.profiles_dir / "broken").mkdir(parents=True, exist_ok=True)
    (store.profiles_dir / "broken" / "cv.json").write_text("{oops", encoding="utf-8")
    slugs = [p["slug"] for p in client.get("/api/profiles").json()]
    assert "broken" not in slugs
    assert "default" in slugs


# -- mounted inside job-scout's dashboard ---------------------------------


def test_mounted_api_answers_under_the_cv_prefix(dashboard: TestClient) -> None:
    profiles = dashboard.get(f"/api/cv/profiles?user={MOUNTED_USER}").json()
    assert [p["slug"] for p in profiles] == ["default", "nederlands"]
    assert all(p["full_name"] == SAMPLE_NAME for p in profiles)


def test_mounted_api_does_not_leak_onto_the_flat_prefix(dashboard: TestClient) -> None:
    """The dashboard owns /api/profiles; the CV API must stay under /api/cv."""
    assert dashboard.get(f"/api/profiles?user={MOUNTED_USER}").status_code == 404


def test_mounted_api_writes_into_the_users_own_cv_directory(
    dashboard: TestClient, dashboard_data: Path
) -> None:
    response = dashboard.post(
        f"/api/cv/profiles?user={MOUNTED_USER}", json={"name": "Mounted CV"}
    )
    assert response.status_code == 201
    document = (
        dashboard_data
        / "users"
        / MOUNTED_USER
        / "cv"
        / "profiles"
        / "mounted-cv"
        / "cv.json"
    )
    assert document.is_file()


def test_mounted_api_keeps_users_apart(dashboard: TestClient) -> None:
    dashboard.post(f"/api/cv/profiles?user={MOUNTED_USER}", json={"name": "Only Mine"})
    slugs = [
        p["slug"] for p in dashboard.get(f"/api/cv/profiles?user={OTHER_USER}").json()
    ]
    assert "only-mine" not in slugs
    assert (
        dashboard.get(f"/api/cv/profiles/only-mine?user={OTHER_USER}").status_code
        == 404
    )
    assert (
        dashboard.get(f"/api/cv/profiles/only-mine?user={MOUNTED_USER}").status_code
        == 200
    )


def test_mounted_api_requires_a_user(dashboard: TestClient) -> None:
    assert dashboard.get("/api/cv/profiles").status_code == 400


def test_mounted_api_rejects_an_unknown_user(dashboard: TestClient) -> None:
    assert dashboard.get("/api/cv/profiles?user=nobody").status_code == 404


def test_mounted_api_renders_a_pdf(dashboard: TestClient) -> None:
    dashboard.get(f"/api/cv/profiles?user={MOUNTED_USER}")
    response = dashboard.get(f"/api/cv/profiles/default/pdf?user={MOUNTED_USER}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")


def test_mounted_api_uploads_a_portrait(dashboard: TestClient) -> None:
    dashboard.post(f"/api/cv/profiles?user={MOUNTED_USER}", json={"name": "Portrait"})
    response = dashboard.post(
        f"/api/cv/profiles/portrait/photo?user={MOUNTED_USER}",
        files={"file": ("me.png", make_image(300, 200, (9, 9, 9)), "image/png")},
    )
    assert response.status_code == 200
    assert response.json()["photo"] == "portrait.png"
    assert (
        dashboard.get(
            f"/api/cv/profiles/portrait/photo?user={MOUNTED_USER}"
        ).status_code
        == 200
    )


def test_mounted_api_inherits_the_dashboard_token(
    dashboard_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point of mounting under /api/: TokenAuthMiddleware covers it."""
    monkeypatch.setenv("JOB_SCOUT_DASHBOARD_TOKEN", "s3cret")
    with TestClient(create_dashboard()) as client:
        unauthenticated = client.get(f"/api/cv/profiles?user={MOUNTED_USER}")
        assert unauthenticated.status_code == 401
        authorised = client.get(
            f"/api/cv/profiles?user={MOUNTED_USER}",
            headers={"Authorization": "Bearer s3cret"},
        )
        assert authorised.status_code == 200


def test_editor_page_loads_without_a_token(
    dashboard_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The page sits outside /api/ and is inert until its API calls succeed."""
    monkeypatch.setenv("JOB_SCOUT_DASHBOARD_TOKEN", "s3cret")
    with TestClient(create_dashboard()) as client:
        assert client.get("/cv/").status_code == 200
        assert client.get("/cv/app.js").status_code == 200


def test_dashboard_serves_the_editor_page(dashboard: TestClient) -> None:
    response = dashboard.get("/cv/")
    assert response.status_code == 200
    assert "CV Builder" in response.text
    # The vendored markup links /static/...; under the dashboard those paths
    # belong to nobody, so every one of them must have been rewritten.
    assert "/static/" not in response.text
    assert '"/cv/app.js"' in response.text
    assert '"/cv/style.css"' in response.text
    assert 'window.CV_API_BASE = "/api/cv"' in response.text


def test_dashboard_serves_the_editor_assets(dashboard: TestClient) -> None:
    for path in ("/cv/app.js", "/cv/style.css", "/cv/favicon.svg"):
        assert dashboard.get(path).status_code == 200, path


def test_editor_assets_must_be_revalidated(dashboard: TestClient) -> None:
    """As standalone: an edited app.js must not keep serving from cache."""
    assert "no-cache" in dashboard.get("/cv/app.js").headers["cache-control"]
    assert "no-cache" in dashboard.get("/cv/").headers["cache-control"]


def test_editor_script_routes_every_call_through_the_api_base() -> None:
    """The front end must have no hard-coded /api path left to mount around."""
    source = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    assert 'const API_BASE = window.CV_API_BASE || "/api";' in source
    for hardcoded in ('"/api/profiles', "`/api/profiles", '"/api/meta'):
        assert hardcoded not in source, hardcoded
