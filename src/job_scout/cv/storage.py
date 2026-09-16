"""On-disk persistence for CV profiles.

Each profile is a directory holding ``cv.json`` plus an ``uploads`` folder for its
portrait, so a profile can be copied or backed up by moving one directory::

    data/profiles/<slug>/cv.json
    data/profiles/<slug>/uploads/portrait.png

Profile slugs come from the HTTP layer, so :func:`normalise_slug` is the single
gate that keeps them from escaping the data directory.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import unicodedata
from pathlib import Path

from loguru import logger
from pydantic import ValidationError

from job_scout.cv.images import ImageError, load_square
from job_scout.cv.models import CVDocument
from job_scout.cv.sample import sample_cv, sample_portrait

DATA_ENV_VAR = "CV_BUILDER_DATA"
DEFAULT_PROFILE = "default"
STARTER_PROFILES: tuple[tuple[str, str], ...] = (
    (DEFAULT_PROFILE, "EN"),
    ("nederlands", "NL"),
)
"""Profiles seeded on a fresh install, as ``(slug, language)`` pairs."""
_SLUG_SAFE = re.compile(r"[^a-z0-9]+")
_MAX_SLUG_LENGTH = 60
PORTRAIT_NAME = "portrait.png"


class StorageError(RuntimeError):
    """Raised when a profile cannot be read, written or named."""


def normalise_slug(value: str) -> str:
    """Reduce arbitrary text to a safe single path segment.

    Accents are folded, everything outside ``[a-z0-9]`` becomes a hyphen, and the
    result can never be empty, ``.`` or ``..`` - so it is always safe to join onto
    the data directory.

    Args:
        value: Raw profile name or slug.

    Returns:
        A filesystem-safe slug.

    Raises:
        StorageError: If nothing usable remains after normalisation.
    """
    folded = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    slug = _SLUG_SAFE.sub("-", folded.lower()).strip("-")[:_MAX_SLUG_LENGTH].strip("-")
    if not slug:
        raise StorageError(f"{value!r} does not contain any usable characters")
    return slug


def default_data_dir() -> Path:
    """Return the data directory, honouring the ``CV_BUILDER_DATA`` override.

    Returns:
        Absolute path to the data directory.
    """
    override = os.environ.get(DATA_ENV_VAR)
    if override:
        return Path(override).expanduser().resolve()
    return (Path.cwd() / "data").resolve()


class ProfileStore:
    """Reads and writes CV profiles under a single data directory."""

    def __init__(self, root: Path | None = None) -> None:
        """Initialise the store.

        Args:
            root: Data directory. Defaults to :func:`default_data_dir`.
        """
        self.root = (root or default_data_dir()).resolve()
        self.profiles_dir = self.root / "profiles"

    # -- paths ---------------------------------------------------------

    def profile_dir(self, slug: str) -> Path:
        """Return the directory for ``slug``, creating nothing.

        Args:
            slug: Profile slug, normalised before use.

        Returns:
            Path to the profile directory.
        """
        return self.profiles_dir / normalise_slug(slug)

    def document_path(self, slug: str) -> Path:
        """Return the ``cv.json`` path for ``slug``.

        Args:
            slug: Profile slug.

        Returns:
            Path to the document file.
        """
        return self.profile_dir(slug) / "cv.json"

    def uploads_dir(self, slug: str) -> Path:
        """Return the uploads directory for ``slug``.

        Args:
            slug: Profile slug.

        Returns:
            Path to the uploads directory.
        """
        return self.profile_dir(slug) / "uploads"

    def photo_path(self, slug: str, doc: CVDocument) -> Path | None:
        """Resolve a document's portrait to a path inside its uploads directory.

        Args:
            slug: Profile slug.
            doc: The document whose ``photo`` field is resolved.

        Returns:
            The portrait path, or ``None`` when there is no usable portrait.
        """
        if not doc.photo:
            return None
        # Only the bare filename is honoured, so a crafted document cannot point
        # the renderer at an arbitrary file.
        candidate = self.uploads_dir(slug) / Path(doc.photo).name
        return candidate if candidate.is_file() else None

    # -- listing -------------------------------------------------------

    def list_profiles(self) -> list[str]:
        """List the slugs of every stored profile.

        Returns:
            Sorted slugs, empty when nothing has been saved yet.
        """
        if not self.profiles_dir.is_dir():
            return []
        return sorted(
            entry.name
            for entry in self.profiles_dir.iterdir()
            if entry.is_dir() and (entry / "cv.json").is_file()
        )

    def exists(self, slug: str) -> bool:
        """Whether a profile has been saved.

        Args:
            slug: Profile slug.

        Returns:
            True if ``cv.json`` exists.
        """
        return self.document_path(slug).is_file()

    # -- reading and writing -------------------------------------------

    def load(self, slug: str) -> CVDocument:
        """Load a profile's document.

        Args:
            slug: Profile slug.

        Returns:
            The parsed document.

        Raises:
            StorageError: If the profile is missing or its JSON is invalid.
        """
        path = self.document_path(slug)
        if not path.is_file():
            raise StorageError(f"Profile {slug!r} does not exist")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return CVDocument.model_validate(payload)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise StorageError(f"Profile {slug!r} is corrupt: {exc}") from exc
        except OSError as exc:
            raise StorageError(f"Could not read profile {slug!r}: {exc}") from exc

    def save(self, slug: str, doc: CVDocument) -> Path:
        """Write a profile's document atomically.

        The JSON is written to a sibling temporary file and then moved into place,
        so an interrupted save cannot truncate the previous version.

        Args:
            slug: Profile slug.
            doc: Document to persist.

        Returns:
            The path written.

        Raises:
            StorageError: If the document could not be written.
        """
        path = self.document_path(slug)
        temporary = path.with_suffix(".json.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.uploads_dir(slug).mkdir(parents=True, exist_ok=True)
            temporary.write_text(
                json.dumps(doc.model_dump(mode="json"), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            temporary.replace(path)
        except OSError as exc:
            raise StorageError(f"Could not save profile {slug!r}: {exc}") from exc
        logger.debug("Saved profile {} to {}", slug, path)
        return path

    def delete(self, slug: str) -> None:
        """Remove a profile and everything in it.

        Args:
            slug: Profile slug.

        Raises:
            StorageError: If the profile is missing or could not be removed.
        """
        directory = self.profile_dir(slug)
        if not directory.is_dir():
            raise StorageError(f"Profile {slug!r} does not exist")
        try:
            shutil.rmtree(directory)
        except OSError as exc:
            raise StorageError(f"Could not delete profile {slug!r}: {exc}") from exc
        logger.info("Deleted profile {}", slug)

    # -- portraits -----------------------------------------------------

    def save_photo(self, slug: str, data: bytes) -> str:
        """Normalise and store a portrait for a profile.

        The image is squared and downscaled on the way in, so whatever the browser
        uploaded is not what ends up on disk.

        Args:
            slug: Profile slug.
            data: Raw uploaded bytes.

        Returns:
            The stored filename, suitable for ``CVDocument.photo``.

        Raises:
            StorageError: If the bytes are not a usable image or cannot be written.
        """
        try:
            image = load_square(data)
        except ImageError as exc:
            raise StorageError(str(exc)) from exc

        destination = self.uploads_dir(slug) / PORTRAIT_NAME
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            image.save(destination, format="PNG")
        except OSError as exc:
            raise StorageError(f"Could not store portrait: {exc}") from exc
        logger.debug("Stored portrait for {} at {}", slug, destination)
        return PORTRAIT_NAME

    # -- bootstrap -----------------------------------------------------

    def seed(self, slug: str, doc: CVDocument) -> None:
        """Write a starter profile together with the bundled portrait.

        A missing or unreadable portrait is not fatal; the profile is simply saved
        without one.

        Args:
            slug: Profile slug.
            doc: Document to persist.
        """
        self.save(slug, doc)
        portrait = sample_portrait()
        if not portrait.is_file():
            doc.photo = ""
            self.save(slug, doc)
            return
        try:
            self.save_photo(slug, portrait.read_bytes())
        except (StorageError, OSError) as exc:
            logger.warning("Could not seed sample portrait for {}: {}", slug, exc)
            doc.photo = ""
            self.save(slug, doc)

    def ensure_default(self) -> str:
        """Create the seeded starter profiles if no profiles exist yet.

        Both language versions are seeded, so the English and Dutch CVs are
        available side by side from the first run.

        Returns:
            The slug of an existing profile: the newly seeded English one, or the
            first profile already present.
        """
        existing = self.list_profiles()
        if existing:
            return existing[0]

        for slug, language in STARTER_PROFILES:
            self.seed(slug, sample_cv(language))
        logger.info(
            "Seeded starter profiles {} in {}",
            ", ".join(slug for slug, _ in STARTER_PROFILES),
            self.profiles_dir,
        )
        return DEFAULT_PROFILE
