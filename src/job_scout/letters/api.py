"""Per-user letter editor API, protected by the dashboard token middleware."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile
from loguru import logger
from pydantic import BaseModel, Field
from starlette.responses import Response

from job_scout.applicant import describe_sources, profile_choices
from job_scout.config import build_effective_config
from job_scout.cv.storage import StorageError
from job_scout.letters.examples import (
    MAX_BYTES,
    ExampleError,
    add_example,
    delete_example,
    list_example_summaries,
    list_examples,
)
from job_scout.letters.models import (
    ExampleSummary,
    Letter,
    LetterLanguage,
    LetterRequest,
)
from job_scout.letters.style import (
    StyleError,
    derive_style_guide,
    load_style_guide,
    save_style_guide,
)
from job_scout.letters.writer import (
    LetterError,
    letter_pdf_bytes,
    load_letter,
    require_user,
    save_letter,
    write_letter,
)
from job_scout.llm.base import LLMError
from job_scout.llm.factory import get_llm_client
from job_scout.memories import MemorySource, MemoryUse
from job_scout.web.memories_api import start_capture
from job_scout.web.vacancies import open_vacancy_choices


class StyleBody(BaseModel):
    """An editable, private style guide."""

    markdown: str = Field(max_length=16000)


def checked_user(user: str, response: Response) -> Iterator[str]:
    """Validate the user and translate expected domain failures into API errors."""
    response.headers["Cache-Control"] = "no-store"
    try:
        yield require_user(user)
    except (LetterError, ExampleError, StorageError, ValueError) as exc:
        logger.warning("Letter request rejected for {!r}: {}", user, exc)
        raise HTTPException(400, str(exc)) from exc
    except (LLMError, StyleError) as exc:
        # The client message stays deliberately vague -- provider errors can carry
        # endpoints and key fragments. The operator still needs the real cause, so
        # it goes to the log rather than nowhere.
        logger.error(
            "Letter request failed for {!r}: {}: {}", user, type(exc).__name__, exc
        )
        raise HTTPException(
            502,
            "The model could not complete the request. Check LLM settings and retry.",
        ) from exc
    except OSError as exc:
        logger.error("Letter data unreadable for {!r}: {}", user, exc)
        raise HTTPException(
            503, "Private letter data could not be read or saved."
        ) from exc


User = Annotated[str, Depends(checked_user)]


def build_api_router() -> APIRouter:
    """Build the letter routes; no personal documents are packaged with the app."""
    router = APIRouter()

    @router.get("/context")
    def context(user: User) -> dict[str, object]:
        """List usable vacancies and CVs without returning their private contents.

        Only vacancies still worth applying to are offered, best match first, so
        the top of the list is the obvious place to start. The shortlist itself
        is built by ``open_vacancy_choices``, which the interview tab calls too:
        one definition, so the two lists cannot drift apart.
        """
        return {
            "jobs": open_vacancy_choices(user),
            "profiles": profile_choices(user),
            "sources": describe_sources(user, MemoryUse.LETTER),
        }

    @router.get("/examples")
    def examples(user: User) -> list[ExampleSummary]:
        """List imported examples without returning their text."""
        return list_example_summaries(user)

    @router.post("/examples", status_code=201)
    async def upload_example(user: User, file: UploadFile) -> ExampleSummary:
        """Read a bounded upload and keep its original in this user's data."""
        try:
            data = await file.read(MAX_BYTES + 1)
            return add_example(user, file.filename or "", data)
        finally:
            await file.close()

    @router.delete("/examples/{name}", status_code=204)
    def remove_example(name: str, user: User) -> Response:
        """Delete exactly one example belonging to the selected user."""
        delete_example(user, name)
        return Response(status_code=204)

    @router.get("/style")
    def style(user: User) -> StyleBody:
        """Read the user's editable style guide."""
        return StyleBody(markdown=load_style_guide(user) or "")

    @router.put("/style")
    def update_style(body: StyleBody, user: User) -> StyleBody:
        """Save an explicitly reviewed style guide."""
        if not body.markdown.strip():
            raise LetterError("The style guide cannot be empty.")
        save_style_guide(user, body.markdown)
        return body

    @router.post("/style/derive")
    def derive_style(user: User) -> StyleBody:
        """Propose a guide for review without replacing a saved guide."""
        guide = derive_style_guide(
            list_examples(user), get_llm_client(build_effective_config(user))
        )
        return StyleBody(markdown=guide)

    @router.post("/generate")
    def generate(
        body: LetterRequest, user: User, response: Response, tasks: BackgroundTasks
    ) -> Letter:
        """Draft using the current CV; leave saved letters untouched.

        With notes, the facts about the applicant in them are turned into
        memories after the letter is sent back (see ``start_capture``).
        """
        client = get_llm_client(build_effective_config(user))
        letter = write_letter(user, body, client)
        start_capture(
            tasks,
            response,
            user,
            body.notes,
            source=MemorySource.LETTER_NOTES,
            job_id=body.job_id,
            client=client,
        )
        return letter

    @router.get("/draft/{job_id}")
    def draft(job_id: int, language: LetterLanguage, user: User) -> Letter:
        """Read the saved draft in the requested language."""
        letter = load_letter(user, job_id, language)
        if letter is None:
            raise HTTPException(404, "No saved draft for this vacancy and language.")
        return letter

    @router.put("/draft/{job_id}")
    def update_draft(job_id: int, body: Letter, user: User) -> Letter:
        """Persist an edited letter without overwriting the other language."""
        if body.job_id != job_id:
            raise LetterError("Vacancy ID does not match the draft.")
        save_letter(user, body)
        return body

    @router.post("/pdf")
    def pdf(body: Letter, user: User) -> Response:
        """Render the current editor contents using the CV's theme."""
        return Response(
            letter_pdf_bytes(user, body),
            media_type="application/pdf",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="letter-{body.job_id}-'
                    f'{body.language.value}.pdf"'
                ),
                "Cache-Control": "no-store",
            },
        )

    return router
