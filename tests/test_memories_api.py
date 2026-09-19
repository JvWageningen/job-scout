"""The Memories tab's API and page.

The model is a FakeLLMClient; nothing here touches the network. Every person,
employer and project is invented: the repository is public, so no fixture may
carry a real name, address or company.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import job_scout.config as config
from job_scout.memories import (
    MemoryDraft,
    MemorySource,
    add_memory,
    list_forgotten,
    list_memories,
)
from job_scout.memory_extract import auto_capture_enabled
from job_scout.models import Config
from job_scout.web.app import create_app
from job_scout.web.memories_api import PASTED_DETAIL
from tests.helpers import FakeLLMClient
from tests.style_checks import assert_plain

USER = "Sam"
OTHER = "Robin"
VAGUE = "The model could not complete the request. Check LLM settings and retry."
PASTED = (
    "Ik heb bij Voorbeeld Retail 40 winkels naar een nieuw kassasysteem "
    "gemigreerd en ik spreek vloeiend Duits."
)
MIGRATION = (
    "Ik heb bij Voorbeeld Retail 40 winkels naar een nieuw kassasysteem gemigreerd."
)
GERMAN = "Ik spreek vloeiend Duits."
REPLY = json.dumps(
    {
        "memories": [
            {
                "text": MIGRATION,
                "kind": "project",
                "tags": ["retail", "kassasystemen"],
                "hint": "Gebruik voor retail rollen.",
                "use_in": ["cv", "letter", "interview"],
                "sensitive": False,
            },
            {"text": GERMAN, "kind": "skill", "tags": ["duits"]},
        ]
    }
)
STATIC = Path(__file__).parent.parent / "src/job_scout/web/static"


@pytest.fixture
def model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeLLMClient:
    """Two users in a private data directory, and a model answering REPLY.

    Args:
        tmp_path: Private data root for this test.
        monkeypatch: Used to redirect config and the LLM client factory.

    Returns:
        The model every memory route is given.
    """
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.yaml")
    config.write_global_config({"llm_provider": "local"})
    for user in (USER, OTHER):
        config.save_user_config(user, {"name": user})
    client = FakeLLMClient([REPLY])

    def fake_client(_cfg: Config) -> FakeLLMClient:
        return client

    monkeypatch.setattr("job_scout.web.memories_api.get_llm_client", fake_client)
    return client


@pytest.fixture
def api(model: FakeLLMClient) -> TestClient:
    """A dashboard without a token, over the two users."""
    return TestClient(create_app())


def _add(api: TestClient, user: str = USER, **values: object) -> dict[str, object]:
    """Add a memory by hand and return the response body."""
    body: dict[str, object] = {"text": GERMAN, **values}
    response = api.post(f"/api/memories?user={user}", json=body)
    assert response.status_code == 201, response.text
    return response.json()


# -- By hand -------------------------------------------------------------------


def test_a_memory_added_by_hand_is_stored_as_written(
    api: TestClient, model: FakeLLMClient
) -> None:
    """Nothing goes to a model; the page gets back what it needs to show."""
    added = _add(
        api,
        text="Ik wil  maximaal 32 uur\nper week werken.",
        kind="preference",
        tags="Uren, #Parttime, uren",
        hint="Gebruik bij vragen over beschikbaarheid.",
        use_in=["interview", "letter"],
    )

    memory = added["memory"]
    assert isinstance(memory, dict)
    assert memory["text"] == "Ik wil maximaal 32 uur per week werken."
    assert memory["tags"] == ["uren", "parttime"]
    assert memory["use_in"] == ["letter", "interview"]
    assert memory["source"] == "manual"
    assert memory["origin"] == "added by hand"
    assert memory["cited_as"] == f"memory {memory['id']}"
    assert added["similar_id"] is None
    assert model.calls == []
    assert [m.text for m in list_memories(USER)] == [memory["text"]]


def test_the_list_holds_every_memory_the_switch_and_the_deleted_count(
    api: TestClient,
) -> None:
    """One request is enough to draw the tab, private memories included."""
    add_memory(USER, MemoryDraft(text=GERMAN))
    add_memory(USER, MemoryDraft(text="Ik ben mantelzorger.", sensitive=True))

    response = api.get(f"/api/memories?user={USER}")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert [m["text"] for m in body["memories"]] == ["Ik ben mantelzorger.", GERMAN]
    assert body["memories"][0]["sensitive"] is True
    assert body["auto_capture"] is True
    assert body["forgotten"] == 0


def test_adding_a_fact_already_remembered_names_the_memory_that_says_it(
    api: TestClient,
) -> None:
    """It is stored anyway, since it was written on purpose, and the page says so."""
    first = _add(api)["memory"]
    assert isinstance(first, dict)

    again = _add(api, text="ik spreek vloeiend Duits")

    assert again["similar_id"] == first["id"]
    assert len(list_memories(USER)) == 2


@pytest.mark.parametrize(
    "body",
    [
        {"text": ""},
        {"text": "x" * 601},
        {"text": GERMAN, "kind": "hobby"},
        {"text": GERMAN, "hint": "x" * 201},
        {"text": GERMAN, "use_in": ["cv", "poster"]},
        {"kind": "skill"},
    ],
)
def test_a_memory_the_store_cannot_hold_is_refused(
    api: TestClient, body: dict[str, object]
) -> None:
    """Invalid input is a 422 and stores nothing."""
    response = api.post(f"/api/memories?user={USER}", json=body)

    assert response.status_code == 422
    assert list_memories(USER) == []


def test_a_changed_memory_keeps_where_it_came_from(api: TestClient) -> None:
    """Editing replaces what the applicant writes, not the origin."""
    stored = add_memory(
        USER,
        MemoryDraft(
            text=GERMAN,
            source=MemorySource.LETTER_NOTES,
            source_detail="notes for the Analist letter to Voorbeeld BV",
            job_id=7,
        ),
    )

    response = api.put(
        f"/api/memories/{stored.id}?user={USER}",
        json={
            "text": "Ik spreek vloeiend Duits en redelijk Frans.",
            "kind": "skill",
            "tags": ["duits", "frans"],
            "hint": "",
            "use_in": ["letter"],
            "sensitive": True,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["text"] == "Ik spreek vloeiend Duits en redelijk Frans."
    assert body["use_in"] == ["letter"]
    assert body["sensitive"] is True
    assert body["source"] == "letter_notes"
    assert body["job_id"] == 7
    assert body["origin"] == (
        "taken from letter notes (notes for the Analist letter to Voorbeeld BV)"
    )


def test_changing_or_deleting_a_memory_that_is_gone_is_a_404(api: TestClient) -> None:
    """The page says the memory may have been deleted already."""
    content = {"text": GERMAN}

    assert api.put(f"/api/memories/99?user={USER}", json=content).status_code == 404
    assert api.delete(f"/api/memories/99?user={USER}").status_code == 404


@pytest.mark.parametrize("number", ["0", str(2**63), "99999999999999999999"])
def test_a_memory_number_sqlite_cannot_hold_is_refused(
    api: TestClient, number: str
) -> None:
    """A number beyond SQLite's integers is a bad request, not a server error."""
    stored = add_memory(USER, MemoryDraft(text=GERMAN))
    content = {"text": "Overschreven."}

    changed = api.put(f"/api/memories/{number}?user={USER}", json=content)
    deleted = api.delete(f"/api/memories/{number}?user={USER}")

    assert changed.status_code == 422
    assert deleted.status_code == 422
    assert [m.id for m in list_memories(USER)] == [stored.id]
    assert list_forgotten(USER) == []


def test_a_deleted_memory_is_remembered_by_its_wording_until_erased(
    api: TestClient,
) -> None:
    """Deleting holds against notes typed again; erasing the list is explicit."""
    stored = add_memory(USER, MemoryDraft(text=GERMAN))

    assert api.delete(f"/api/memories/{stored.id}?user={USER}").status_code == 204
    assert list_memories(USER) == []
    assert api.get(f"/api/memories?user={USER}").json()["forgotten"] == 1
    forgotten = api.get(f"/api/memories/forgotten?user={USER}").json()["forgotten"]
    assert [item["text"] for item in forgotten] == [GERMAN]

    erased = api.delete(f"/api/memories/forgotten?user={USER}")

    assert erased.status_code == 200, erased.text
    assert erased.json() == {"erased": 1}
    assert list_forgotten(USER) == []


# -- Users ---------------------------------------------------------------------


def test_one_users_memories_never_reach_another(api: TestClient) -> None:
    """Another user can neither see, change nor delete them."""
    stored = add_memory(USER, MemoryDraft(text=GERMAN))

    assert api.get(f"/api/memories?user={OTHER}").json()["memories"] == []
    changed = api.put(
        f"/api/memories/{stored.id}?user={OTHER}", json={"text": "Overschreven."}
    )
    assert changed.status_code == 404
    assert api.delete(f"/api/memories/{stored.id}?user={OTHER}").status_code == 404
    assert [m.text for m in list_memories(USER)] == [GERMAN]
    assert list_forgotten(OTHER) == []


@pytest.mark.parametrize("name", ["Nobody", "../Sam", "all", "", "Sam/..", "."])
def test_an_unknown_or_unsafe_user_is_refused(api: TestClient, name: str) -> None:
    """No database is opened for a name that is not a single existing user."""
    response = api.get("/api/memories", params={"user": name})

    assert response.status_code == 400


def test_every_memory_route_needs_the_dashboard_token(
    model: FakeLLMClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The router sits under /api/, behind the token like the others."""
    monkeypatch.setattr(
        "job_scout.web.app.load_secrets", lambda: {"dashboard_token": "test-token"}
    )
    with TestClient(create_app()) as client:
        assert client.get(f"/api/memories?user={USER}").status_code == 401
        client.headers["Authorization"] = "Bearer test-token"
        assert client.get(f"/api/memories?user={USER}").status_code == 200


# -- From a text ---------------------------------------------------------------


def test_a_text_becomes_proposals_and_nothing_is_saved(
    api: TestClient, model: FakeLLMClient
) -> None:
    """One cv_parsing call; the drafts come back for ticking and editing."""
    response = api.post(f"/api/memories/extract?user={USER}", json={"text": PASTED})

    assert response.status_code == 200, response.text
    body = response.json()
    assert [d["text"] for d in body["drafts"]] == [MIGRATION, GERMAN]
    assert body["truncated"] is False
    first = body["drafts"][0]
    assert first["source"] == "text_import"
    assert first["source_detail"] == PASTED_DETAIL
    assert first["origin"] == f"taken from a text ({PASTED_DETAIL})"
    assert [purpose for _prompt, purpose in model.calls] == ["cv_parsing"]
    assert PASTED in model.calls[0][0]
    assert list_memories(USER) == []


def test_proposals_carry_the_file_they_came_from(api: TestClient) -> None:
    """The page names the file; it is only a label on each draft."""
    response = api.post(
        f"/api/memories/extract?user={USER}",
        json={"text": PASTED, "source_detail": "file projecten.docx"},
    )

    assert {d["source_detail"] for d in response.json()["drafts"]} == {
        "file projecten.docx"
    }


def test_proposals_leave_out_what_is_already_remembered(api: TestClient) -> None:
    """A fact already kept is not proposed a second time."""
    add_memory(USER, MemoryDraft(text=GERMAN))

    response = api.post(f"/api/memories/extract?user={USER}", json={"text": PASTED})

    assert [d["text"] for d in response.json()["drafts"]] == [MIGRATION]


def test_proposing_shows_the_model_your_memories_but_never_a_private_one(
    api: TestClient, model: FakeLLMClient
) -> None:
    """What the tab says is sent when proposing is what the prompt holds."""
    add_memory(USER, MemoryDraft(text=GERMAN))
    private = "Ik ben in 2024 hersteld van een burn-out."
    add_memory(USER, MemoryDraft(text=private, sensitive=True))

    api.post(f"/api/memories/extract?user={USER}", json={"text": PASTED})

    prompt = model.calls[0][0]
    assert GERMAN in prompt
    assert private not in prompt
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    start = page.index('<section id="memories-section"')
    section = page[start : page.index("</section>", start)]
    assert "only as part of" not in section
    assert "whenever new memories are proposed" in section
    assert "every automatic capture sends it" in section


@pytest.mark.parametrize(
    ("text", "message"), [("   ", "Paste or type"), ("x" * 20001, "too long")]
)
def test_an_empty_or_overlong_text_is_refused_without_a_model_call(
    api: TestClient, model: FakeLLMClient, text: str, message: str
) -> None:
    """The applicant is told what to do; the model is not asked."""
    response = api.post(f"/api/memories/extract?user={USER}", json={"text": text})

    assert response.status_code == 400
    assert message in response.json()["detail"]
    assert model.calls == []


def test_a_model_failure_is_reported_without_its_details(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Provider errors can carry endpoints and key fragments."""
    failing = FakeLLMClient([], repeat_last=False)
    monkeypatch.setattr(
        "job_scout.web.memories_api.get_llm_client", lambda _cfg: failing
    )

    response = api.post(f"/api/memories/extract?user={USER}", json={"text": PASTED})

    assert response.status_code == 502
    assert response.json()["detail"] == VAGUE


def test_an_unreadable_answer_asks_for_a_retry(
    api: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A model that answers in prose is a request to try again, not a crash."""
    prose = FakeLLMClient(["Hier zijn de feiten over de sollicitant."])
    monkeypatch.setattr("job_scout.web.memories_api.get_llm_client", lambda _cfg: prose)

    response = api.post(f"/api/memories/extract?user={USER}", json={"text": PASTED})

    assert response.status_code == 400
    assert "Retry" in response.json()["detail"]


def test_the_kept_proposals_are_saved_together_as_edited(api: TestClient) -> None:
    """What the applicant ticked and changed is what is stored, in order."""
    drafts = api.post(
        f"/api/memories/extract?user={USER}", json={"text": PASTED}
    ).json()["drafts"]
    kept = [{**drafts[1], "use_in": ["letter", "interview"], "sensitive": True}]
    kept.insert(
        0, {**drafts[0], "text": "Ik migreerde 40 winkels naar een nieuwe kassa."}
    )

    response = api.post(f"/api/memories/batch?user={USER}", json={"drafts": kept})

    assert response.status_code == 201, response.text
    saved = response.json()["memories"]
    assert [m["text"] for m in saved] == [
        "Ik migreerde 40 winkels naar een nieuwe kassa.",
        GERMAN,
    ]
    assert saved[1]["use_in"] == ["letter", "interview"]
    assert saved[1]["sensitive"] is True
    stored = list_memories(USER)
    assert {m.source for m in stored} == {MemorySource.TEXT_IMPORT}
    assert {m.source_detail for m in stored} == {PASTED_DETAIL}


def test_an_empty_batch_is_refused(api: TestClient) -> None:
    """Saving nothing is a mistake of the page, not a success."""
    response = api.post(f"/api/memories/batch?user={USER}", json={"drafts": []})

    assert response.status_code == 422


def test_a_file_is_read_into_text_and_nothing_is_saved(
    api: TestClient, model: FakeLLMClient
) -> None:
    """The text comes back for the applicant to check before proposing."""
    response = api.post(
        f"/api/memories/read-file?user={USER}",
        files={"file": ("projecten.txt", PASTED.encode("utf-8"), "text/plain")},
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "name": "projecten.txt",
        "text": PASTED,
        "max_chars": 20000,
    }
    assert model.calls == []
    assert list_memories(USER) == []


def test_a_file_that_cannot_be_read_is_refused(api: TestClient) -> None:
    """Only the document formats the CLI import reads are accepted."""
    response = api.post(
        f"/api/memories/read-file?user={USER}",
        files={"file": ("programma.exe", b"MZ", "application/octet-stream")},
    )

    assert response.status_code == 400


# -- The switch ----------------------------------------------------------------


def test_the_capture_switch_is_read_and_written_per_user(api: TestClient) -> None:
    """Switching it off for one user leaves the other as it was."""
    assert api.get(f"/api/memories/auto-capture?user={USER}").json() == {
        "enabled": True
    }

    response = api.put(
        f"/api/memories/auto-capture?user={USER}", json={"enabled": False}
    )

    assert response.json() == {"enabled": False}
    assert auto_capture_enabled(USER) is False
    assert auto_capture_enabled(OTHER) is True
    assert api.get(f"/api/memories?user={USER}").json()["auto_capture"] is False


def test_the_switch_takes_only_a_real_yes_or_no(api: TestClient) -> None:
    """A string such as "no" would otherwise read as switched on."""
    response = api.put(
        f"/api/memories/auto-capture?user={USER}", json={"enabled": "no"}
    )

    assert response.status_code == 422
    assert auto_capture_enabled(USER) is True


# -- The page ------------------------------------------------------------------


def test_the_memories_tab_comes_right_after_cv_builder(api: TestClient) -> None:
    """In the Prepare applications group, with its own section and script."""
    page = api.get("/").text
    group = page[page.index("Prepare applications") :]
    tabs = re.findall(r'class="tab-btn" data-tab="(\w+)"', group)
    assert tabs[:3] == ["cv", "memories", "letters"]
    assert 'id="memories-section"' in page
    assert '<script src="/memories.js"></script>' in page
    script = api.get("/memories.js")
    assert script.status_code == 200
    assert "/api/memories" in script.text


def test_every_element_the_script_looks_up_is_on_the_page() -> None:
    """A missing id would leave the tab dead with nothing but a console error."""
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    script = (STATIC / "memories.js").read_text(encoding="utf-8")
    looked_up = set(re.findall(r"\bel\('([\w-]+)'\)", script))
    assert {"workspace", "status", "list", "auto"} <= looked_up
    missing = [name for name in looked_up if f'id="memory-{name}"' not in page]
    assert missing == []


def _strings(script: str) -> list[str]:
    """Return the text of every string literal in a script, code left out."""
    script = re.sub(r"/\*.*?\*/", "", script, flags=re.DOTALL)
    script = re.sub(r"(?m)^\s*//.*$", "", script)
    quoted = re.findall(r"'((?:[^'\\\n]|\\.)*)'", script)
    templates = re.findall(r"`([^`]*)`", script)
    return quoted + [re.sub(r"\$\{[^}]*\}", " ", text) for text in templates]


def test_what_the_tab_says_follows_the_house_style() -> None:
    """No em or en dashes and no spaced hyphens, on the page or in the script."""
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    start = page.index('<section id="memories-section"')
    section = page[start : page.index("</section>", start)]
    assert_plain(re.sub(r"<[^>]+>", " ", section))
    script = (STATIC / "memories.js").read_text(encoding="utf-8")
    assert "\u2014" not in script
    assert "\u2013" not in script
    for text in _strings(script):
        assert_plain(text)
