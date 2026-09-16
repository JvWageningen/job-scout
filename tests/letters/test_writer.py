"""End-to-end letter contracts using fictional private data and a fake model."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PyPDF2 import PdfReader

import job_scout.config as config
from job_scout.cv.models import CVDocument, TextSection
from job_scout.cv.storage import ProfileStore
from job_scout.database import Database
from job_scout.letters.examples import (
    ExampleError,
    add_example,
    extract_text,
    list_examples,
)
from job_scout.letters.models import LetterLanguage, LetterRequest, WarningKind
from job_scout.letters.style import save_style_guide
from job_scout.letters.writer import (
    LetterError,
    letter_pdf_bytes,
    load_letter,
    save_letter,
    select_cv,
    write_letter,
)
from job_scout.models import JobListing
from job_scout.web.app import create_app
from tests.helpers import FakeLLMClient


@pytest.fixture
def private_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> int:
    """Make two users with distinct current CVs and one vacancy."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.yaml")
    config.write_global_config({"llm_provider": "local"})
    for user in ("Alex", "Sam"):
        config.save_user_config(user, {})
        for slug, language in (("default", "EN"), ("nederlands", "NL")):
            ProfileStore(config.user_cv_dir(user)).save(
                slug,
                CVDocument(
                    full_name=f"{user} Example",
                    language=language,
                    main=[
                        TextSection(
                            title="Experience",
                            body=(
                                f"{user} works at CurrentWorks since 2025. "
                                "Earlier: OldWorks, 2020-2024. Incomplete MSc."
                            ),
                        )
                    ],
                ),
            )
    return Database(config.user_db_path("Alex")).save_job(
        JobListing(
            title="Engineer",
            company="TargetWorks",
            url="https://example.org/vacancy",
            source="test",
            description=(
                "Wij zoeken een collega die met de techniek werkt. "
                "Je gaat in het team aan de slag en bent verantwoordelijk "
                "voor de kwaliteit."
            ),
        )
    )


def fake() -> FakeLLMClient:
    """Return a reproducible structured letter body."""
    return FakeLLMClient(
        [
            json.dumps(
                {
                    "paragraphs": [
                        "Ik solliciteer op de functie van Engineer.",
                        "Bij CurrentWorks werk ik aan technische vraagstukken.",
                        "Ik bespreek graag wat uw team nodig heeft.",
                    ]
                }
            )
        ]
    )


def test_current_cv_language_and_source_separation(private_data: int) -> None:
    """Historical employers remain style sources, never current CV facts."""
    add_example(
        "Alex",
        "past.txt",
        b"Beste team, ik werk bij ObsoleteCompany "
        b"en ik zoek een nieuwe functie in de techniek.",
    )
    save_style_guide("Alex", "Write directly; use concrete evidence.")
    client = fake()
    letter = write_letter(
        "Alex", LetterRequest(job_id=private_data), client, today=date(2026, 9, 16)
    )
    assert letter.language is LetterLanguage.NL
    assert letter.cv_slug == "nederlands"
    assert "16 september 2026" in letter.place_date
    assert letter.signature == "Alex Example"
    prompt, purpose = client.calls[0]
    assert purpose == "cover_letter"
    sources = json.loads(prompt[prompt.index('{"current_cv_facts"') :])
    assert "CurrentWorks" in json.dumps(sources["current_cv_facts"])
    assert "ObsoleteCompany" not in json.dumps(sources["current_cv_facts"])
    assert "ObsoleteCompany" in sources["style_examples_NOT_facts"][0]
    assert "Sam" not in prompt
    assert "never reuse that chronology" in prompt
    assert load_letter("Alex", private_data, LetterLanguage.NL) is None
    english = write_letter(
        "Alex", LetterRequest(job_id=private_data, language="en"), fake()
    )
    assert english.cv_slug == "default"
    assert english.closing == "Best regards,"


def test_saved_drafts_and_invalid_response(private_data: int) -> None:
    """Generation cannot destroy a saved draft; language versions stay separate."""
    dutch = write_letter("Alex", LetterRequest(job_id=private_data), fake())
    save_letter("Alex", dutch)
    english = write_letter(
        "Alex", LetterRequest(job_id=private_data, language="en"), fake()
    )
    save_letter("Alex", english)
    with pytest.raises(LetterError):
        write_letter(
            "Alex",
            LetterRequest(job_id=private_data),
            FakeLLMClient(['{"paragraphs": [']),
        )
    assert load_letter("Alex", private_data, LetterLanguage.NL) == dutch
    assert load_letter("Alex", private_data, LetterLanguage.EN) == english
    assert load_letter("Sam", private_data, LetterLanguage.NL) is None
    assert (
        Database(config.user_db_path("Alex")).get_cover_letter(private_data)
        == english.as_plain_text()
    )


def test_live_cv_and_explicit_choice(private_data: int) -> None:
    """Read saved CV edits on each generation and respect an explicit profile."""
    store = ProfileStore(config.user_cv_dir("Alex"))
    doc = store.load("default")
    doc.main = [TextSection(title="Experience", body="Now employed by UpdatedWorks.")]
    store.save("default", doc)
    client = fake()
    result = write_letter(
        "Alex",
        LetterRequest(job_id=private_data, language="nl", cv_slug="default"),
        client,
    )
    assert "UpdatedWorks" in client.calls[0][0]
    assert result.cv_slug == "default"
    assert WarningKind.CV_FALLBACK in [w.kind for w in result.warnings]
    with pytest.raises(LetterError):
        select_cv("Alex", LetterLanguage.NL, "../../Sam")
    with pytest.raises(LetterError):
        select_cv("../Alex", LetterLanguage.NL)


def test_example_leak_warning(private_data: int) -> None:
    """A recognisable example-only employer is flagged for human review."""
    add_example(
        "Alex",
        "old.txt",
        b"Beste team, ik werk voor GhostEmployer en ik heb ervaring in de techniek.",
    )
    client = FakeLLMClient(['{"paragraphs": ["Ik werk bij GhostEmployer."]}'])
    result = write_letter("Alex", LetterRequest(job_id=private_data), client)
    assert any(w.kind == WarningKind.EXAMPLE_LEAK for w in result.warnings)


@pytest.mark.parametrize(
    "name",
    [
        "../x.txt",
        "a/b.txt",
        "a\\b.txt",
        "a:stream.txt",
        "CON.txt",
        "a?.txt",
        ".secret.txt",
        "x.exe",
    ],
)
def test_unsafe_example_names(name: str) -> None:
    """An upload name cannot escape storage or address a Windows device."""
    with pytest.raises(ExampleError):
        extract_text(name, b"This is a valid length letter about an engineering role.")


def test_examples_limits_and_office_import(private_data: int) -> None:
    """Bounded document imports preserve paragraph structure and reject duplicates."""
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(
            "word/document.xml",
            "<doc><p>A letter with enough words for a meaningful example.</p>"
            "<p>Second paragraph.</p></doc>",
        )
    text = extract_text("old.docx", stream.getvalue())
    assert "example.\n\nSecond" in text
    add_example("Alex", "old.docx", stream.getvalue())
    assert len(list_examples("Alex")) == 1
    assert not list_examples("Sam")
    with pytest.raises(ExampleError):
        add_example("Alex", "old.docx", stream.getvalue())
    for contents in [b"short", b"x" * (8 * 1024 * 1024 + 1)]:
        with pytest.raises(ExampleError):
            extract_text("old.txt", contents)
    with pytest.raises(ExampleError):
        extract_text("bad.pdf", b"Not a PDF")


def test_pdf_editing_and_escaping(private_data: int) -> None:
    """Exports contain edited text; markup remains literal and bullets render."""
    letter = write_letter("Alex", LetterRequest(job_id=private_data), fake())
    letter.paragraphs = [
        "Edited <b>text</b> & evidence.",
        "Relevant work:\n- Technical review\n- Clear advice",
    ]
    pdf = letter_pdf_bytes("Alex", letter)
    reader = PdfReader(io.BytesIO(pdf))
    assert len(reader.pages) == 1
    text = reader.pages[0].extract_text()
    assert "Edited <b>text</b> & evidence." in text
    assert "Technical review" in text
    assert "Alex Example" in text


def test_api_auth_isolation_and_roundtrip(
    private_data: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every letter route inherits auth and all saved data belongs to its user."""
    monkeypatch.setattr(
        "job_scout.web.app.load_secrets", lambda: {"dashboard_token": "test-token"}
    )
    monkeypatch.setattr("job_scout.letters.api.get_llm_client", lambda _: fake())
    with TestClient(create_app()) as client:
        assert client.get("/api/letters/context?user=Alex").status_code == 401
        client.headers["Authorization"] = "Bearer test-token"
        context = client.get("/api/letters/context?user=Alex")
        assert context.headers["cache-control"] == "no-store"
        assert len(context.json()["jobs"]) == 1
        assert client.get("/api/letters/context?user=Sam").json()["jobs"] == []
        assert client.get("/api/letters/context?user=../Alex").status_code == 400
        response = client.post(
            "/api/letters/generate?user=Alex",
            json={"job_id": private_data, "language": "en"},
        )
        assert response.status_code == 200, response.text
        draft = response.json()
        assert (
            client.get(
                f"/api/letters/draft/{private_data}?user=Alex&language=en"
            ).status_code
            == 404
        )
        assert (
            client.put(
                f"/api/letters/draft/{private_data}?user=Sam", json=draft
            ).status_code
            == 400
        )
        assert (
            client.put(
                f"/api/letters/draft/{private_data}?user=Alex", json=draft
            ).status_code
            == 200
        )
        assert (
            client.get(
                f"/api/letters/draft/{private_data}?user=Alex&language=en"
            ).json()
            == draft
        )
        pdf = client.post("/api/letters/pdf?user=Alex", json=draft)
        assert pdf.status_code == 200
        assert pdf.content.startswith(b"%PDF")
        assert client.get("/letters.js").status_code == 200


def test_schedule_without_host_cron(
    private_data: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Container installs can open Schedule without a missing-crontab error."""

    def unavailable(user: str | None = None) -> str:
        raise FileNotFoundError("crontab")

    monkeypatch.setattr("job_scout.web.app.check_schedule_status", unavailable)
    with TestClient(create_app()) as client:
        response = client.get("/api/schedule/status?user=Alex")
        assert response.status_code == 200
        assert "Automatic runs" in response.json()["status"]


def test_long_letter_continues_without_losing_text(private_data: int) -> None:
    """Overflowing paragraphs flow onto more pages with the final signature intact."""
    letter = write_letter("Alex", LetterRequest(job_id=private_data), fake())
    letter.paragraphs = [
        "Measured results inform practical decisions. " * 65 for _ in range(6)
    ]
    reader = PdfReader(io.BytesIO(letter_pdf_bytes("Alex", letter)))
    assert len(reader.pages) > 1
    combined = " ".join(page.extract_text() for page in reader.pages)
    assert " ".join(combined.split()).count("Measured results") == 390
    assert "Alex Example" in reader.pages[-1].extract_text()


def test_style_proposal_needs_explicit_save(
    private_data: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Learning proposes a guide; it cannot silently replace a user's edits."""
    from tests.letters.test_style import GOOD_GUIDE

    save_style_guide("Alex", "Existing guide.")
    add_example(
        "Alex",
        "example.txt",
        b"I am applying for the test engineer position "
        b"and would like to discuss the role.",
    )
    monkeypatch.setattr(
        "job_scout.letters.api.get_llm_client", lambda _: FakeLLMClient([GOOD_GUIDE])
    )
    with TestClient(create_app()) as client:
        proposed = client.post("/api/letters/style/derive?user=Alex")
        assert proposed.status_code == 200
        assert proposed.json()["markdown"] == GOOD_GUIDE.strip()
        assert (
            client.get("/api/letters/style?user=Alex").json()["markdown"]
            == "Existing guide."
        )
        assert (
            client.put("/api/letters/style?user=Alex", json=proposed.json()).status_code
            == 200
        )
        assert client.get("/api/letters/style?user=Sam").json()["markdown"] == ""


def test_cli_generation_is_read_only_unless_save(
    private_data: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The registered CLI works with an explicit user and does not auto-save."""
    from click.testing import CliRunner

    from job_scout.cli import cli

    monkeypatch.setattr("job_scout.letters.cli.get_llm_client", lambda _: fake())
    result = CliRunner().invoke(
        cli,
        ["letter", "generate", str(private_data), "--user", "Alex", "--language", "en"],
    )
    assert result.exit_code == 0, result.output
    assert "Best regards," in result.output
    assert load_letter("Alex", private_data, LetterLanguage.EN) is None
