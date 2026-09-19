"""Click commands for the CV builder, mounted on job-scout's root group.

The CV builder used to ship its own ``cv-builder`` entry point. Inside job-scout
it is one more group on the existing CLI, so everything is reachable as
``job-scout cv ...`` and there is a single console script to install.
"""

from __future__ import annotations

import logging
import socket
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import click
from loguru import logger

from job_scout.applicant import memories_for_vacancy
from job_scout.config import build_effective_config, user_cv_dir, user_db_path
from job_scout.cv.models import CVDocument
from job_scout.cv.render import render_pdf
from job_scout.cv.storage import DEFAULT_PROFILE, ProfileStore, StorageError
from job_scout.cv.tailor import TailoredCV, TailorError, tailor_cv, tailored_slug
from job_scout.database import Database
from job_scout.llm.base import LLMClient, LLMError
from job_scout.llm.factory import get_llm_client
from job_scout.memories import MemoryUse
from job_scout.models import JobListing

DEFAULT_PORT = 38271
"""Deliberately not 8000, which is commonly taken by another local service. Kept
below Windows' dynamic range (49152+) so it cannot clash with an ephemeral port."""


def _port_is_free(host: str, port: int) -> bool:
    """Check whether the editor can bind ``host:port``.

    Binding a throwaway socket first turns "port already taken" into a one-line
    message instead of a uvicorn traceback.

    Args:
        host: Interface to test.
        port: Port to test.

    Returns:
        True if the port accepted a bind.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


class _DropClientDisconnects(logging.Filter):
    """Suppress the noise a browser makes when it abandons a response.

    Auto-refresh replaces the preview iframe while a render may still be
    streaming, so the browser closes the socket mid-response. On Windows asyncio's
    proactor reports that as an unhandled ``ConnectionResetError`` with a full
    traceback, even though nothing has gone wrong. Only that exact exception is
    dropped, so real asyncio errors still surface.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Return False for connection-reset reports.

        Args:
            record: The record being emitted.

        Returns:
            False to discard the record.
        """
        error = record.exc_info[1] if record.exc_info else None
        return not isinstance(error, ConnectionResetError)


def _configure_logging(verbose: bool) -> None:
    """Point loguru at stderr with a level matching the flag.

    Args:
        verbose: Whether to emit DEBUG records.
    """
    logger.remove()
    logger.add(sys.stderr, level="DEBUG" if verbose else "INFO")


def _resolve_user(user_name: str | None) -> str:
    """Resolve which user a command applies to, or exit.

    The user is resolved the same way every other job-scout command resolves
    one, so a single-user install can leave ``--user`` off here too. The import
    is deferred because the root CLI imports this module to register the group.

    Args:
        user_name: Explicit user name, or None to use the only user.

    Returns:
        The resolved user name.
    """
    from job_scout.cli import _require_single_user  # noqa: PLC0415

    return _require_single_user(user_name)


def _store_for_user(user_name: str | None) -> ProfileStore:
    """Return the CV profile store belonging to the resolved user.

    Args:
        user_name: Explicit user name, or None to use the only user.

    Returns:
        A store rooted at ``data/users/<name>/cv``.
    """
    return ProfileStore(user_cv_dir(_resolve_user(user_name)))


def _load_profile(store: ProfileStore, slug: str) -> CVDocument:
    """Load one stored profile, or exit with a one-line message.

    Args:
        store: The user's profile store.
        slug: Profile slug to load.

    Returns:
        The stored document.
    """
    try:
        return store.load(slug)
    except StorageError as exc:
        click.echo(str(exc), err=True)
        sys.exit(1)


def _load_job(user_name: str, job_id: int) -> JobListing:
    """Load one vacancy from a user's database, or exit with a message.

    Args:
        user_name: The user whose database holds the vacancy.
        job_id: The vacancy's database id.

    Returns:
        The vacancy.
    """
    job = Database(user_db_path(user_name)).get_job(job_id)
    if job is None:
        click.echo(f"No job with id {job_id} for user {user_name!r}.", err=True)
        sys.exit(1)
    return job


def _client_for_user(user_name: str) -> LLMClient:
    """Build the user's configured LLM client, or exit with a message.

    The client comes from the shared factory rather than being constructed here,
    so per-purpose provider routing and retries apply exactly as they do to
    every other job-scout command.

    Args:
        user_name: The user whose LLM settings to use.

    Returns:
        A ready-to-use client.
    """
    try:
        client = get_llm_client(build_effective_config(user_name))
    except LLMError as exc:
        click.echo(f"LLM configuration error: {exc}", err=True)
        sys.exit(1)

    available, error = client.check_available()
    if not available:
        click.echo(f"LLM not available: {error}", err=True)
        sys.exit(1)
    return client


def _write_pdf(store: ProfileStore, slug: str, doc: CVDocument, output: Path) -> int:
    """Render a document to ``output``, creating parent directories.

    Args:
        store: Store used to resolve the profile's portrait.
        slug: Slug the portrait belongs to.
        doc: Document to render.
        output: File to write.

    Returns:
        The number of pages written.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as handle:
        return render_pdf(doc, handle, store.photo_path(slug, doc))


_USER_OPTION = click.option(
    "--user", "user_name", default=None, help="User whose CV profiles to use"
)


@click.group("cv")
@click.pass_context
def cv(ctx: click.Context) -> None:
    """Edit CV content in a browser and render it to a print-ready PDF."""
    _configure_logging(bool(ctx.find_root().params.get("verbose")))


@cv.command("serve")
@_USER_OPTION
@click.option(
    "--host", default="127.0.0.1", show_default=True, help="Interface to bind."
)
@click.option(
    "--port",
    type=int,
    default=DEFAULT_PORT,
    show_default=True,
    help="Port to listen on.",
)
def serve(user_name: str | None, host: str, port: int) -> None:
    """Run the CV editor web server."""
    if not _port_is_free(host, port):
        click.echo(
            f"Port {port} on {host} is already in use. "
            f"Pick another with: job-scout cv serve --port {port + 1}",
            err=True,
        )
        sys.exit(1)

    logging.getLogger("asyncio").addFilter(_DropClientDisconnects())

    store = _store_for_user(user_name)
    store.ensure_default()
    logger.info("Data directory: {}", store.root)
    logger.info("CV editor: http://{}:{}", host, port)

    import uvicorn  # noqa: PLC0415

    from job_scout.cv.app import create_app  # noqa: PLC0415

    uvicorn.run(create_app(store), host=host, port=port)


@cv.command("render")
@click.argument("slug")
@_USER_OPTION
@click.option(
    "-o",
    "--output",
    type=click.Path(dir_okay=False, path_type=Path),
    default=Path("cv.pdf"),
    show_default=True,
    help="Output file.",
)
def render(slug: str, user_name: str | None, output: Path) -> None:
    """Render a stored profile straight to a PDF file."""
    store = _store_for_user(user_name)
    doc = _load_profile(store, slug)
    pages = _write_pdf(store, slug, doc, output)
    click.echo(f"Wrote {output} ({pages} page(s))")


@cv.command("list")
@_USER_OPTION
def list_profiles(user_name: str | None) -> None:
    """List the stored CV profiles."""
    store = _store_for_user(user_name)
    slugs = store.list_profiles()
    if not slugs:
        click.echo(f"No profiles yet in {store.root}")
        return
    for slug in slugs:
        click.echo(slug)


def _tailor_or_exit(
    doc: CVDocument,
    job: JobListing,
    client: LLMClient,
    memories: Sequence[Mapping[str, Any]] = (),
) -> TailoredCV:
    """Tailor a document, turning a model failure into a one-line message.

    Args:
        doc: The source document.
        job: The vacancy to tailor towards.
        client: LLM client to tailor with.
        memories: The applicant's memories allowed on a CV, possibly none.

    Returns:
        The tailored copy, and the memories it left out.
    """
    try:
        return tailor_cv(doc, job, client, memories=memories)
    except (LLMError, TailorError) as exc:
        click.echo(f"Tailoring failed: {exc}", err=True)
        sys.exit(1)


def _save_tailored(
    store: ProfileStore, source_slug: str, slug: str, doc: CVDocument
) -> Path:
    """Save a tailored document as a profile of its own, portrait included.

    The portrait is copied rather than shared: a profile owns everything in its
    own directory, so the tailored CV shows the same face whether it is rendered
    here or opened in the editor. A portrait that cannot be copied is logged and
    skipped -- losing the photograph is not worth losing the tailored CV over.

    Args:
        store: The user's profile store.
        source_slug: Slug the document was tailored from.
        slug: Slug to save the tailored copy under.
        doc: The tailored document.

    Returns:
        The path of the written ``cv.json``.
    """
    try:
        document = store.save(slug, doc)
    except StorageError as exc:
        click.echo(str(exc), err=True)
        sys.exit(1)

    portrait = store.photo_path(source_slug, doc)
    if portrait is None:
        return document
    try:
        store.save_photo(slug, portrait.read_bytes())
    except (StorageError, OSError) as exc:
        logger.warning("Could not copy the portrait into {}: {}", slug, exc)
    return document


@cv.command("tailor")
@click.argument("job_id", type=int)
@_USER_OPTION
@click.option(
    "--slug",
    default=DEFAULT_PROFILE,
    show_default=True,
    help="Profile to tailor. It is read, never written.",
)
@click.option(
    "-o",
    "--output",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Output file. Defaults to <tailored slug>.pdf in the current directory.",
)
def tailor(job_id: int, user_name: str | None, slug: str, output: Path | None) -> None:
    """Tailor a stored CV to one vacancy and save it as a new profile.

    The tailored CV is written under a slug of its own, so the profile it was
    built from is left exactly as it was and can be tailored again to the next
    vacancy.
    """
    target = _resolve_user(user_name)
    store = ProfileStore(user_cv_dir(target))
    doc = _load_profile(store, slug)
    job = _load_job(target, job_id)

    # The client is built before the announcement, so a misconfigured provider
    # does not first claim that tailoring has started.
    client = _client_for_user(target)
    memories = memories_for_vacancy(target, MemoryUse.CV, job)
    click.echo(f"Tailoring {slug!r} to {job.title} at {job.company}...")
    if len(memories) == 1:
        click.echo("Offering 1 memory to use where it fits.")
    elif memories:
        click.echo(f"Offering {len(memories)} memories to use where they fit.")
    result = _tailor_or_exit(doc, job, client, memories)
    for unused in result.memories_not_used:
        click.echo(unused.describe(), err=True)
    tailored = result.document

    new_slug = tailored_slug(slug, job)
    document = _save_tailored(store, slug, new_slug, tailored)
    destination = output or Path(f"{new_slug}.pdf")
    pages = _write_pdf(store, new_slug, tailored, destination)

    click.echo(f"Saved profile {new_slug!r} to {document}")
    click.echo(f"Wrote {destination} ({pages} page(s))")
