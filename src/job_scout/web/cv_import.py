"""Import the CV an applicant already has into CV Builder.

Declared on the dashboard rather than on the vendored CV router, like tailoring:
it needs the user's settings, their parsed profile and their LLM, none of which
the standalone editor has. The editor shows its Import button only when
``/import/options`` answers, so standalone it simply is not offered.

CV Builder stays optional. Letters and interview material read the applicant's
own CV file whether or not it was ever imported (see :mod:`job_scout.applicant`);
importing is for the applicant who wants to edit and lay out that CV here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from loguru import logger
from pydantic import BaseModel

from job_scout.applicant import ApplicantError, cached_profile, require_user
from job_scout.config import build_effective_config, user_cv_dir, user_db_path
from job_scout.cv.importer import CVImportError, transcribe_cv
from job_scout.cv.sample import has_career_content
from job_scout.cv.storage import ProfileStore, StorageError, normalise_slug
from job_scout.cv_parser import parse_cv
from job_scout.database import Database
from job_scout.letters.examples import MAX_BYTES, ExampleError, extract_text
from job_scout.llm.base import LLMError
from job_scout.llm.factory import get_llm_client
from job_scout.models import Config

_PROFILE_LABEL = "your parsed profile (including any LinkedIn import)"


class ImportResult(BaseModel):
    """What the editor needs after an import: where it went and what to check."""

    slug: str
    full_name: str
    warnings: list[str]
    sources_used: list[str]


def _user(user: str) -> str:
    """Validate the user, as a 400 rather than a server error.

    Args:
        user: The ``?user=`` parameter.

    Returns:
        The validated user.

    Raises:
        HTTPException: If the user is unknown or unsafe.
    """
    try:
        return require_user(user)
    except ApplicantError as exc:
        raise HTTPException(400, str(exc)) from exc


def _settings_cv(config: Config) -> Path | None:
    """Return the CV file configured in Settings, when it exists.

    Args:
        config: The user's effective configuration.

    Returns:
        The file, or None.
    """
    if not config.cv_path:
        return None
    path = Path(config.cv_path)
    return path if path.is_file() else None


def _settings_text(config: Config) -> tuple[str, str]:
    """Read the CV file configured in Settings.

    Args:
        config: The user's effective configuration.

    Returns:
        The CV text and a label for it.

    Raises:
        HTTPException: If no readable CV is configured.
    """
    path = _settings_cv(config)
    if path is None:
        raise HTTPException(
            400, "No CV is uploaded under Profile & Filters. Upload one here instead."
        )
    text = parse_cv(path)
    if not text.strip():
        raise HTTPException(400, f"No text could be read from {path.name}.")
    return text, f"your uploaded CV ({path.name})"


async def _upload_text(file: UploadFile | None) -> tuple[str, str]:
    """Read an uploaded CV.

    Args:
        file: The upload, if any.

    Returns:
        The CV text and a label for it.

    Raises:
        HTTPException: If nothing usable was uploaded.
    """
    if file is None:
        raise HTTPException(400, "Choose a file to upload.")
    try:
        data = await file.read(MAX_BYTES + 1)
        text = extract_text(file.filename or "", data, kind="CV")
    except ExampleError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        await file.close()
    return text, f"the uploaded file ({Path(file.filename or 'CV').name})"


def _target_slug(store: ProfileStore, name: str) -> str:
    """Choose where the import is saved, never over a CV someone wrote.

    Args:
        store: The user's CV profiles.
        name: The profile name the user gave.

    Returns:
        The slug to save under: new, or an existing profile that is still empty.

    Raises:
        HTTPException: If the name is unusable or already holds a CV.
    """
    try:
        slug = normalise_slug(name)
    except StorageError as exc:
        raise HTTPException(400, str(exc)) from exc
    if store.exists(slug) and has_career_content(store.load(slug)):
        raise HTTPException(
            409, f"Profile '{slug}' already holds a CV. Choose another name."
        )
    return slug


async def _source(
    source: str, user: str, config: Config, file: UploadFile | None
) -> tuple[str, list[str], dict[str, Any] | None]:
    """Read the CV to import, and the parsed profile that may extend it.

    The parsed profile, which is where a LinkedIn import lands, is keyed on the
    Settings CV. It is only offered alongside that CV: next to a different,
    newer upload it could bring back a role the applicant removed.

    Args:
        source: "settings" or "upload".
        user: Name of an existing user.
        config: The user's effective configuration.
        file: The upload, for the "upload" source.

    Returns:
        The CV text, labels of the sources used, and the parsed profile or None.
    """
    if source != "settings":
        text, label = await _upload_text(file)
        return text, [label], None
    text, label = _settings_text(config)
    supplement = cached_profile(Database(user_db_path(user)), config, text)
    if supplement is None:
        return text, [label], None
    return text, [label, _PROFILE_LABEL], supplement


def build_cv_import_router() -> APIRouter:
    """Build the import routes, mounted under ``/api/cv``.

    Returns:
        A router with ``GET /import/options`` and ``POST /import``.
    """
    router = APIRouter()

    @router.get("/import/options")
    def import_options(user: str) -> dict[str, object]:
        """Say what can be imported: the Settings CV and a LinkedIn import."""
        config = build_effective_config(_user(user))
        path = _settings_cv(config)
        linkedin = False
        if path is not None:
            text = parse_cv(path)
            db = Database(user_db_path(user))
            linkedin = bool(text) and cached_profile(db, config, text) is not None
        return {"settings_cv": path.name if path else "", "linkedin": linkedin}

    @router.post("/import")
    async def import_cv(
        user: str,
        source: Annotated[Literal["settings", "upload"], Form()],
        language: Annotated[Literal["EN", "NL"], Form()],
        name: Annotated[str, Form(min_length=1, max_length=120)],
        file: Annotated[UploadFile | None, File()] = None,
    ) -> ImportResult:
        """Transcribe the applicant's CV into a new, editable profile."""
        config = build_effective_config(_user(user))
        store = ProfileStore(user_cv_dir(user))
        slug = _target_slug(store, name)
        text, used, supplement = await _source(source, user, config, file)
        try:
            doc, warnings = transcribe_cv(
                text, language, get_llm_client(config), supplement=supplement
            )
        except CVImportError as exc:
            raise HTTPException(422, str(exc)) from exc
        except LLMError as exc:
            logger.error(f"CV import failed for {user!r}: {type(exc).__name__}: {exc}")
            raise HTTPException(
                502, "The model could not read the CV. Check LLM settings and retry."
            ) from exc
        store.save(slug, doc)
        logger.info(f"Imported a CV for {user} as profile '{slug}'")
        return ImportResult(
            slug=slug, full_name=doc.full_name, warnings=warnings, sources_used=used
        )

    return router
