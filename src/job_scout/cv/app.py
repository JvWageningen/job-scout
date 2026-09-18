"""FastAPI application backing the CV editor.

The API is deliberately small: profiles are CRUD, and rendering has two flavours -
an inline preview that renders whatever the editor currently holds (so unsaved edits
are visible) and a download that renders what is on disk.
"""

import io
import re
from collections.abc import Awaitable, Callable
from datetime import date
from pathlib import Path
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    File,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
from pydantic import BaseModel, Field

from job_scout.cv import __version__
from job_scout.cv.models import CVDocument, Theme
from job_scout.cv.placeholders import with_placeholders
from job_scout.cv.render import render_pdf
from job_scout.cv.render.icons import icon_names
from job_scout.cv.sample import blank_cv
from job_scout.cv.storage import ProfileStore, StorageError, normalise_slug

MAX_PHOTO_BYTES = 12 * 1024 * 1024
_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._ -]+")

STATIC_DIR = Path(__file__).parent / "static"


class ProfileSummary(BaseModel):
    """A profile as listed in the dashboard's picker."""

    slug: str
    full_name: str


class CreateProfileRequest(BaseModel):
    """Payload for creating a profile."""

    name: str = Field(min_length=1, max_length=120)


class PhotoResponse(BaseModel):
    """Result of a portrait upload."""

    photo: str


def get_store(request: Request) -> ProfileStore:
    """Return the store attached to the running application.

    Defined at module level on purpose: FastAPI resolves route annotations
    against this module's globals, so a dependency alias declared inside
    :func:`create_app` would be invisible to it. Module level also makes this the
    stable key a host application overrides via ``app.dependency_overrides`` when
    the router is mounted and the store has to vary per request - which is how
    job-scout hands each user their own CV directory.

    Args:
        request: The incoming request.

    Returns:
        The application's profile store.
    """
    store: ProfileStore = request.app.state.store
    return store


StoreDep = Annotated[ProfileStore, Depends(get_store)]


def _download_name(doc: CVDocument, today: date | None = None) -> str:
    """Build a safe ``Content-Disposition`` filename for a document.

    Downloads are stamped ``YYYYMMDD`` and tagged with the document's language, so
    a folder of applications sorts chronologically and the English and Dutch
    versions never overwrite each other.

    Args:
        doc: The document being downloaded.
        today: Date to stamp. Defaults to the current local date; injected by
            tests.

    Returns:
        An ASCII-safe filename such as
        ``20260809 Jane Doe CV EN.pdf``.
    """
    stamp = (today or date.today()).strftime("%Y%m%d")
    stem = _UNSAFE_FILENAME.sub("", doc.full_name).strip()
    language = _UNSAFE_FILENAME.sub("", doc.language).strip().upper()
    parts = [stamp, *([stem] if stem else []), "CV", *([language] if language else [])]
    return " ".join(parts) + ".pdf"


def build_api_router() -> APIRouter:
    """Build the CV API as a router any application can mount.

    Declaring the endpoints on a router rather than straight onto an app is what
    lets job-scout's dashboard serve this API under ``/api/cv`` from its own
    process while :func:`create_app` keeps serving it under ``/api`` standalone.
    Paths are relative to the mount point (``/meta``, ``/profiles``, ...).

    Returns:
        A router holding every CV endpoint.
    """
    router = APIRouter()

    def load_or_404(profiles: ProfileStore, slug: str) -> CVDocument:
        """Load a profile, translating storage failures into HTTP errors.

        Args:
            profiles: The store to read from.
            slug: Profile slug.

        Returns:
            The parsed document.

        Raises:
            HTTPException: 404 when missing, 400 when the slug or file is bad.
        """
        try:
            return profiles.load(slug)
        except StorageError as exc:
            status = 404 if "does not exist" in str(exc) else 400
            raise HTTPException(status_code=status, detail=str(exc)) from exc

    # -- meta ----------------------------------------------------------

    @router.get("/meta")
    def meta() -> dict[str, Any]:
        """Describe the vocabulary the dashboard needs to build its forms."""
        return {
            "version": __version__,
            "icons": icon_names(),
            "section_kinds": [
                "text",
                "experience",
                "education",
                "skills",
                "details",
                "contact",
                "list",
            ],
            "theme_defaults": Theme().model_dump(mode="json"),
        }

    # -- profiles ------------------------------------------------------

    @router.get("/profiles")
    def list_profiles(profiles: StoreDep) -> list[ProfileSummary]:
        """List every stored profile, creating empty starter ones when none exist."""
        profiles.ensure_default()
        summaries: list[ProfileSummary] = []
        for slug in profiles.list_profiles():
            try:
                doc = profiles.load(slug)
            except StorageError as exc:
                logger.warning("Skipping unreadable profile {}: {}", slug, exc)
                continue
            summaries.append(ProfileSummary(slug=slug, full_name=doc.full_name))
        return summaries

    @router.post("/profiles", status_code=201)
    def create_profile(
        payload: CreateProfileRequest, profiles: StoreDep
    ) -> ProfileSummary:
        """Create an empty profile; the preview shows placeholders until filled."""
        try:
            slug = normalise_slug(payload.name)
        except StorageError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if profiles.exists(slug):
            raise HTTPException(
                status_code=409, detail=f"Profile {slug!r} already exists"
            )

        doc = blank_cv()
        doc.full_name = payload.name
        try:
            profiles.save(slug, doc)
        except StorageError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return ProfileSummary(slug=slug, full_name=doc.full_name)

    @router.get("/profiles/{slug}")
    def get_profile(slug: str, profiles: StoreDep) -> CVDocument:
        """Return one profile's document."""
        return load_or_404(profiles, slug)

    @router.put("/profiles/{slug}")
    def save_profile(slug: str, doc: CVDocument, profiles: StoreDep) -> CVDocument:
        """Overwrite one profile's document."""
        try:
            profiles.save(slug, doc)
        except StorageError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return doc

    @router.delete("/profiles/{slug}", status_code=204)
    def delete_profile(slug: str, profiles: StoreDep) -> Response:
        """Delete a profile and its uploads."""
        try:
            profiles.delete(slug)
        except StorageError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return Response(status_code=204)

    # -- portrait ------------------------------------------------------

    @router.post("/profiles/{slug}/photo")
    async def upload_photo(
        slug: str, profiles: StoreDep, file: Annotated[UploadFile, File()]
    ) -> PhotoResponse:
        """Store a portrait, replacing any previous one."""
        doc = load_or_404(profiles, slug)
        data = await file.read()
        if not data:
            raise HTTPException(status_code=400, detail="Uploaded file is empty")
        if len(data) > MAX_PHOTO_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"Image is larger than {MAX_PHOTO_BYTES // (1024 * 1024)} MB",
            )
        try:
            filename = profiles.save_photo(slug, data)
        except StorageError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        doc.photo = filename
        profiles.save(slug, doc)
        return PhotoResponse(photo=filename)

    @router.get("/profiles/{slug}/photo")
    def get_photo(slug: str, profiles: StoreDep) -> FileResponse:
        """Serve the stored portrait."""
        doc = load_or_404(profiles, slug)
        path = profiles.photo_path(slug, doc)
        if path is None:
            raise HTTPException(status_code=404, detail="No portrait uploaded")
        return FileResponse(path, media_type="image/png")

    @router.delete("/profiles/{slug}/photo", status_code=204)
    def delete_photo(slug: str, profiles: StoreDep) -> Response:
        """Forget the portrait without touching the rest of the document."""
        doc = load_or_404(profiles, slug)
        path = profiles.photo_path(slug, doc)
        if path is not None:
            path.unlink(missing_ok=True)
        doc.photo = ""
        profiles.save(slug, doc)
        return Response(status_code=204)

    # -- rendering -----------------------------------------------------

    def _render(profiles: ProfileStore, slug: str, doc: CVDocument) -> bytes:
        """Render a document to PDF bytes.

        Args:
            profiles: Store used to resolve the portrait.
            slug: Profile slug.
            doc: Document to render.

        Returns:
            The PDF payload.

        Raises:
            HTTPException: 500 if ReportLab could not produce a document.
        """
        buffer = io.BytesIO()
        try:
            render_pdf(doc, buffer, profiles.photo_path(slug, doc))
        except (ValueError, OSError) as exc:
            logger.exception("Render failed for {}", slug)
            raise HTTPException(
                status_code=500, detail=f"Could not render PDF: {exc}"
            ) from exc
        return buffer.getvalue()

    @router.post("/profiles/{slug}/preview")
    def preview(slug: str, doc: CVDocument, profiles: StoreDep) -> StreamingResponse:
        """Render the posted document inline, without saving it.

        Empty fields are shown as lorem ipsum so the layout is visible before
        anything is filled in. Only this preview does that: the download renders
        exactly what is saved.
        """
        payload = _render(profiles, slug, with_placeholders(doc))
        return StreamingResponse(
            io.BytesIO(payload),
            media_type="application/pdf",
            headers={"Content-Disposition": 'inline; filename="preview.pdf"'},
        )

    @router.get("/profiles/{slug}/pdf")
    def download(slug: str, profiles: StoreDep) -> StreamingResponse:
        """Render the saved document as a download."""
        doc = load_or_404(profiles, slug)
        payload = _render(profiles, slug, doc)
        return StreamingResponse(
            io.BytesIO(payload),
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{_download_name(doc)}"'
            },
        )

    return router


def create_app(store: ProfileStore | None = None) -> FastAPI:
    """Build the standalone application.

    Args:
        store: Profile store to use. Defaults to one rooted at
            :func:`~job_scout.cv.storage.default_data_dir`, which is what the CLI
            uses; tests inject a temporary one.

    Returns:
        The configured FastAPI application.
    """
    app = FastAPI(title="cv-builder", version=__version__)
    app.state.store = store or ProfileStore()

    # -- pages ---------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def index() -> HTMLResponse:
        """Serve the dashboard shell."""
        return HTMLResponse((STATIC_DIR / "index.html").read_text(encoding="utf-8"))

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> FileResponse:
        """Serve the tab icon.

        Browsers request ``/favicon.ico`` regardless of the ``<link rel=icon>``
        tag, so without this every page load logs a 404.
        """
        return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")

    # The same router job-scout mounts under ``/api/cv``; standalone it keeps the
    # flat ``/api`` paths the editor has always called.
    app.include_router(build_api_router(), prefix="/api")

    @app.middleware("http")
    async def revalidate_static(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Force the browser to revalidate dashboard assets on every load.

        Without this the browser happily serves a cached ``app.js`` after the file
        changes on disk, so edits appear to do nothing until a hard refresh.
        ``no-cache`` still allows a 304 via the ETag, so it costs almost nothing.

        Args:
            request: The incoming request.
            call_next: The rest of the middleware chain.

        Returns:
            The response, with cache headers adjusted for static assets.
        """
        response = await call_next(request)
        if request.url.path.startswith("/static/") or request.url.path == "/":
            response.headers["Cache-Control"] = "no-cache"
        return response

    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    return app
