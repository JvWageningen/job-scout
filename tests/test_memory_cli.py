"""The ``job-scout memory`` commands.

Every person, employer and project here is invented. The model is a
FakeLLMClient; nothing here touches the network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

import job_scout.config as config
import job_scout.memory_cli as memory_cli
from job_scout.cli import cli
from job_scout.memories import (
    MemoryDraft,
    MemorySource,
    MemoryUse,
    add_memory,
    get_memory,
    list_forgotten,
    list_memories,
)
from job_scout.memory_extract import auto_capture_enabled
from job_scout.models import Config
from tests.helpers import FakeLLMClient

USER = "tester"
PASTED = (
    "Ik heb bij Voorbeeld Retail 40 winkels naar een nieuw kassasysteem "
    "gemigreerd en ik spreek vloeiend Duits."
)
REPLY = json.dumps(
    {
        "memories": [
            {
                "text": "Ik heb bij Voorbeeld Retail 40 winkels naar een nieuw "
                "kassasysteem gemigreerd.",
                "kind": "project",
                "tags": ["retail"],
                "hint": "Gebruik voor retail rollen.",
                "use_in": ["cv", "letter", "interview"],
                "sensitive": False,
            },
            {"text": "Ik spreek vloeiend Duits.", "kind": "skill", "tags": ["duits"]},
        ]
    }
)


@pytest.fixture(autouse=True)
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate every test in its own data directory holding one user."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.yaml")
    config.write_global_config({"llm_provider": "local"})
    config.save_user_config(USER, {"name": USER})
    return tmp_path


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch) -> FakeLLMClient:
    """Answer every import with REPLY."""
    client = FakeLLMClient([REPLY])

    def fake_client(_cfg: Config) -> FakeLLMClient:
        return client

    monkeypatch.setattr(memory_cli, "get_llm_client", fake_client)
    return client


def _run(*args: str, stdin: str | None = None) -> Result:
    """Run ``job-scout memory`` with these arguments."""
    return CliRunner().invoke(cli, ["memory", *args], input=stdin)


def test_the_group_hangs_off_the_root_cli() -> None:
    """Reachable as 'job-scout memory', with every command listed."""
    result = _run("--help")
    assert result.exit_code == 0
    for command in (
        "list",
        "add",
        "edit",
        "delete",
        "import",
        "forgotten",
        "auto-capture",
    ):
        assert command in result.output


def test_an_empty_list_says_so() -> None:
    """No memories is a normal state."""
    result = _run("list")
    assert result.exit_code == 0
    assert "No memories stored yet." in result.output


def test_add_stores_every_option_and_list_shows_it() -> None:
    """Text, kind, tags, hint, uses and the private flag all arrive."""
    result = _run(
        "add",
        "Ik wil maximaal 32 uur per week werken.",
        "--kind",
        "preference",
        "--tags",
        "Uren, parttime",
        "--hint",
        "Gebruik bij vragen over beschikbaarheid.",
        "--use",
        "letter",
        "--use",
        "interview",
        "--private",
        "--user",
        USER,
    )
    assert result.exit_code == 0, result.output
    [memory] = list_memories(USER)
    assert memory.tags == ["uren", "parttime"]
    assert memory.use_in == [MemoryUse.LETTER, MemoryUse.INTERVIEW]
    assert memory.sensitive is True
    assert memory.source is MemorySource.MANUAL
    shown = _run("list").output
    assert f"#{memory.id} [preference] added by hand" in shown
    assert "Tags: uren, parttime" in shown
    assert "Used in: letter, interview" in shown
    assert "Private: stored, never sent to a model" in shown


def test_add_without_uses_allows_every_use() -> None:
    """The default, as in the dashboard."""
    assert _run("add", "Ik spreek vloeiend Duits.").exit_code == 0
    assert list_memories(USER)[0].use_in == list(MemoryUse)


def test_add_refuses_an_overlong_text() -> None:
    """The limit is explained, not a traceback."""
    result = _run("add", "x" * 601)
    assert result.exit_code != 0
    assert "text" in result.output
    assert list_memories(USER) == []


def test_add_warns_about_a_memory_that_says_the_same() -> None:
    """It is still saved: the applicant asked for it."""
    first = add_memory(USER, MemoryDraft(text="Ik spreek vloeiend Duits."))
    result = _run("add", "ik spreek vloeiend duits")
    assert result.exit_code == 0
    assert f"Memory #{first.id} already says much the same." in result.output
    assert len(list_memories(USER)) == 2


def test_list_filters_on_a_search() -> None:
    """Every word must occur in the text, tags, hint or kind."""
    add_memory(USER, MemoryDraft(text="Ik spreek Duits.", tags=["taal"]))
    add_memory(USER, MemoryDraft(text="Ik ken Python."))
    result = _run("list", "--search", "TAAL")
    assert "Ik spreek Duits." in result.output
    assert "Python" not in result.output
    assert "No memories match." in _run("list", "--search", "cobol").output


def test_edit_changes_only_what_is_given() -> None:
    """Clearing the private flag is the way to let a memory be used."""
    stored = add_memory(
        USER, MemoryDraft(text="Oud.", tags=["a"], hint="Hint.", sensitive=True)
    )
    result = _run("edit", str(stored.id), "--text", "Nieuw.", "--not-private")
    assert result.exit_code == 0, result.output
    changed = get_memory(USER, stored.id)
    assert changed is not None
    assert changed.text == "Nieuw."
    assert changed.sensitive is False
    assert changed.tags == ["a"]
    assert changed.hint == "Hint."


def test_edit_replaces_the_uses_when_given() -> None:
    """--use lists the new uses in full."""
    stored = add_memory(USER, MemoryDraft(text="Feit."))
    assert _run("edit", str(stored.id), "--use", "cv").exit_code == 0
    changed = get_memory(USER, stored.id)
    assert changed is not None
    assert changed.use_in == [MemoryUse.CV]


def test_edit_of_a_missing_memory_fails() -> None:
    """The id is checked before anything is written."""
    result = _run("edit", "99", "--text", "Nieuw.")
    assert result.exit_code != 0
    assert "Memory #99 not found." in result.output


def test_delete_removes_the_named_memories_and_reports_missing_ones() -> None:
    """Every id is tried; a missing one makes the command fail."""
    first = add_memory(USER, MemoryDraft(text="Een."))
    second = add_memory(USER, MemoryDraft(text="Twee."))
    result = _run("delete", str(first.id), "99")
    assert result.exit_code != 0
    assert f"Memory #{first.id} deleted." in result.output
    assert "Not found: #99." in result.output
    assert [m.id for m in list_memories(USER)] == [second.id]


def test_delete_needs_ids_or_all() -> None:
    """Deleting nothing by accident is a usage error."""
    assert _run("delete").exit_code != 0


def test_delete_all_asks_first() -> None:
    """Answering no keeps everything."""
    add_memory(USER, MemoryDraft(text="Een."))
    result = _run("delete", "--all", stdin="n\n")
    assert result.exit_code != 0
    assert len(list_memories(USER)) == 1
    result = _run("delete", "--all", "--yes")
    assert result.exit_code == 0
    assert "Deleted 1 memories." in result.output
    assert list_memories(USER) == []


def test_import_text_shows_the_proposals_and_saves_them(model: FakeLLMClient) -> None:
    """With --yes every proposal is stored as imported from text."""
    result = _run("import", "--text", PASTED, "--yes")
    assert result.exit_code == 0, result.output
    assert "1. [project] taken from a text" in result.output
    assert "Saved 2 memories." in result.output
    stored = list_memories(USER)
    assert {m.source for m in stored} == {MemorySource.TEXT_IMPORT}
    assert [purpose for _, purpose in model.calls] == ["cv_parsing"]


def test_import_asks_before_saving(model: FakeLLMClient) -> None:
    """Answering no, or a dry run, saves nothing."""
    result = _run("import", "--text", PASTED, stdin="n\n")
    assert "Nothing saved." in result.output
    result = _run("import", "--text", PASTED, "--dry-run")
    assert "Nothing saved." in result.output
    assert list_memories(USER) == []


def test_import_reads_a_file(model: FakeLLMClient, tmp_path: Path) -> None:
    """A text file is read and its name kept as the origin."""
    path = tmp_path / "over-mij.txt"
    path.write_bytes(PASTED.encode("utf-8"))
    result = _run("import", str(path), "--yes")
    assert result.exit_code == 0, result.output
    assert PASTED in model.calls[0][0]
    assert list_memories(USER)[0].source_detail == "over-mij.txt"


def test_import_needs_exactly_one_text(model: FakeLLMClient, tmp_path: Path) -> None:
    """A file and --text together, or neither, is a usage error."""
    path = tmp_path / "over-mij.txt"
    path.write_bytes(PASTED.encode("utf-8"))
    assert _run("import").exit_code != 0
    assert _run("import", str(path), "--text", PASTED).exit_code != 0
    assert model.calls == []


def test_import_reports_an_unreadable_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    """A model failure is a message, not a traceback."""
    client = FakeLLMClient(["no json"])
    monkeypatch.setattr(memory_cli, "get_llm_client", lambda _cfg: client)
    result = _run("import", "--text", PASTED)
    assert result.exit_code != 0
    assert "Retry" in result.output


def test_import_with_nothing_new_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty answer is normal."""
    client = FakeLLMClient(['{"memories": []}'])
    monkeypatch.setattr(memory_cli, "get_llm_client", lambda _cfg: client)
    result = _run("import", "--text", PASTED)
    assert result.exit_code == 0
    assert "Nothing new to remember in that text." in result.output


def test_import_says_when_the_text_may_hold_more(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The applicant must not assume a long text was captured in full."""
    items = [{"text": f"Ik heb project nummer {i} geleid."} for i in range(25)]
    client = FakeLLMClient([json.dumps({"memories": items})])
    monkeypatch.setattr(memory_cli, "get_llm_client", lambda _cfg: client)
    result = _run("import", "--text", PASTED, "--yes")
    assert result.exit_code == 0, result.output
    assert "Saved 20 memories." in result.output
    assert "There may be more in this text; run it again" in result.output


def test_import_of_a_short_text_does_not_mention_more(model: FakeLLMClient) -> None:
    """Only a cut answer gets the note."""
    result = _run("import", "--text", PASTED, "--yes")
    assert "There may be more" not in result.output


def test_forgotten_lists_deleted_memories_and_clears_after_asking() -> None:
    """What notes will not bring back is visible, and can be erased."""
    assert "No deleted memories." in _run("forgotten").output
    stored = add_memory(USER, MemoryDraft(text="Ik spreek Duits.", sensitive=True))
    _run("delete", str(stored.id))
    shown = _run("forgotten").output
    assert "(private): Ik spreek Duits." in shown
    assert "1 deleted memories." in shown
    assert _run("forgotten", "--clear", stdin="n\n").exit_code != 0
    assert list_forgotten(USER) != []
    result = _run("forgotten", "--clear", "--yes")
    assert "Erased 1 deleted memories." in result.output
    assert list_forgotten(USER) == []


def test_auto_capture_shows_and_switches_the_setting() -> None:
    """On by default; off and on again per user."""
    assert "is on for tester" in _run("auto-capture").output
    assert "is off for tester" in _run("auto-capture", "off").output
    assert auto_capture_enabled(USER) is False
    assert "is on for tester" in _run("auto-capture", "on").output


def test_an_unknown_user_is_refused() -> None:
    """--user works like everywhere else."""
    result = _run("list", "--user", "nobody")
    assert result.exit_code != 0
    assert "not found" in result.output
