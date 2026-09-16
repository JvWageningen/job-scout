"""Reaching the tailoring bridge: the 'cv tailor' command and its endpoint.

:mod:`job_scout.cv.tailor` is tested on its own in ``test_tailor.py``. These
tests are about the two surfaces that expose it - that they find the right
profile, the right vacancy and the right LLM client, that they save the result
beside the original instead of over it, and that every failure arrives as a
message rather than a traceback or a 500.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner, Result
from fastapi.testclient import TestClient

import job_scout.config
import job_scout.cv.cli
import job_scout.web.app
from job_scout.cli import cli
from job_scout.config import user_cv_dir, user_db_path
from job_scout.cv.models import (
    CVDocument,
    ExperienceEntry,
    ExperienceSection,
    SkillItem,
    SkillsSection,
    TextSection,
)
from job_scout.cv.storage import ProfileStore
from job_scout.database import Database
from job_scout.llm.base import LLMError
from job_scout.models import JobListing
from job_scout.web.app import create_app as create_dashboard
from tests.cv.conftest import make_image
from tests.helpers import FakeLLMClient

USER = "tester"
SOURCE_SLUG = "default"

# What tailored_slug() builds from ("default", "Meridiaan Data"). Hard-coded
# rather than computed, so these tests pin the slug a person actually ends up
# with instead of asking the code under test what it thinks the answer is.
TAILORED_SLUG = "default-meridiaan-data"

KEYWORDS = '{"keywords": ["Python", "SQL"]}'
NEW_PROFILE_BODY = "Data engineer who builds Python and SQL pipelines."
PLAN = json.dumps(
    {
        "main": ["xp", "pf"],
        "sections": {"pf": {"body": NEW_PROFILE_BODY}},
    }
)


def make_doc() -> CVDocument:
    """A small CV with stable ids, so :data:`PLAN` can name its sections.

    Returns:
        The source document every test tailors.
    """
    return CVDocument(
        full_name="Sam de Vries",
        headline="Data Engineer",
        sidebar=[
            SkillsSection(
                id="sk",
                title="Skills",
                items=[
                    SkillItem(id="s1", name="Python"),
                    SkillItem(id="s2", name="SQL"),
                ],
            )
        ],
        main=[
            TextSection(id="pf", title="Profile", body="Engineer with a terminal."),
            ExperienceSection(
                id="xp",
                title="Experience",
                entries=[
                    ExperienceEntry(
                        id="e1",
                        title="Data Engineer",
                        organisation="Beta NV",
                        period="2019 - 2025",
                        description="Built reporting pipelines.",
                        bullets=["Shipped an ETL stack"],
                    )
                ],
            ),
        ],
    )


def make_job() -> JobListing:
    """The vacancy every test tailors towards.

    Returns:
        The vacancy.
    """
    return JobListing(
        title="Data Engineer",
        company="Meridiaan Data",
        location="Utrecht",
        url="https://example.invalid/vacancy/1",
        source="test",
        description="We build Python and SQL data pipelines.",
    )


def fake_client(*responses: str) -> FakeLLMClient:
    """A client that answers the keyword call and then the tailoring call.

    Args:
        responses: Replies to give after keyword extraction. Passing none leaves
            the client with nothing to say, which is how these tests simulate a
            provider that will not answer.

    Returns:
        The fake client.
    """
    return FakeLLMClient([KEYWORDS, *responses], repeat_last=False)


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point job-scout at a throwaway data directory holding one user.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        The temporary data directory.
    """
    monkeypatch.setattr(job_scout.config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(job_scout.config, "CONFIG_PATH", tmp_path / "config.yaml")
    # A token in the developer's own environment would 401 every request the
    # endpoint tests make without one.
    monkeypatch.delenv("JOB_SCOUT_DASHBOARD_TOKEN", raising=False)
    (tmp_path / "users" / USER).mkdir(parents=True)
    return tmp_path


@pytest.fixture
def profiles(data_dir: Path) -> ProfileStore:
    """The user's CV store, holding one saved profile to tailor.

    Args:
        data_dir: The temporary data directory fixture.

    Returns:
        The store.
    """
    store = ProfileStore(user_cv_dir(USER))
    store.save(SOURCE_SLUG, make_doc())
    return store


@pytest.fixture
def job_id(data_dir: Path) -> int:
    """The database id of the vacancy under test.

    Args:
        data_dir: The temporary data directory fixture.

    Returns:
        The saved vacancy's id.
    """
    return Database(user_db_path(USER)).save_job(make_job())


@pytest.fixture
def dashboard(data_dir: Path) -> Iterator[TestClient]:
    """A client for the dashboard, with the CV API mounted.

    Args:
        data_dir: The temporary data directory fixture.

    Yields:
        A test client.
    """
    with TestClient(create_dashboard()) as test_client:
        yield test_client


def use_cli_client(monkeypatch: pytest.MonkeyPatch, client: FakeLLMClient) -> None:
    """Make the CV command build ``client`` instead of a real one.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        client: The client the command should use.
    """
    monkeypatch.setattr(job_scout.cv.cli, "get_llm_client", lambda config: client)


def use_web_client(monkeypatch: pytest.MonkeyPatch, client: FakeLLMClient) -> None:
    """Make the endpoint build ``client`` instead of a real one.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        client: The client the endpoint should use.
    """
    monkeypatch.setattr(job_scout.web.app, "get_llm_client", lambda config: client)


def explode(config: object) -> FakeLLMClient:
    """Fail the way the factory fails on a misconfigured provider.

    Args:
        config: The configuration the factory was handed.

    Returns:
        Never returns.

    Raises:
        LLMError: Always.
    """
    raise LLMError("zai_api_key is not set")


def run_tailor(*args: str) -> Result:
    """Invoke 'job-scout cv tailor' with the given arguments.

    Args:
        args: Arguments following 'cv tailor'.

    Returns:
        The click result.
    """
    return CliRunner().invoke(cli, ["cv", "tailor", *args])


def assert_clean_failure(result: Result, expected: str) -> None:
    """Assert a command failed with a message rather than a traceback.

    Args:
        result: The click result.
        expected: Text the message must contain.
    """
    assert result.exit_code == 1, result.output
    # sys.exit() rather than an escaped exception: anything else would have
    # printed a traceback at whoever ran the command.
    assert isinstance(result.exception, SystemExit), result.exception
    assert expected in result.output


# the CLI command
# ---------------------------------------------------------------------------


def test_tailor_is_listed_on_the_cv_group() -> None:
    """The bridge is discoverable next to serve, render and list."""
    result = CliRunner().invoke(cli, ["cv", "--help"])
    assert result.exit_code == 0
    assert "tailor" in result.output


def test_tailor_writes_a_new_profile_and_a_pdf(
    profiles: ProfileStore,
    job_id: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The happy path: a tailored profile on disk and a PDF beside it."""
    use_cli_client(monkeypatch, fake_client(PLAN))
    output = tmp_path / "out" / "tailored.pdf"

    result = run_tailor(str(job_id), "-o", str(output))

    assert result.exit_code == 0, result.output
    tailored = profiles.load(TAILORED_SLUG)
    assert [section.id for section in tailored.main] == ["xp", "pf"]
    profile_section = tailored.main[1]
    assert isinstance(profile_section, TextSection)
    assert profile_section.body == NEW_PROFILE_BODY
    assert output.read_bytes().startswith(b"%PDF")
    # It has to say what it made and where, or the new slug is a guess.
    assert TAILORED_SLUG in result.output
    assert str(output) in result.output


def test_tailor_leaves_the_source_profile_untouched(
    profiles: ProfileStore,
    job_id: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Tailoring is additive: the CV it was built from survives intact."""
    use_cli_client(monkeypatch, fake_client(PLAN))
    before = profiles.document_path(SOURCE_SLUG).read_bytes()

    result = run_tailor(str(job_id), "-o", str(tmp_path / "cv.pdf"))

    assert result.exit_code == 0, result.output
    assert profiles.document_path(SOURCE_SLUG).read_bytes() == before
    assert profiles.list_profiles() == [SOURCE_SLUG, TAILORED_SLUG]


def test_tailor_defaults_the_pdf_name_to_the_new_slug(
    profiles: ProfileStore, job_id: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without -o the PDF is named after the profile it renders."""
    use_cli_client(monkeypatch, fake_client(PLAN))
    runner = CliRunner()

    with runner.isolated_filesystem() as workspace:
        result = runner.invoke(cli, ["cv", "tailor", str(job_id)])
        assert result.exit_code == 0, result.output
        assert (Path(workspace) / f"{TAILORED_SLUG}.pdf").is_file()


def test_tailor_copies_the_portrait_into_the_new_profile(
    profiles: ProfileStore,
    job_id: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A profile owns its own uploads, so the face comes along with the copy."""
    doc = make_doc()
    doc.photo = profiles.save_photo(SOURCE_SLUG, make_image(300, 300, (20, 90, 160)))
    profiles.save(SOURCE_SLUG, doc)
    use_cli_client(monkeypatch, fake_client(PLAN))

    result = run_tailor(str(job_id), "-o", str(tmp_path / "cv.pdf"))

    assert result.exit_code == 0, result.output
    tailored = profiles.load(TAILORED_SLUG)
    assert tailored.photo == "portrait.png"
    assert profiles.photo_path(TAILORED_SLUG, tailored) is not None


def test_tailor_selects_the_profile_named_by_slug(
    profiles: ProfileStore,
    job_id: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--slug picks which CV is tailored, and the copy is named after it."""
    profiles.save("nederlands", make_doc())
    use_cli_client(monkeypatch, fake_client(PLAN))

    result = run_tailor(
        str(job_id), "--slug", "nederlands", "-o", str(tmp_path / "cv.pdf")
    )

    assert result.exit_code == 0, result.output
    assert "nederlands-meridiaan-data" in profiles.list_profiles()
    assert TAILORED_SLUG not in profiles.list_profiles()


def test_tailor_unknown_job_is_a_clean_error(
    profiles: ProfileStore, tmp_path: Path
) -> None:
    """An id with no vacancy behind it stops before anything is written."""
    result = run_tailor("4242", "-o", str(tmp_path / "cv.pdf"))

    assert_clean_failure(result, "4242")
    assert profiles.list_profiles() == [SOURCE_SLUG]


def test_tailor_unknown_profile_is_a_clean_error(
    profiles: ProfileStore, job_id: int, tmp_path: Path
) -> None:
    """A slug that names nothing is reported, not raised."""
    result = run_tailor(str(job_id), "--slug", "ghost", "-o", str(tmp_path / "cv.pdf"))

    assert_clean_failure(result, "ghost")
    assert not (tmp_path / "cv.pdf").exists()


def test_tailor_unknown_user_is_a_clean_error(
    profiles: ProfileStore, job_id: int, tmp_path: Path
) -> None:
    """A typo in --user names the users that do exist, as elsewhere."""
    result = run_tailor(str(job_id), "--user", "ghost", "-o", str(tmp_path / "cv.pdf"))

    assert_clean_failure(result, USER)


def test_tailor_llm_failure_is_a_clean_error(
    profiles: ProfileStore,
    job_id: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provider that will not answer is a message, not a stack trace."""
    use_cli_client(monkeypatch, fake_client())

    result = run_tailor(str(job_id), "-o", str(tmp_path / "cv.pdf"))

    assert_clean_failure(result, "Tailoring failed")
    assert profiles.list_profiles() == [SOURCE_SLUG]
    assert not (tmp_path / "cv.pdf").exists()


def test_tailor_misconfigured_llm_is_a_clean_error(
    profiles: ProfileStore,
    job_id: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing API key is reported the way every other command reports it."""
    monkeypatch.setattr(job_scout.cv.cli, "get_llm_client", explode)

    result = run_tailor(str(job_id), "-o", str(tmp_path / "cv.pdf"))

    assert_clean_failure(result, "zai_api_key is not set")


def test_tailor_unusable_plan_is_a_clean_error(
    profiles: ProfileStore,
    job_id: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A model that answers with prose leaves no half-written profile behind."""
    use_cli_client(monkeypatch, fake_client("I am afraid I cannot do that."))

    result = run_tailor(str(job_id), "-o", str(tmp_path / "cv.pdf"))

    assert_clean_failure(result, "Tailoring failed")
    assert profiles.list_profiles() == [SOURCE_SLUG]


# the endpoint
# ---------------------------------------------------------------------------


def tailor_url(slug: str = SOURCE_SLUG, **params: object) -> str:
    """Build the tailoring endpoint's URL.

    Args:
        slug: Profile slug in the path.
        params: Query parameters to append.

    Returns:
        The URL.
    """
    query = "&".join(f"{key}={value}" for key, value in params.items())
    return f"/api/cv/profiles/{slug}/tailor?{query}"


def test_endpoint_returns_the_new_slug(
    dashboard: TestClient,
    profiles: ProfileStore,
    job_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The response is the slug the editor should switch to."""
    use_web_client(monkeypatch, fake_client(PLAN))

    response = dashboard.post(tailor_url(user=USER, job_id=job_id))

    assert response.status_code == 200, response.text
    assert response.json() == {"slug": TAILORED_SLUG}


def test_endpoint_saves_into_the_users_own_cv_directory(
    dashboard: TestClient,
    profiles: ProfileStore,
    job_id: int,
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The tailored CV lands beside the original, in that user's own tree."""
    use_web_client(monkeypatch, fake_client(PLAN))
    before = profiles.document_path(SOURCE_SLUG).read_bytes()

    response = dashboard.post(tailor_url(user=USER, job_id=job_id))

    assert response.status_code == 200, response.text
    document = data_dir / "users" / USER / "cv" / "profiles" / TAILORED_SLUG / "cv.json"
    assert document.is_file()
    assert profiles.document_path(SOURCE_SLUG).read_bytes() == before
    tailored = profiles.load(TAILORED_SLUG)
    assert [section.id for section in tailored.main] == ["xp", "pf"]


def test_endpoint_copies_the_portrait(
    dashboard: TestClient,
    profiles: ProfileStore,
    job_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The tailored profile renders with the same face as the one it copied."""
    doc = make_doc()
    doc.photo = profiles.save_photo(SOURCE_SLUG, make_image(300, 300, (20, 90, 160)))
    profiles.save(SOURCE_SLUG, doc)
    use_web_client(monkeypatch, fake_client(PLAN))

    response = dashboard.post(tailor_url(user=USER, job_id=job_id))

    assert response.status_code == 200, response.text
    tailored = profiles.load(TAILORED_SLUG)
    assert profiles.photo_path(TAILORED_SLUG, tailored) is not None


def test_endpoint_requires_a_user(dashboard: TestClient, job_id: int) -> None:
    """Which CV to tailor is ambiguous without a user, as everywhere else."""
    response = dashboard.post(f"/api/cv/profiles/{SOURCE_SLUG}/tailor?job_id={job_id}")

    assert response.status_code == 400
    assert "User" in response.json()["detail"]


def test_endpoint_rejects_an_unknown_user(dashboard: TestClient, job_id: int) -> None:
    """An unknown user is a 404, not an empty profile store."""
    response = dashboard.post(tailor_url(user="nobody", job_id=job_id))

    assert response.status_code == 404


def test_endpoint_requires_a_job(dashboard: TestClient, profiles: ProfileStore) -> None:
    """There is nothing to tailor towards without a vacancy."""
    response = dashboard.post(tailor_url(user=USER))

    assert response.status_code == 400
    assert response.json()["detail"]


def test_endpoint_rejects_an_unknown_job(
    dashboard: TestClient, profiles: ProfileStore
) -> None:
    """An id with no vacancy behind it is a 404 naming the id."""
    response = dashboard.post(tailor_url(user=USER, job_id=4242))

    assert response.status_code == 404
    assert "4242" in response.json()["detail"]


def test_endpoint_rejects_an_unknown_profile(
    dashboard: TestClient, profiles: ProfileStore, job_id: int
) -> None:
    """A slug that names no profile is a 404, matching the rest of the CV API."""
    response = dashboard.post(tailor_url("ghost", user=USER, job_id=job_id))

    assert response.status_code == 404
    assert "ghost" in response.json()["detail"]


def test_endpoint_reports_a_misconfigured_llm(
    dashboard: TestClient,
    profiles: ProfileStore,
    job_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing API key is the caller's to fix, so 400 with the reason."""
    monkeypatch.setattr(job_scout.web.app, "get_llm_client", explode)

    response = dashboard.post(tailor_url(user=USER, job_id=job_id))

    assert response.status_code == 400
    assert "zai_api_key" in response.json()["detail"]


def test_endpoint_reports_a_model_failure(
    dashboard: TestClient,
    profiles: ProfileStore,
    job_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A model that answers with prose is an upstream failure, not a 500."""
    use_web_client(monkeypatch, fake_client("I am afraid I cannot do that."))

    response = dashboard.post(tailor_url(user=USER, job_id=job_id))

    assert response.status_code == 502
    assert "Tailoring failed" in response.json()["detail"]
    assert profiles.list_profiles() == [SOURCE_SLUG]


def test_endpoint_reports_an_unreachable_model(
    dashboard: TestClient,
    profiles: ProfileStore,
    job_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provider that will not answer is also a 502, never a traceback."""
    use_web_client(monkeypatch, fake_client())

    response = dashboard.post(tailor_url(user=USER, job_id=job_id))

    assert response.status_code == 502
    assert profiles.list_profiles() == [SOURCE_SLUG]
