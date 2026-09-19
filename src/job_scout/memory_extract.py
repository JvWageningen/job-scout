"""Turn free text into memories, on request or automatically from notes.

Two ways in, one extraction. :func:`extract_memories` asks the model once for
the facts about the applicant in a text and returns them as drafts: the
dashboard shows them to tick, edit and save, and ``job-scout memory import``
saves them after asking. :func:`capture_from_notes` runs the same extraction
on the notes the applicant typed for a letter or an interview and saves what
it finds straight away, in the background, so a fact mentioned once for one
vacancy is there for the next.

The model judges what is worth keeping and how to generalise it; code keeps a
floor under that judgement. Every draft is put in the house style, checked
against the limits of :class:`job_scout.memories.MemoryContent`, compared with
what is already remembered (see :func:`job_scout.memories.same_fact`) and
capped in number. Wishes and conditions never go on a CV, and a text that
plainly touches a private matter is marked sensitive even when the model did
not say so. Sensitive memories are never shown to the model, not even here
as existing memories; they are compared with in code only.

Automatic capture remembers the hash of every notes text it handled, so
generating again with the same notes costs no second model call and does not
bring back memories the applicant deleted. It can be switched off per user
with the ``memory_auto_capture`` setting.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import yaml
from loguru import logger
from pydantic import ValidationError

from job_scout.config import build_effective_config, set_config_value, user_db_path
from job_scout.database import Database
from job_scout.llm.base import LLMClient, LLMError
from job_scout.llm.factory import get_llm_client
from job_scout.memories import (
    ALL_USES,
    MAX_HINT_CHARS,
    MAX_SOURCE_DETAIL_CHARS,
    MAX_TEXT_CHARS,
    Memory,
    MemoryContent,
    MemoryDraft,
    MemoryKind,
    MemorySource,
    MemoryUse,
    add_memories,
    find_duplicate,
    list_memories,
    most_relevant,
    normalise_text,
    require_memory_user,
)
from job_scout.prose import clean_prose
from job_scout.writing_style import HOUSE_STYLE

# The reasoning model needs minutes for a structured answer; the client's
# default timeout suits scoring a vacancy, not this.
EXTRACT_TIMEOUT = 300.0
MAX_INPUT_CHARS = 20000
MAX_DRAFTS = 20
# Existing memories shown to the model so it does not propose them again, the
# ones closest to the text first. Code compares with all of them regardless.
MAX_EXISTING_IN_PROMPT = 40
# Notes shorter than this are an instruction ("kort houden"), not a fact.
MIN_NOTES_WORDS = 3
# A capture that has not finished after this long was stopped halfway (a
# restart during the model call); the notes may then be captured again.
CLAIM_STALE_AFTER = timedelta(minutes=30)

# Kinds that describe a wish or a condition: useful for a letter or an
# interview, never a line on a CV.
_OFF_CV_KINDS = frozenset({MemoryKind.PREFERENCE, MemoryKind.CONSTRAINT})
# Phrases that plainly touch the applicant's own private life: health, faith,
# politics and union membership, sexuality, a criminal record, debts and
# benefits, family care. Matched on whole normalised words; a hit marks the
# draft sensitive. Deliberately narrow: words such as "depression",
# "medication" or "autism" also describe a psychologist's or a pharmacist's
# work, and those are left to the model.
_SENSITIVE_TERMS = (
    "burnout",
    "burn out",
    "overspannen",
    "overspannenheid",
    "ziekteverlof",
    "sick leave",
    "arbeidsongeschikt",
    "arbeidsongeschiktheid",
    "chronisch ziek",
    "chronic illness",
    "diagnosed with",
    "gediagnosticeerd",
    "zwanger",
    "zwangerschap",
    "zwangerschapsverlof",
    "pregnant",
    "pregnancy",
    "maternity leave",
    "my faith",
    "my religion",
    "mijn geloof",
    "mijn religie",
    "ramadan",
    "politieke partij",
    "political party",
    "vakbond",
    "trade union",
    "homoseksueel",
    "lesbisch",
    "transgender",
    "strafblad",
    "criminal record",
    "schulden",
    "schuldsanering",
    "bewindvoering",
    "in debt",
    "personal debt",
    "uitkering",
    "bijstand",
    "mantelzorg",
    "mantelzorger",
    "echtscheiding",
    "divorce",
    "divorced",
)
_CAPTURE_ERRORS = (LLMError, ValueError, sqlite3.Error, OSError, yaml.YAMLError)

_CONTEXTS = {
    MemorySource.LETTER_NOTES: "The TEXT is the notes the applicant typed to go "
    "with a motivation letter for one vacancy",
    MemorySource.INTERVIEW_NOTES: "The TEXT is the notes the applicant typed to "
    "prepare a job interview for one vacancy",
    MemorySource.TEXT_IMPORT: "The TEXT is something the applicant wrote or "
    "pasted about themselves",
    MemorySource.MANUAL: "The TEXT is something the applicant wrote about themselves",
}

_RULES = (
    "RULES\n"
    "1. Keep only facts about the applicant that could matter for other "
    "applications or later documents: projects, results, skills, tools, "
    "experience, education, certificates, languages, the kind of work they "
    "want, and conditions such as hours, travel, notice period or when they "
    "are available.\n"
    "2. Leave out instructions about the document being written, such as "
    '"make it shorter", "mention my salary wish" or "use a formal tone". Leave '
    "out facts that are only about the vacancy, the employer or a contact "
    "person. Leave out anything the EXISTING MEMORIES already say.\n"
    "3. Write each memory so it stands on its own, without the vacancy or the "
    "document it came from. Generalise the framing, never the facts: keep "
    "names, numbers, dates, tools and places exactly as the TEXT gives them. "
    "Never invent, embellish or add anything the TEXT does not state.\n"
    "4. One fact per memory. Split a sentence that holds two unrelated facts, "
    "but keep a project together with its own result.\n"
    "5. Write each memory in the first person, in the language of the TEXT "
    "(Dutch or English), in one to three plain sentences of at most "
    f"{MAX_TEXT_CHARS} characters.\n"
    "6. For each memory also give:\n"
    "kind: one of project, achievement, skill, experience, education, "
    "preference, constraint, personal, other.\n"
    "tags: at most 8 short lower case keywords that a vacancy it matters for "
    "would contain, such as a field, a skill, a tool or a sector.\n"
    "hint: one short sentence in the language of the memory on when to use it, "
    'such as "Use for CRO or experimentation roles".\n'
    'use_in: where it may be used, any of "cv", "letter" and "interview". '
    'Leave out "cv" for anything the applicant does not want on their CV, and '
    "for wishes, conditions and personal circumstances, which do not belong on "
    "a CV.\n"
    "sensitive: true for health, family circumstances, religion, politics, "
    "finances and other private matters, false otherwise.\n"
    f"7. Return at most {MAX_DRAFTS} memories. When nothing in the TEXT is worth "
    "remembering, return an empty list: that is a normal answer.\n"
)

_OUTPUT = (
    "OUTPUT: only JSON, no other text, in this shape:\n"
    '{"memories": [{"text": "...", "kind": "project", "tags": ["..."], '
    '"hint": "...", "use_in": ["cv", "letter", "interview"], '
    '"sensitive": false}]}\n'
)


class MemoryExtractionError(ValueError):
    """A text cannot be turned into memories, or the model's answer was unusable."""


@dataclass(frozen=True)
class _Origin:
    """Where the text being turned into memories came from.

    Attributes:
        source: How the memories are made.
        detail: Where the text came from in words, possibly empty.
        job_id: The vacancy the text was written for, if any.
    """

    source: MemorySource
    detail: str
    job_id: int | None

    @classmethod
    def of(cls, source: MemorySource | str, detail: str, job_id: int | None) -> _Origin:
        """Describe an origin, dropping a vacancy id that cannot be one.

        Args:
            source: How the memories are made.
            detail: Where the text came from in words.
            job_id: The vacancy the text was written for, if any.

        Returns:
            The origin.

        Raises:
            ValueError: If the source is not a known one.
        """
        vacancy = job_id if isinstance(job_id, int) and job_id > 0 else None
        detail = " ".join(detail.split())[:MAX_SOURCE_DETAIL_CHARS]
        return cls(source=MemorySource(source), detail=detail, job_id=vacancy)


# -- Prompt ----------------------------------------------------------------------


def _context(origin: _Origin) -> str:
    """Tell the model what kind of text it is reading.

    Args:
        origin: Where the text came from.

    Returns:
        One sentence.
    """
    base = _CONTEXTS[origin.source]
    return f"{base} ({origin.detail})." if origin.detail else f"{base}."


def _existing_block(existing: Sequence[Memory], text: str) -> str:
    """List the existing memories the model may see, closest to the text first.

    Sensitive memories are left out: they never reach a model.

    Args:
        existing: Every stored memory.
        text: The text being read.

    Returns:
        A JSON list of memory texts, or "[]" when there are none.
    """
    shareable = [memory for memory in existing if not memory.sensitive]
    shown = most_relevant(shareable, text, limit=MAX_EXISTING_IN_PROMPT)
    return json.dumps([memory.text for memory in shown], ensure_ascii=False)


def _prompt(text: str, existing: Sequence[Memory], origin: _Origin) -> str:
    """Build the one extraction prompt.

    Args:
        text: The text to read.
        existing: Every stored memory.
        origin: Where the text came from.

    Returns:
        The prompt.
    """
    return (
        "You keep a memory of facts about a job applicant, so that later CVs, "
        "motivation letters and interview answers can use them. "
        f"{_context(origin)} Read the TEXT and return the facts about the "
        "applicant worth remembering.\n\n"
        f"{_RULES}\n{HOUSE_STYLE}\n{_OUTPUT}\n"
        "EXISTING MEMORIES (already remembered, do not repeat them):\n"
        f"{_existing_block(existing, text)}\n\n"
        f"TEXT:\n<<<\n{text}\n>>>\n"
    )


# -- Reading the answer --------------------------------------------------------------


def _json_value(raw: str) -> object:
    """Read the JSON in a model answer, past fences and a thinking block.

    Args:
        raw: The model's answer.

    Returns:
        The parsed value.

    Raises:
        MemoryExtractionError: If the answer holds no readable JSON.
    """
    text = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    for candidate in (text, text[start : end + 1] if 0 <= start < end else ""):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    raise MemoryExtractionError("The model returned no readable memories. Retry.")


def _items(raw: str) -> list[dict[str, Any]]:
    """Take the proposed memories out of a model answer.

    Args:
        raw: The model's answer.

    Returns:
        The objects in its memories list; anything else in the list is ignored.

    Raises:
        MemoryExtractionError: If the answer holds no list of memories.
    """
    value = _json_value(raw)
    if isinstance(value, dict):
        value = value.get("memories")
    if not isinstance(value, list):
        raise MemoryExtractionError("The model returned no list of memories. Retry.")
    return [item for item in value if isinstance(item, dict)]


def _prose(value: object) -> str:
    """Put a generated sentence in the house style, on one line.

    Args:
        value: A field as the model wrote it.

    Returns:
        The cleaned text; empty when the field is not text.
    """
    if not isinstance(value, str):
        return ""
    return " ".join(clean_prose(value, flatten_lists=True).split())


def _kind(value: object) -> MemoryKind:
    """Read the kind the model gave, falling back to "other".

    Args:
        value: The kind as the model wrote it.

    Returns:
        A known kind.
    """
    try:
        return MemoryKind(str(value).strip().lower())
    except ValueError:
        return MemoryKind.OTHER


def _uses(value: object, kind: MemoryKind) -> list[MemoryUse]:
    """Read where the model says a memory may be used.

    Args:
        value: A list of uses, or a comma-separated string.
        kind: The memory's kind; a wish or a condition never goes on a CV.

    Returns:
        The valid uses, or every use its kind allows when none is valid.
    """
    raw = value.split(",") if isinstance(value, str) else value
    items = raw if isinstance(raw, list | tuple) else []
    named = {str(item).strip().lower() for item in items}
    allowed = [
        use for use in ALL_USES if not (kind in _OFF_CV_KINDS and use is MemoryUse.CV)
    ]
    return [use for use in allowed if use.value in named] or allowed


def _truthy(value: object) -> bool:
    """Read a yes or no the model may have written as text.

    Args:
        value: The field as the model wrote it.

    Returns:
        True for true, "true", "yes", "ja" and 1.
    """
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "ja", "1"}
    return value is True or value == 1


def touches_private_matter(text: str) -> bool:
    """Tell whether a text plainly touches a private matter.

    The floor under the model's own ``sensitive`` judgement: it catches the
    obvious cases the model missed and nothing subtle.

    Args:
        text: A memory text or hint.

    Returns:
        True when a term such as "burnout", "zwanger" or "schulden" occurs.
    """
    padded = f" {normalise_text(text)} "
    return any(f" {term} " in padded for term in _SENSITIVE_TERMS)


def _draft(item: dict[str, Any], origin: _Origin) -> MemoryDraft | None:
    """Turn one proposed memory into a validated draft.

    Args:
        item: One object from the model's list.
        origin: Where the text came from.

    Returns:
        The draft, or None when its text is empty, too long or otherwise
        unusable. A hint over the limit is dropped rather than the memory.
    """
    text, hint = _prose(item.get("text")), _prose(item.get("hint"))
    if not text or len(text) > MAX_TEXT_CHARS:
        logger.debug(f"Dropped a proposed memory of {len(text)} characters")
        return None
    kind = _kind(item.get("kind"))
    try:
        return MemoryDraft(
            text=text,
            kind=kind,
            tags=item.get("tags"),
            hint=hint if len(hint) <= MAX_HINT_CHARS else "",
            use_in=_uses(item.get("use_in"), kind),
            sensitive=_truthy(item.get("sensitive")) or touches_private_matter(text),
            source=origin.source,
            source_detail=origin.detail,
            job_id=origin.job_id,
        )
    except ValidationError as exc:
        logger.debug(f"Dropped a proposed memory: {exc}")
        return None


def keep_new(
    drafts: Sequence[MemoryDraft], existing: Sequence[MemoryContent]
) -> list[MemoryDraft]:
    """Drop drafts that repeat a stored memory or an earlier draft, and cap them.

    Args:
        drafts: Proposed memories, in the model's order.
        existing: Every stored memory, sensitive ones included.

    Returns:
        At most :data:`MAX_DRAFTS` drafts that state something new.
    """
    kept: list[MemoryDraft] = []
    for draft in drafts:
        known = find_duplicate(draft.text, existing) or find_duplicate(draft.text, kept)
        if known is not None:
            logger.debug(f"Dropped a proposed memory already known: {draft.text!r}")
            continue
        kept.append(draft)
    return kept[:MAX_DRAFTS]


def extract_memories(
    text: str,
    existing: Sequence[Memory],
    client: LLMClient,
    *,
    source: MemorySource | str,
    source_detail: str = "",
    job_id: int | None = None,
) -> list[MemoryDraft]:
    """Turn free text into proposed memories with one model call.

    Args:
        text: What the applicant wrote: notes, a pasted text, a document.
        existing: The applicant's stored memories, from
            :func:`job_scout.memories.list_memories`. Sensitive ones are
            compared with in code but never shown to the model.
        client: The model to ask.
        source: How the memories are made, e.g. "text_import".
        source_detail: Where the text came from in words, such as "notes for
            the Findwhere letter"; stored with every draft.
        job_id: The vacancy the text was written for, stored for reference.

    Returns:
        Drafts that state something not yet remembered, in the house style,
        at most :data:`MAX_DRAFTS`; empty for an empty text or when nothing is
        worth remembering. Nothing is stored.

    Raises:
        MemoryExtractionError: If the text is over :data:`MAX_INPUT_CHARS` or
            the model's answer is unreadable.
        ValueError: If the source is not a known one.
        LLMError: If the model call fails.
    """
    body = text.strip()
    if not body:
        return []
    if len(body) > MAX_INPUT_CHARS:
        raise MemoryExtractionError(
            f"The text is too long. Use at most {MAX_INPUT_CHARS:,} characters "
            "at a time."
        )
    origin = _Origin.of(source, source_detail, job_id)
    raw = client.complete(
        _prompt(body, existing, origin), purpose="cv_parsing", timeout=EXTRACT_TIMEOUT
    )
    proposed = (_draft(item, origin) for item in _items(raw))
    drafts = [draft for draft in proposed if draft is not None]
    kept = keep_new(drafts, existing)
    logger.info(
        f"Proposed {len(kept)} memories from {origin.source}; "
        f"{len(drafts) - len(kept)} already known or over the limit"
    )
    return kept


# -- Automatic capture ---------------------------------------------------------------


def notes_hash(notes: str) -> str:
    """Return the key a notes text is recorded under once captured.

    Case and spacing do not make a new text.

    Args:
        notes: The notes as typed.

    Returns:
        A SHA-256 hex digest of the normalised notes.
    """
    normalised = " ".join(notes.casefold().split())
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


def auto_capture_enabled(user: str) -> bool:
    """Tell whether notes are turned into memories automatically for a user.

    Args:
        user: Name of an existing user.

    Returns:
        The user's ``memory_auto_capture`` setting; on unless switched off.

    Raises:
        MemoryStoreError: If the user does not exist.
    """
    return build_effective_config(require_memory_user(user)).memory_auto_capture


def set_auto_capture(user: str, enabled: bool) -> None:
    """Switch automatic capture on or off for a user.

    Args:
        user: Name of an existing user.
        enabled: The new setting.

    Raises:
        MemoryStoreError: If the user does not exist.
    """
    value = "true" if enabled else "false"
    set_config_value("memory_auto_capture", value, user=require_memory_user(user))


def _worth_capturing(notes: str) -> bool:
    """Tell whether notes are long enough to state a fact.

    Args:
        notes: The notes as typed.

    Returns:
        True for notes of at least :data:`MIN_NOTES_WORDS` words.
    """
    return len(notes.split()) >= MIN_NOTES_WORDS


def capture_pending(user: str, notes: str) -> bool:
    """Tell whether :func:`capture_from_notes` would read these notes now.

    For the page that starts a capture: it can say new memories may appear
    only when one will actually run.

    Args:
        user: Name of an existing user.
        notes: The notes as typed.

    Returns:
        True when the notes are long enough, capture is on for the user, and
        the notes were neither captured before nor are being captured.

    Raises:
        MemoryStoreError: If the user does not exist.
    """
    if not _worth_capturing(notes) or not auto_capture_enabled(user):
        return False
    record = Database(user_db_path(user)).get_notes_capture(notes_hash(notes))
    if record is None:
        return True
    if record["memories_added"] is not None:
        return False
    claimed = record["claimed_at"]
    return claimed is None or claimed < datetime.now(UTC) - CLAIM_STALE_AFTER


def _extract_and_save(
    user: str, notes: str, origin: _Origin, client: LLMClient | None
) -> list[Memory]:
    """Extract memories from notes and store the new ones.

    The memories are read again just before storing, so a capture of other
    notes that finished meanwhile is not repeated.

    Args:
        user: Name of an existing user.
        notes: The notes as typed.
        origin: Where the notes were typed.
        client: The model to ask; the user's configured one when None.

    Returns:
        The stored memories.
    """
    model = client or get_llm_client(build_effective_config(user))
    drafts = extract_memories(
        notes,
        list_memories(user),
        model,
        source=origin.source,
        source_detail=origin.detail,
        job_id=origin.job_id,
    )
    if not drafts:
        return []
    return add_memories(user, keep_new(drafts, list_memories(user)))


def _capture(
    user: str, notes: str, origin: _Origin, client: LLMClient | None
) -> list[Memory]:
    """Capture memories from notes once, releasing the claim if it fails.

    Args:
        user: Name of an existing user.
        notes: The notes as typed.
        origin: Where the notes were typed.
        client: The model to ask; the user's configured one when None.

    Returns:
        The stored memories; empty when there was nothing to do.
    """
    if not _worth_capturing(notes) or not auto_capture_enabled(user):
        return []
    db, key = Database(user_db_path(user)), notes_hash(notes)
    stale = datetime.now(UTC) - CLAIM_STALE_AFTER
    job_id, source = origin.job_id, origin.source.value
    if not db.claim_notes_capture(
        key, source=source, job_id=job_id, stale_before=stale
    ):
        logger.debug(f"Notes for {user} were already captured; skipped")
        return []
    done = False
    try:
        saved = _extract_and_save(user, notes, origin, client)
        db.complete_notes_capture(key, len(saved))
        done = True
    finally:
        if not done:
            db.release_notes_capture(key)
    logger.info(f"Captured {len(saved)} memories for {user} from {source}")
    return saved


def capture_from_notes(
    user: str,
    notes: str,
    *,
    source: MemorySource | str,
    source_detail: str = "",
    job_id: int | None = None,
    client: LLMClient | None = None,
) -> list[Memory]:
    """Turn the notes typed for a letter or interview into stored memories.

    Meant to run after the generation, e.g. as a FastAPI background task. It
    never raises into the caller: a failure is logged and nothing is stored.
    Nothing runs when the user switched ``memory_auto_capture`` off, when the
    notes are shorter than :data:`MIN_NOTES_WORDS` words, or when the same
    notes were captured before (by hash of the normalised text). A capture
    that fails is not recorded, so the next generation tries again.

    Args:
        user: Name of an existing user.
        notes: The notes as typed.
        source: "letter_notes" or "interview_notes".
        source_detail: Where the notes were typed in words, such as "notes for
            the Findwhere letter".
        job_id: The vacancy the notes were typed for.
        client: The model to ask; the user's configured one when None.

    Returns:
        The memories stored; empty when nothing was stored for any reason.
    """
    try:
        origin = _Origin.of(source, source_detail, job_id)
        return _capture(user, notes, origin, client)
    except _CAPTURE_ERRORS as exc:
        logger.warning(f"Memories were not captured from {source} for {user}: {exc}")
        return []
