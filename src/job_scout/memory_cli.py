"""Click commands for the facts job-scout remembers about the applicant."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import click
import yaml
from loguru import logger
from pydantic import ValidationError

from job_scout.company_lookups import day
from job_scout.config import build_effective_config
from job_scout.letters.examples import extract_text
from job_scout.llm.base import LLMClient, LLMError
from job_scout.llm.factory import get_llm_client
from job_scout.memories import (
    ALL_USES,
    Memory,
    MemoryContent,
    MemoryDraft,
    MemoryKind,
    MemorySource,
    MemoryUse,
    add_memories,
    add_memory,
    clear_forgotten,
    delete_all_memories,
    delete_memory,
    describe_origin,
    find_duplicate,
    get_memory,
    list_forgotten,
    list_memories,
    memory_matches,
    update_memory,
)
from job_scout.memory_extract import (
    auto_capture_enabled,
    capture_from_notes,
    capture_pending,
    extract_memories,
    set_auto_capture,
)

_KINDS = click.Choice([kind.value for kind in MemoryKind])
_USES = click.Choice([use.value for use in MemoryUse])


def _user(name: str | None) -> str:
    """Use the root command's single-user resolution policy."""
    from job_scout.cli import _require_single_user  # noqa: PLC0415

    return _require_single_user(name)


def _problem(exc: ValidationError) -> str:
    """Say in one line what is wrong with a memory.

    Args:
        exc: The validation failure.

    Returns:
        The field and the first problem with it.
    """
    first = exc.errors()[0]
    field = ".".join(str(part) for part in first["loc"]) or "memory"
    return f"{field}: {first['msg']}"


def _show(memory: MemoryDraft, number: str) -> None:
    """Print one memory or proposal.

    Args:
        memory: A stored memory or a draft.
        number: How to refer to it, such as "#12" or "3.".
    """
    when = f", {day(memory.created_at)}" if isinstance(memory, Memory) else ""
    click.echo(f"{number} [{memory.kind.value}] {describe_origin(memory)}{when}")
    click.echo(f"   {memory.text}")
    if memory.tags:
        click.echo(f"   Tags: {', '.join(memory.tags)}")
    if memory.hint:
        click.echo(f"   Hint: {memory.hint}")
    uses = ", ".join(use.value for use in memory.use_in) or "nowhere"
    click.echo(f"   Used in: {uses}")
    if memory.sensitive:
        click.echo("   Private: stored, never sent to a model")
    click.echo()


@click.group("memory")
def memory() -> None:
    """Keep facts about yourself for letters, interviews and your CV.

    A memory is one statement about you that is not on your CV, or not for
    every vacancy: a project, a result, a wish, a condition. Letters,
    interview answers and a tailored CV use the ones that fit the vacancy.
    """


@memory.command("list")
@click.option("--user", "name")
@click.option("--search", default="", help="Only memories containing these words.")
def list_cmd(name: str | None, search: str) -> None:
    """Show your memories, newest first."""
    user = _user(name)
    memories = [m for m in list_memories(user) if memory_matches(m, search)]
    if not memories:
        click.echo("No memories match." if search else "No memories stored yet.")
        return
    for item in memories:
        _show(item, f"#{item.id}")
    click.echo(f"{len(memories)} memories.")


@memory.command("add")
@click.argument("text")
@click.option("--user", "name")
@click.option("--kind", type=_KINDS, default=MemoryKind.OTHER.value)
@click.option("--tags", default="", help="Keywords, separated by commas.")
@click.option("--hint", default="", help="When it applies, in one sentence.")
@click.option(
    "--use",
    "uses",
    type=_USES,
    multiple=True,
    help="Where it may be used; repeat. Default: cv, letter and interview.",
)
@click.option("--private", is_flag=True, help="Store it, never send it to a model.")
def add_cmd(
    text: str,
    name: str | None,
    kind: str,
    tags: str,
    hint: str,
    uses: tuple[str, ...],
    private: bool,
) -> None:
    """Remember TEXT, a statement about yourself."""
    user = _user(name)
    try:
        draft = MemoryDraft(
            text=text,
            kind=MemoryKind(kind),
            tags=tags,
            hint=hint,
            use_in=list(uses) or list(ALL_USES),
            sensitive=private,
        )
    except ValidationError as exc:
        raise click.ClickException(_problem(exc)) from exc
    similar = find_duplicate(draft.text, list_memories(user))
    stored = add_memory(user, draft)
    click.echo(f"Memory #{stored.id} saved.")
    if similar is not None:
        click.echo(f"Memory #{similar.id} already says much the same.", err=True)


@memory.command("edit")
@click.argument("memory_id", type=int)
@click.option("--user", "name")
@click.option("--text", default=None)
@click.option("--kind", type=_KINDS, default=None)
@click.option("--tags", default=None, help="Keywords, separated by commas.")
@click.option("--hint", default=None)
@click.option("--use", "uses", type=_USES, multiple=True, help="Replaces the uses.")
@click.option("--private/--not-private", "private", default=None)
def edit_cmd(
    memory_id: int,
    name: str | None,
    text: str | None,
    kind: str | None,
    tags: str | None,
    hint: str | None,
    uses: tuple[str, ...],
    private: bool | None,
) -> None:
    """Change a memory; what you leave out stays as it is."""
    user = _user(name)
    current = get_memory(user, memory_id)
    if current is None:
        raise click.ClickException(f"Memory #{memory_id} not found.")
    given = {"text": text, "kind": kind, "tags": tags, "hint": hint}
    changes: dict[str, object] = {k: v for k, v in given.items() if v is not None}
    if uses:
        changes["use_in"] = list(uses)
    if private is not None:
        changes["sensitive"] = private
    try:
        content = MemoryContent.model_validate(
            {**current.content().model_dump(), **changes}
        )
    except ValidationError as exc:
        raise click.ClickException(_problem(exc)) from exc
    update_memory(user, memory_id, content)
    click.echo(f"Memory #{memory_id} updated.")


@memory.command("delete")
@click.argument("memory_ids", nargs=-1, type=int)
@click.option("--user", "name")
@click.option("--all", "everything", is_flag=True, help="Delete every memory.")
@click.option("--yes", is_flag=True, help="Do not ask before deleting all.")
def delete_cmd(
    memory_ids: tuple[int, ...], name: str | None, everything: bool, yes: bool
) -> None:
    """Delete the memories with these ids, or all of them with --all.

    Automatic capture will not bring a deleted memory back; see
    'memory forgotten'.
    """
    user = _user(name)
    if everything:
        if not yes:
            click.confirm("Delete all your memories?", abort=True)
        click.echo(f"Deleted {delete_all_memories(user)} memories.")
        return
    if not memory_ids:
        raise click.UsageError("Name the memories to delete, or pass --all.")
    missing = [i for i in memory_ids if not delete_memory(user, i)]
    for memory_id in memory_ids:
        if memory_id not in missing:
            click.echo(f"Memory #{memory_id} deleted.")
    if missing:
        names = ", ".join(f"#{i}" for i in missing)
        raise click.ClickException(f"Not found: {names}.")


def _import_text(path: Path | None, text: str | None) -> tuple[str, str]:
    """Read the text to import and say where it came from.

    Args:
        path: A file to read, or None.
        text: Text given on the command line, or None.

    Returns:
        The text and its origin in words.
    """
    if path is not None and text is not None:
        raise click.UsageError("Give a file or --text, not both.")
    if path is None:
        if text is None:
            raise click.UsageError("Give a file to read, or the text with --text.")
        return text, "text given on the command line"
    try:
        return extract_text(path.name, path.read_bytes(), kind="document"), path.name
    except (ValueError, OSError) as exc:
        raise click.ClickException(str(exc)) from exc


@memory.command("import")
@click.argument(
    "file",
    required=False,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option("--text", default=None, help="The text itself, instead of a file.")
@click.option("--user", "name")
@click.option("--yes", is_flag=True, help="Save every proposal without asking.")
@click.option("--dry-run", is_flag=True, help="Show the proposals, save nothing.")
def import_cmd(
    file: Path | None, text: str | None, name: str | None, yes: bool, dry_run: bool
) -> None:
    """Turn a text about yourself into memories (TXT, MD, PDF, DOCX or ODT)."""
    user = _user(name)
    body, origin = _import_text(file, text)
    try:
        found = extract_memories(
            body,
            list_memories(user),
            get_llm_client(build_effective_config(user)),
            source=MemorySource.TEXT_IMPORT,
            source_detail=origin,
        )
    except (ValueError, LLMError) as exc:
        raise click.ClickException(str(exc)) from exc
    _save_proposals(user, found.drafts, yes=yes, dry_run=dry_run)
    if found.truncated:
        click.echo("There may be more in this text; run it again to get the rest.")


def _save_proposals(
    user: str, drafts: list[MemoryDraft], *, yes: bool, dry_run: bool
) -> None:
    """Show proposed memories and save them when the applicant agrees.

    Args:
        user: The user the memories are for.
        drafts: The proposals.
        yes: Save without asking.
        dry_run: Save nothing.
    """
    if not drafts:
        click.echo("Nothing new to remember in that text.")
        return
    for number, draft in enumerate(drafts, start=1):
        _show(draft, f"{number}.")
    if dry_run or not (yes or click.confirm(f"Save these {len(drafts)} memories?")):
        click.echo("Nothing saved.")
        return
    click.echo(f"Saved {len(add_memories(user, drafts))} memories.")


@memory.command("forgotten")
@click.option("--user", "name")
@click.option("--clear", is_flag=True, help="Erase the list after asking.")
@click.option("--yes", is_flag=True, help="Do not ask before erasing.")
def forgotten_cmd(name: str | None, clear: bool, yes: bool) -> None:
    """Show the memories you deleted, which notes will not bring back."""
    user = _user(name)
    if clear:
        if not yes:
            click.confirm(
                "Erase the list? Automatic capture may then find these again.",
                abort=True,
            )
        click.echo(f"Erased {clear_forgotten(user)} deleted memories.")
        return
    forgotten = list_forgotten(user)
    if not forgotten:
        click.echo("No deleted memories.")
        return
    for item in forgotten:
        private = " (private)" if item.sensitive else ""
        click.echo(f"{day(item.forgotten_at)}{private}: {item.text}")
    click.echo(f"{len(forgotten)} deleted memories.")


@memory.command("auto-capture")
@click.argument("state", required=False, type=click.Choice(["on", "off"]))
@click.option("--user", "name")
def auto_capture_cmd(state: str | None, name: str | None) -> None:
    """Show or switch whether letter and interview notes become memories."""
    user = _user(name)
    if state is not None:
        set_auto_capture(user, state == "on")
    now = "on" if auto_capture_enabled(user) else "off"
    click.echo(f"Automatic capture from notes is {now} for {user}.")


# -- Capture after a letter or interview command ------------------------------------

_CHECK_ERRORS = (ValueError, sqlite3.Error, OSError, yaml.YAMLError)


def _capture_due(user: str, notes: str) -> bool:
    """Tell whether the notes of a generation should be read for memories now.

    Args:
        user: The user the generation was for.
        notes: The notes as typed.

    Returns:
        False for notes too short to state a fact, with automatic capture
        switched off, for notes captured before, or when that cannot be told.
    """
    try:
        return capture_pending(user, notes)
    except _CHECK_ERRORS as exc:
        logger.warning(f"Could not tell whether to read notes for memories: {exc}")
        return False


def _say_captured(user: str, notes: str, saved: list[Memory]) -> None:
    """Tell the applicant what their notes added to their memories.

    Args:
        user: The user the memories are for.
        notes: The notes that were read.
        saved: The memories stored from them.
    """
    if not saved:
        failed = _capture_due(user, notes)
        click.echo(
            "Your notes could not be read for memories this time; the next "
            "letter or interview with them tries again."
            if failed
            else "Nothing new to remember in your notes.",
            err=True,
        )
        return
    noun = "memory" if len(saved) == 1 else "memories"
    click.echo(f"Saved {len(saved)} new {noun} from your notes:", err=True)
    for item in saved:
        private = " (private, never sent to a model)" if item.sensitive else ""
        click.echo(f"  #{item.id}{private} {item.text}", err=True)
    click.echo(
        f"See them with 'job-scout memory list --user {user}', remove one with "
        f"'job-scout memory delete ID --user {user}', or stop this with "
        f"'job-scout memory auto-capture off --user {user}'.",
        err=True,
    )


def report_notes_capture(
    user: str,
    notes: str,
    *,
    source: MemorySource,
    source_detail: str,
    job_id: int | None = None,
    client: LLMClient | None = None,
) -> list[Memory]:
    """Keep the facts in the notes of a letter or interview command as memories.

    The dashboard does this in the background; a command runs it after its
    own output and says what was kept. Nothing is said, and no model is
    asked, when the notes are too short to state a fact, automatic capture
    is switched off, or the same notes were captured before. It never
    raises: the letter or interview set is already done.

    Args:
        user: The user the generation was for.
        notes: The notes as typed.
        source: Where the notes were typed, letter or interview notes.
        source_detail: Where they came from in words, such as "notes for the
            Findwhere letter".
        job_id: The vacancy the notes were typed for.
        client: The model to ask; the user's configured one when None.

    Returns:
        The memories stored.
    """
    if not _capture_due(user, notes):
        return []
    click.echo("\nLooking in your notes for facts to remember for later...", err=True)
    saved = capture_from_notes(
        user,
        notes,
        source=source,
        source_detail=source_detail,
        job_id=job_id,
        client=client,
    )
    _say_captured(user, notes, saved)
    return saved
