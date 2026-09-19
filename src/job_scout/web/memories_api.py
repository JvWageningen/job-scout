"""The Memories tab's API, and the capture that runs after letters and interviews.

Mounted under ``/api/memories`` inside the prefix TokenAuthMiddleware guards,
like the letter and interview routers, and validated per user the same way:
every route takes ``?user=`` and reads and writes only that user's database.

Two ways to add memories run through here. By hand, the applicant writes the
statement and it is stored as written. From a text, ``/extract`` asks the
model for proposed memories and stores nothing; the page shows them to tick
and edit, and ``/batch`` stores the ones the applicant kept. The third way,
automatic capture from the notes typed for a letter or interview set, is
started by :func:`start_capture`, which the letter and interview routes call
after a generation; it runs as a background task, after the response is sent.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Annotated

import yaml
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, UploadFile
from loguru import logger
from pydantic import BaseModel, Field, StrictBool
from starlette.responses import Response

from job_scout.config import build_effective_config, user_db_path
from job_scout.database import Database
from job_scout.letters.examples import MAX_BYTES, extract_text
from job_scout.llm.base import LLMClient, LLMError
from job_scout.llm.factory import get_llm_client
from job_scout.memories import (
    MAX_SOURCE_DETAIL_CHARS,
    ForgottenMemory,
    Memory,
    MemoryContent,
    MemoryDraft,
    MemorySource,
    add_memories,
    add_memory,
    clear_forgotten,
    delete_memory,
    describe_origin,
    find_duplicate,
    list_forgotten,
    list_memories,
    require_memory_user,
    update_memory,
)
from job_scout.memory_extract import (
    MAX_DRAFTS,
    MAX_INPUT_CHARS,
    auto_capture_enabled,
    capture_from_notes,
    capture_pending,
    extract_memories,
    set_auto_capture,
)

# Tells the letter and interview pages that facts from the notes are being
# turned into memories, without changing the shape of what they get back.
CAPTURE_HEADER = "X-Memory-Capture"
# Where a pasted text came from, when the page names no file.
PASTED_DETAIL = "text pasted in the Memories tab"
# A body this long is not a text to read but a mistake; the extractor's own,
# lower limit gives the message the applicant sees.
_MAX_BODY_CHARS = 5 * MAX_INPUT_CHARS
_NOT_FOUND = "No memory with that number. It may have been deleted already."
_VAGUE = "The model could not complete the request. Check LLM settings and retry."
# What checking or naming a capture can raise. None of it may cost the
# applicant the letter or interview set that was just generated.
_CAPTURE_CHECK_ERRORS = (ValueError, sqlite3.Error, OSError, yaml.YAMLError)
# How a vacancy is named in the origin of a memory taken from notes.
_PREPOSITIONS = {"letter": "to", "interview": "at"}


class MemoryItem(Memory):
    """A stored memory as the Memories tab shows it.

    Attributes:
        origin: Where it came from in words, such as "taken from letter notes
            (notes for the Data analyst letter to Findwhere)".
        cited_as: The label a generated answer cites it by, "memory 3".
    """

    origin: str
    cited_as: str

    @classmethod
    def of(cls, memory: Memory) -> MemoryItem:
        """Describe a stored memory for the page.

        Args:
            memory: The stored memory.

        Returns:
            The memory with its origin and label.
        """
        extra = {"origin": describe_origin(memory), "cited_as": memory.label}
        return cls.model_validate({**memory.model_dump(), **extra})


class DraftItem(MemoryDraft):
    """A proposed memory as the page shows it for ticking and editing.

    Attributes:
        origin: Where it came from in words.
    """

    origin: str

    @classmethod
    def of(cls, draft: MemoryDraft) -> DraftItem:
        """Describe a proposal for the page.

        Args:
            draft: The proposed memory.

        Returns:
            The proposal with its origin.
        """
        origin = describe_origin(draft)
        return cls.model_validate({**draft.model_dump(), "origin": origin})


class MemoryList(BaseModel):
    """Everything the Memories tab needs when it opens.

    Attributes:
        memories: Every memory, newest first, private ones included.
        auto_capture: Whether notes become memories automatically.
        forgotten: How many deleted memories are remembered by their wording.
    """

    memories: list[MemoryItem]
    auto_capture: bool
    forgotten: int


class AddedMemory(BaseModel):
    """A memory added by hand.

    Attributes:
        memory: The stored memory.
        similar_id: A memory that already says much the same, if any. The new
            one is stored anyway: the applicant wrote it on purpose.
    """

    memory: MemoryItem
    similar_id: int | None = None


class ExtractBody(BaseModel):
    """A text to turn into proposed memories.

    Attributes:
        text: What the applicant pasted or read from a file.
        source_detail: Where it came from, such as a file name. Only a label:
            a long one is cut to ``MAX_SOURCE_DETAIL_CHARS`` rather than refused.
    """

    text: str = Field(max_length=_MAX_BODY_CHARS)
    source_detail: str = Field(default="", max_length=5 * MAX_SOURCE_DETAIL_CHARS)


class Proposals(BaseModel):
    """The memories proposed for a text; nothing is stored yet.

    Attributes:
        drafts: The proposals, at most ``MAX_DRAFTS``.
        truncated: The text may hold more; proposing again after saving these
            gets the rest.
    """

    drafts: list[DraftItem]
    truncated: bool


class BatchBody(BaseModel):
    """The proposals the applicant kept, as they edited them."""

    drafts: list[MemoryDraft] = Field(min_length=1, max_length=MAX_DRAFTS)


class SavedBatch(BaseModel):
    """The memories stored from a batch, in the order given."""

    memories: list[MemoryItem]


class AutoCapture(BaseModel):
    """Whether notes typed for letters and interviews become memories."""

    enabled: StrictBool


class ReadFile(BaseModel):
    """The text of an uploaded file, for the applicant to check before reading it.

    Attributes:
        name: The file's name.
        text: Its text.
        max_chars: The most characters one proposal run reads.
    """

    name: str
    text: str
    max_chars: int = MAX_INPUT_CHARS


class ForgottenList(BaseModel):
    """The memories the applicant deleted, most recently deleted first."""

    forgotten: list[ForgottenMemory]


class Erased(BaseModel):
    """How many records were erased."""

    erased: int


def checked_memory_user(user: str, response: Response) -> Iterator[str]:
    """Validate the user and translate expected failures into API errors.

    The same split the letter and interview APIs make: a problem with the
    request is the caller's to fix and says so, a provider failure stays vague
    to the client and detailed in the log.

    Args:
        user: User name from the query string.
        response: Response whose caching headers are set before the handler runs.

    Yields:
        The validated user name.

    Raises:
        HTTPException: 400 for an unknown user or an unusable request (every
            memory error is a ValueError), 502 when the model call fails, 503
            when the user's data cannot be read or saved.
    """
    response.headers["Cache-Control"] = "no-store"
    try:
        yield require_memory_user(user)
    except ValueError as exc:
        logger.warning("Memory request rejected for {!r}: {}", user, exc)
        raise HTTPException(400, str(exc)) from exc
    except LLMError as exc:
        logger.error(
            "Memory request failed for {!r}: {}: {}", user, type(exc).__name__, exc
        )
        raise HTTPException(502, _VAGUE) from exc
    except (sqlite3.Error, OSError, yaml.YAMLError) as exc:
        logger.error("Memories unreadable for {!r}: {}", user, exc)
        raise HTTPException(503, "Your memories could not be read or saved.") from exc


MemoryUser = Annotated[str, Depends(checked_memory_user)]


def build_memories_router() -> APIRouter:
    """Build the Memories tab's routes.

    The fixed paths are added before ``/{memory_id}``, so a path such as
    ``/forgotten`` is never read as a memory number.

    Returns:
        A router to mount under "/api/memories".
    """
    router = APIRouter()
    _add_list_routes(router)
    _add_text_routes(router)
    _add_setting_routes(router)
    _add_memory_routes(router)
    return router


def _add_list_routes(router: APIRouter) -> None:
    """Add the routes that read all memories and add them.

    Args:
        router: The memories router.
    """

    @router.get("")
    def memories(user: MemoryUser) -> MemoryList:
        """Return every memory, the capture switch and the deleted count."""
        return MemoryList(
            memories=[MemoryItem.of(memory) for memory in list_memories(user)],
            auto_capture=auto_capture_enabled(user),
            forgotten=len(list_forgotten(user)),
        )

    @router.post("", status_code=201)
    def add(body: MemoryContent, user: MemoryUser) -> AddedMemory:
        """Store a memory written by hand, as written; nothing goes to a model."""
        similar = find_duplicate(body.text, list_memories(user))
        stored = add_memory(user, MemoryDraft.model_validate(body.model_dump()))
        return AddedMemory(
            memory=MemoryItem.of(stored),
            similar_id=similar.id if similar is not None else None,
        )

    @router.post("/batch", status_code=201)
    def add_batch(body: BatchBody, user: MemoryUser) -> SavedBatch:
        """Store the proposals the applicant kept, in one transaction."""
        stored = add_memories(user, body.drafts)
        return SavedBatch(memories=[MemoryItem.of(memory) for memory in stored])


def _add_text_routes(router: APIRouter) -> None:
    """Add the routes that turn a text into proposed memories.

    Args:
        router: The memories router.
    """

    @router.post("/extract")
    def extract(body: ExtractBody, user: MemoryUser) -> Proposals:
        """Propose memories for a text with one model call; store nothing.

        Memories the applicant deleted may be proposed again here: they
        review every proposal, so taking a fact back is their choice.
        """
        if not body.text.strip():
            raise ValueError("Paste or type a text first.")
        found = extract_memories(
            body.text,
            list_memories(user),
            get_llm_client(build_effective_config(user)),
            source=MemorySource.TEXT_IMPORT,
            source_detail=body.source_detail or PASTED_DETAIL,
        )
        drafts = [DraftItem.of(draft) for draft in found.drafts]
        return Proposals(drafts=drafts, truncated=found.truncated)

    @router.post("/read-file")
    async def read_file(user: MemoryUser, file: UploadFile) -> ReadFile:
        """Return the text of a TXT, MD, PDF, DOCX or ODT file; store nothing."""
        try:
            data = await file.read(MAX_BYTES + 1)
        finally:
            await file.close()
        name = file.filename or ""
        return ReadFile(name=name, text=extract_text(name, data, kind="document"))


def _add_setting_routes(router: APIRouter) -> None:
    """Add the capture switch and the list of deleted memories.

    Args:
        router: The memories router.
    """

    @router.get("/auto-capture")
    def auto_capture(user: MemoryUser) -> AutoCapture:
        """Say whether notes become memories automatically."""
        return AutoCapture(enabled=auto_capture_enabled(user))

    @router.put("/auto-capture")
    def switch_auto_capture(body: AutoCapture, user: MemoryUser) -> AutoCapture:
        """Switch automatic capture on or off in the user's settings."""
        set_auto_capture(user, body.enabled)
        return AutoCapture(enabled=auto_capture_enabled(user))

    @router.get("/forgotten")
    def forgotten(user: MemoryUser) -> ForgottenList:
        """List the deleted memories that notes will not bring back."""
        return ForgottenList(forgotten=list_forgotten(user))

    @router.delete("/forgotten")
    def erase_forgotten(user: MemoryUser) -> Erased:
        """Erase the wording of deleted memories from the user's database."""
        return Erased(erased=clear_forgotten(user))


def _add_memory_routes(router: APIRouter) -> None:
    """Add the routes that change or delete one memory.

    Args:
        router: The memories router.
    """

    @router.put("/{memory_id}")
    def update(memory_id: int, body: MemoryContent, user: MemoryUser) -> MemoryItem:
        """Replace a memory's text, kind, tags, hint, uses and private flag."""
        changed = update_memory(user, memory_id, body)
        if changed is None:
            raise HTTPException(404, _NOT_FOUND)
        return MemoryItem.of(changed)

    @router.delete("/{memory_id}", status_code=204)
    def delete(memory_id: int, user: MemoryUser) -> Response:
        """Delete one memory; automatic capture will not bring it back."""
        if not delete_memory(user, memory_id):
            raise HTTPException(404, _NOT_FOUND)
        return Response(status_code=204)


# -- Automatic capture after a generation --------------------------------------


def notes_detail(user: str, job_id: int, document: str) -> str:
    """Say which vacancy some notes were typed for, for the Memories tab.

    Only ever shown to the applicant and stored with the memory; it never
    enters a prompt, so a company name from a scraped vacancy is safe here.

    Args:
        user: Name of an existing user.
        job_id: The vacancy the notes were typed for.
        document: "letter" or "interview".

    Returns:
        Such as "notes for the Data analyst letter to Findwhere" or "notes
        for the Data analyst interview at Findwhere".
    """
    job = Database(user_db_path(user)).get_job(job_id)
    if job is None:
        return f"notes for the {document} for vacancy {job_id}"
    title, company = " ".join(job.title.split()), " ".join(job.company.split())
    what = f"{title} {document}" if title else document
    where = f" {_PREPOSITIONS[document]} {company}" if company else ""
    return f"notes for the {what}{where}"


def start_capture(
    tasks: BackgroundTasks,
    response: Response,
    user: str,
    notes: str,
    *,
    source: MemorySource,
    job_id: int,
    client: LLMClient | None = None,
) -> bool:
    """Turn the notes of a generation into memories after the response is sent.

    Nothing starts when the notes are too short, the user switched automatic
    capture off, or these notes were captured before; see
    :func:`job_scout.memory_extract.capture_pending`. When it starts, the
    response carries ``X-Memory-Capture: started`` so the page can say that
    new memories may appear. A failure here is logged and never costs the
    applicant what was just generated.

    Args:
        tasks: The request's background tasks.
        response: The response whose header reports a started capture.
        user: The validated user.
        notes: The notes as typed.
        source: ``LETTER_NOTES`` or ``INTERVIEW_NOTES``.
        job_id: The vacancy the notes were typed for.
        client: The model the generation used, reused for the capture; the
            user's configured one when None.

    Returns:
        True when a capture was scheduled.
    """
    document = "letter" if source is MemorySource.LETTER_NOTES else "interview"
    try:
        if not capture_pending(user, notes):
            return False
        detail = notes_detail(user, job_id, document)
    except _CAPTURE_CHECK_ERRORS as exc:
        logger.warning(f"Memory capture not started for {user}: {exc}")
        return False
    tasks.add_task(
        capture_from_notes,
        user,
        notes,
        source=source,
        source_detail=detail,
        job_id=job_id,
        client=client,
    )
    response.headers[CAPTURE_HEADER] = "started"
    logger.info(f"Memory capture from {document} notes scheduled for {user}")
    return True
