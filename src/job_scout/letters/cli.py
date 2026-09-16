"""Click commands for private examples and CV-grounded motivational letters."""

from __future__ import annotations

from pathlib import Path

import click

from job_scout.config import build_effective_config
from job_scout.cv.storage import StorageError
from job_scout.letters.examples import add_example, list_examples
from job_scout.letters.models import LetterLanguage, LetterRequest
from job_scout.letters.style import derive_style_guide, save_style_guide
from job_scout.letters.writer import (
    letter_pdf_bytes,
    load_letter,
    save_letter,
    write_letter,
)
from job_scout.llm.base import LLMError
from job_scout.llm.factory import get_llm_client


def _user(name: str | None) -> str:
    """Use the root command's single-user resolution policy."""
    from job_scout.cli import _require_single_user  # noqa: PLC0415

    return _require_single_user(name)


@click.group("letter")
def letter() -> None:
    """Write motivational letters from your current CV and private examples."""


@letter.command("import-examples")
@click.argument(
    "files",
    nargs=-1,
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option("--user", "name")
def import_examples(files: tuple[Path, ...], name: str | None) -> None:
    """Import original letters as private style references."""
    user = _user(name)
    for path in files:
        try:
            example = add_example(user, path.name, path.read_bytes())
        except (ValueError, OSError) as exc:
            raise click.ClickException(str(exc)) from exc
        click.echo(
            f"Imported {example.name} ({example.language.value}, {example.words} words)"
        )


@letter.command("learn-style")
@click.option("--user", "name")
def learn_style(name: str | None) -> None:
    """Derive and save a guide from private examples using your configured model."""
    from job_scout.letters.style import StyleError  # noqa: PLC0415

    user = _user(name)
    try:
        guide = derive_style_guide(
            list_examples(user), get_llm_client(build_effective_config(user))
        )
        save_style_guide(user, guide)
    except (ValueError, OSError, LLMError, StyleError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(guide)


@letter.command("generate")
@click.argument("job_id", type=int)
@click.option("--user", "name")
@click.option("--language", type=click.Choice(["auto", "nl", "en"]), default="auto")
@click.option("--cv", "cv_slug")
@click.option("--recipient", default="")
@click.option("--notes", default="")
@click.option(
    "--save",
    "persist",
    is_flag=True,
    help="Replace the saved draft for this vacancy and language.",
)
@click.option("--pdf", "pdf_path", type=click.Path(path_type=Path))
def generate(
    job_id: int,
    name: str | None,
    language: str,
    cv_slug: str | None,
    recipient: str,
    notes: str,
    persist: bool,
    pdf_path: Path | None,
) -> None:
    """Draft a letter; review the text before sending it yourself."""
    user = _user(name)
    try:
        request = LetterRequest.model_validate(
            dict(
                job_id=job_id,
                language=language,
                cv_slug=cv_slug,
                recipient=recipient,
                notes=notes,
            )
        )
        draft = write_letter(
            user, request, get_llm_client(build_effective_config(user))
        )
        if pdf_path:
            pdf_path.write_bytes(letter_pdf_bytes(user, draft))
        if persist:
            save_letter(user, draft)
    except (ValueError, OSError, StorageError, LLMError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(draft.as_plain_text())
    for warning in draft.warnings:
        click.echo(f"Review: {warning.message}", err=True)


@letter.command("render")
@click.argument("job_id", type=int)
@click.argument("output", type=click.Path(path_type=Path))
@click.option("--user", "name")
@click.option("--language", type=click.Choice(["nl", "en"]), default="nl")
def render(job_id: int, output: Path, name: str | None, language: str) -> None:
    """Export a saved letter to PDF using the current CV theme."""
    user = _user(name)
    try:
        draft = load_letter(user, job_id, LetterLanguage(language))
        if draft is None:
            raise ValueError("No saved draft in that language.")
        output.write_bytes(letter_pdf_bytes(user, draft))
    except (ValueError, OSError, StorageError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(str(output))
