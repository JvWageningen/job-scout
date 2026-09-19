"""Memories: facts the applicant told job-scout about themselves, kept for later.

A CV is written for every reader at once, so it leaves things out: a project
that only matters for one kind of role, a result the applicant would rather not
print, a wish about hours or travel, a certificate still in progress. Those
facts used to live only in the notes typed for one letter or one interview and
were gone with it. A memory keeps each of them as one self-contained statement,
in the language it was written in, with its specifics (names, numbers, dates,
tools) exactly as given.

Every memory carries what later generators need to decide whether it fits:

* ``kind``: project, achievement, skill and so on.
* ``tags``: a few lower-case keywords a vacancy it matters for would contain.
* ``hint``: one short sentence on when it applies.
* ``use_in``: which documents may draw on it (CV, letter, interview). Something
  the applicant wants off their CV simply lacks ``cv``.
* ``sensitive``: private matters (health, family, religion, politics, finances
  and the like). A sensitive memory is stored and shown, and never sent to any
  model, not even to the extractor, until the applicant clears the flag.

Memories live in the user's own database (``memories`` table, see
:meth:`job_scout.database.Database.add_memories`). They reach the configured
model provider only as part of the applicant's facts, the way the CV does.

This module holds the models, the store and the selection:
:func:`select_memories` picks the memories allowed for one purpose and ranks
them by how well their tags and words match the vacancy, and
:func:`memories_payload` turns them into the labelled prompt material the
letter, interview and CV generators add to the applicant's facts. Turning free
text into memories, by hand or automatically from notes, is in
:mod:`job_scout.memory_extract`.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from loguru import logger
from pydantic import BaseModel, Field, ValidationError, field_validator

from job_scout.config import list_users, user_db_path
from job_scout.database import Database

MAX_TEXT_CHARS = 600
MAX_HINT_CHARS = 200
MAX_TAGS = 8
MAX_TAG_CHARS = 40
MAX_SOURCE_DETAIL_CHARS = 200
# More memories than this in one prompt crowd out the CV they supplement. With
# fewer eligible memories all of them go, and the model decides with the hints.
SELECT_LIMIT = 25
# Two texts sharing this share of their words (after light stemming) state the
# same fact, provided they hold the same numbers.
DUPLICATE_OVERLAP = 0.8
# A matching tag says more about fit than one shared word in the text.
_TAG_WEIGHT = 3

# The key the memories are added under in the applicant's facts.
MEMORIES_SOURCE_KEY = "memories"
# One sentence for the source guide of every prompt that receives memories.
MEMORY_GUIDE = (
    "memories holds facts the applicant asked job-scout to remember about "
    "themselves: projects, results, skills, circumstances and wishes, often ones "
    "they left off their CV on purpose. Each has a label, a kind, a hint on when "
    "it applies and tags. Use a memory only where its hint or tags fit this "
    "vacancy and leave the others out. A memory is the applicant's own statement "
    "and counts as evidence like the CV, except that a memory of kind preference "
    "or constraint is a wish or a condition, never experience. Never stretch a "
    "memory beyond what it says."
)
# The rule for CV tailoring, where the structure of the CV must stay intact.
MEMORY_CV_RULE = (
    "A memory may reword or add a bullet or a description under the existing "
    "role, study or project it belongs to, or add to the profile text. It never "
    "adds a role, an employer, a date, a school or a skill item that the CV does "
    "not already have."
)


class MemoryKind(StrEnum):
    """What a memory is about."""

    PROJECT = "project"
    ACHIEVEMENT = "achievement"
    SKILL = "skill"
    EXPERIENCE = "experience"
    EDUCATION = "education"
    PREFERENCE = "preference"
    CONSTRAINT = "constraint"
    PERSONAL = "personal"
    OTHER = "other"


class MemoryUse(StrEnum):
    """A kind of document a memory may be used in."""

    CV = "cv"
    LETTER = "letter"
    INTERVIEW = "interview"


ALL_USES: tuple[MemoryUse, ...] = (MemoryUse.CV, MemoryUse.LETTER, MemoryUse.INTERVIEW)


class MemorySource(StrEnum):
    """Where a memory came from."""

    MANUAL = "manual"
    TEXT_IMPORT = "text_import"
    LETTER_NOTES = "letter_notes"
    INTERVIEW_NOTES = "interview_notes"


class MemoryStoreError(ValueError):
    """Memories were asked for under a user that does not exist."""


def normalise_tags(value: object) -> list[str]:
    """Turn tags as a person or a model wrote them into clean keywords.

    Args:
        value: A list of tags, or one comma-separated string.

    Returns:
        Lower-case tags with whitespace collapsed and a leading "#" removed,
        without repeats, blanks or tags over :data:`MAX_TAG_CHARS`, and at most
        :data:`MAX_TAGS` of them.
    """
    if isinstance(value, str):
        value = value.split(",")
    tags: list[str] = []
    for item in value if isinstance(value, list | tuple) else []:
        if not isinstance(item, str):
            continue
        tag = " ".join(item.strip().lstrip("#").lower().split())
        if tag and len(tag) <= MAX_TAG_CHARS and tag not in tags:
            tags.append(tag)
    return tags[:MAX_TAGS]


def _one_line(value: object) -> object:
    """Collapse the whitespace of a string, leaving other values to validation.

    Args:
        value: A field value as given.

    Returns:
        The string on one line without outer spaces, or the value unchanged.
    """
    return " ".join(value.split()) if isinstance(value, str) else value


class MemoryContent(BaseModel):
    """The part of a memory the applicant writes and may change.

    Attributes:
        text: One self-contained statement about the applicant.
        kind: What the statement is about.
        tags: Lower-case keywords a vacancy it matters for would contain.
        hint: One short sentence on when it applies.
        use_in: The documents that may draw on it, in the order cv, letter,
            interview. Empty keeps the memory without using it anywhere.
        sensitive: A private matter that is never sent to a model.
    """

    text: str = Field(min_length=1, max_length=MAX_TEXT_CHARS)
    kind: MemoryKind = MemoryKind.OTHER
    tags: list[str] = Field(default_factory=list)
    hint: str = Field(default="", max_length=MAX_HINT_CHARS)
    use_in: list[MemoryUse] = Field(default_factory=lambda: list(ALL_USES))
    sensitive: bool = False

    @field_validator("text", "hint", mode="before")
    @classmethod
    def _collapse(cls, value: object) -> object:
        """Keep text and hint on one line."""
        return _one_line(value)

    @field_validator("tags", mode="before")
    @classmethod
    def _clean_tags(cls, value: object) -> list[str]:
        """Accept tags as a list or a comma-separated string."""
        return normalise_tags(value)

    @field_validator("use_in", mode="before")
    @classmethod
    def _split_uses(cls, value: object) -> object:
        """Accept the uses as a list or a comma-separated string."""
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @field_validator("use_in")
    @classmethod
    def _order_uses(cls, value: list[MemoryUse]) -> list[MemoryUse]:
        """Drop repeats and keep the uses in one fixed order."""
        return [use for use in ALL_USES if use in value]


class MemoryDraft(MemoryContent):
    """A memory that is not stored yet, with where it came from.

    Attributes:
        source: How the memory was made.
        source_detail: Where it came from in words, such as "notes for the
            Findwhere letter". Cut to :data:`MAX_SOURCE_DETAIL_CHARS`.
        job_id: The vacancy it came from, for reference only.
    """

    source: MemorySource = MemorySource.MANUAL
    source_detail: str = Field(default="", max_length=MAX_SOURCE_DETAIL_CHARS)
    job_id: int | None = Field(default=None, gt=0)

    @field_validator("source_detail", mode="before")
    @classmethod
    def _short_detail(cls, value: object) -> object:
        """Keep the origin on one line and within its limit; it is only a label."""
        value = _one_line(value)
        return value[:MAX_SOURCE_DETAIL_CHARS] if isinstance(value, str) else value


class Memory(MemoryDraft):
    """A stored memory.

    Attributes:
        id: The memory's id in the user's database.
        created_at: When it was stored.
        updated_at: When it was last changed.
    """

    id: int
    created_at: datetime
    updated_at: datetime

    @property
    def label(self) -> str:
        """The name prompts use for this memory, such as "memory 3"."""
        return memory_label(self.id)

    def content(self) -> MemoryContent:
        """Return the editable part, e.g. to change one field and save it.

        Returns:
            A copy of text, kind, tags, hint, use_in and sensitive.
        """
        fields = set(MemoryContent.model_fields)
        return MemoryContent.model_validate(self.model_dump(include=fields))


def memory_label(memory_id: int) -> str:
    """Return the label a memory is given in a prompt and cited by.

    Args:
        memory_id: The memory's id.

    Returns:
        "memory <id>", stable across generations.
    """
    return f"memory {memory_id}"


# -- Store ---------------------------------------------------------------------


def require_memory_user(user: str) -> str:
    """Resolve an existing user before their database is opened.

    Args:
        user: The user name as given.

    Returns:
        The same name, once it is known to exist.

    Raises:
        MemoryStoreError: If the name is unsafe or not an existing user.
    """
    if not user or user in {".", "..", "all"} or re.search(r"[\\/\x00]", user):
        raise MemoryStoreError("Select a single existing user.")
    if user not in list_users():
        raise MemoryStoreError("User not found.")
    return user


def _database(user: str) -> Database:
    """Open an existing user's database, creating the memory tables if needed.

    Args:
        user: The user name as given.

    Returns:
        The user's database.
    """
    return Database(user_db_path(require_memory_user(user)))


def _known[E: StrEnum](enum: type[E], value: object, default: E) -> E:
    """Read a stored enum value, falling back when it is not a known one.

    Args:
        enum: The enum to read.
        value: The stored value.
        default: What an unknown value becomes.

    Returns:
        The member for the value, or the default.
    """
    try:
        return enum(str(value))
    except ValueError:
        return default


def _from_row(row: dict[str, Any]) -> Memory | None:
    """Turn a stored row into a memory, tolerating values this version lacks.

    Args:
        row: A memory as :meth:`Database.get_memories` returns it.

    Returns:
        The memory, or None when the row cannot be read at all.
    """
    uses = {use.value for use in MemoryUse}
    data = {
        **row,
        "kind": _known(MemoryKind, row.get("kind"), MemoryKind.OTHER),
        "source": _known(MemorySource, row.get("source"), MemorySource.MANUAL),
        "use_in": [use for use in row.get("use_in") or [] if use in uses],
    }
    try:
        return Memory.model_validate(data)
    except ValidationError as exc:
        logger.warning(f"Memory {row.get('id')} could not be read; skipped: {exc}")
        return None


def _read_all(db: Database) -> list[Memory]:
    """Read every readable memory from a database, newest first.

    Args:
        db: A user's database.

    Returns:
        The memories.
    """
    memories = (_from_row(row) for row in db.get_memories())
    return [memory for memory in memories if memory is not None]


def list_memories(user: str) -> list[Memory]:
    """Return all of a user's memories, newest first, sensitive ones included.

    Args:
        user: Name of an existing user.

    Returns:
        The memories.

    Raises:
        MemoryStoreError: If the user does not exist.
    """
    return _read_all(_database(user))


def get_memory(user: str, memory_id: int) -> Memory | None:
    """Return one of a user's memories.

    Args:
        user: Name of an existing user.
        memory_id: The memory's id.

    Returns:
        The memory, or None when the user has no memory with that id.

    Raises:
        MemoryStoreError: If the user does not exist.
    """
    row = _database(user).get_memory(memory_id)
    return _from_row(row) if row else None


def add_memories(user: str, drafts: Sequence[MemoryDraft]) -> list[Memory]:
    """Store several memories at once, in one transaction.

    Nothing is deduplicated here: what is added by hand or ticked by the
    applicant is stored as it is. :func:`find_duplicate` tells whether a text
    is already known.

    Args:
        user: Name of an existing user.
        drafts: The memories to store.

    Returns:
        The stored memories, in the order given.

    Raises:
        MemoryStoreError: If the user does not exist.
    """
    if not drafts:
        return []
    db = _database(user)
    ids = db.add_memories([draft.model_dump(mode="json") for draft in drafts])
    stored = {memory.id: memory for memory in _read_all(db)}
    logger.info(f"Stored {len(ids)} memories for {user}")
    return [stored[memory_id] for memory_id in ids if memory_id in stored]


def add_memory(user: str, draft: MemoryDraft) -> Memory:
    """Store one memory.

    Args:
        user: Name of an existing user.
        draft: The memory to store.

    Returns:
        The stored memory.

    Raises:
        MemoryStoreError: If the user does not exist.
    """
    return add_memories(user, [draft])[0]


def update_memory(user: str, memory_id: int, content: MemoryContent) -> Memory | None:
    """Replace the editable part of a stored memory.

    Where the memory came from and when it was made stay as they were.

    Args:
        user: Name of an existing user.
        memory_id: The memory's id.
        content: The new text, kind, tags, hint, uses and sensitive flag.

    Returns:
        The changed memory, or None when there is no memory with that id.

    Raises:
        MemoryStoreError: If the user does not exist.
    """
    db = _database(user)
    if not db.update_memory(memory_id, content.model_dump(mode="json")):
        return None
    row = db.get_memory(memory_id)
    return _from_row(row) if row else None


def delete_memory(user: str, memory_id: int) -> bool:
    """Delete one memory.

    Args:
        user: Name of an existing user.
        memory_id: The memory's id.

    Returns:
        True when it was deleted, False when there was no such memory.

    Raises:
        MemoryStoreError: If the user does not exist.
    """
    return _database(user).delete_memory(memory_id)


def delete_all_memories(user: str) -> int:
    """Delete all of a user's memories.

    Notes already captured stay recorded, so regenerating a letter with the
    same notes does not bring deleted memories back.

    Args:
        user: Name of an existing user.

    Returns:
        How many memories were deleted.

    Raises:
        MemoryStoreError: If the user does not exist.
    """
    return _database(user).delete_all_memories()


# -- Comparing text --------------------------------------------------------------

# Endings stripped before words are compared, longest first, so "projects" and
# "projecten" meet "project", and "testing" and "tested" meet "test".
_SUFFIXES = ("ingen", "ing", "ers", "er", "en", "es", "ed", "e", "s")
# Words that say nothing about fit: function words of both languages and the
# words nearly every vacancy uses.
_STOPWORD_TEXT = (
    "the and for with that this from have has had are was were been will would "
    "you your our their they them its into over under about also only more most "
    "not but can could should may must who what when where which while than then "
    "there here all any each other some such very just like work working worked "
    "experience role team teams company job jobs year years month months new "
    "de het een en van voor met dat die dit deze wat wie waar wordt worden werd "
    "zijn was waren ben bent heb hebt heeft hebben had kan kun kunt kunnen zal "
    "zullen wil wilt willen niet geen ook maar dan als bij naar over onder door "
    "uit aan tot om op te er je jij jouw jullie wij we ons onze zij hun ik mijn "
    "mij hem haar veel meer meest werk werken ervaring functie rol bedrijf team "
    "jaar jaren maand maanden nieuwe"
)
_STOPWORDS = frozenset(_STOPWORD_TEXT.split())


def normalise_text(text: str) -> str:
    """Reduce text to lower-case words for comparison.

    Args:
        text: Any text.

    Returns:
        The words without accents or punctuation, separated by single spaces.
    """
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(re.sub(r"\W+", " ", plain).split())


def _stem(word: str) -> str:
    """Strip one common English or Dutch ending, keeping at least four letters.

    Args:
        word: A normalised word.

    Returns:
        The word without its ending.
    """
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def _stems(normalised: str) -> set[str]:
    """Return the stems of every word in normalised text.

    Args:
        normalised: Text from :func:`normalise_text`.

    Returns:
        The stems.
    """
    return {_stem(word) for word in normalised.split()}


def significant_words(text: str) -> set[str]:
    """Return the stems of the words in a text that can say something about fit.

    Args:
        text: Any text.

    Returns:
        Stems of words of three letters or more that are not numbers or
        stopwords.
    """
    stems = {
        _stem(word)
        for word in normalise_text(text).split()
        if len(word) >= 3 and not word.isdigit() and word not in _STOPWORDS
    }
    return stems - _STOPWORDS


def _numbers(normalised: str) -> set[str]:
    """Return the words of normalised text that hold a digit.

    Args:
        normalised: Text from :func:`normalise_text`.

    Returns:
        Those words, such as "2019" or "40".
    """
    return {word for word in normalised.split() if any(c.isdigit() for c in word)}


def same_fact(first: str, second: str) -> bool:
    """Tell whether two memory texts state the same fact.

    The texts are equal once case, accents and punctuation are ignored, or
    they hold the same numbers and share at least :data:`DUPLICATE_OVERLAP`
    of their word stems. A different number is a different fact: "since
    2019" and "since 2020" are not the same claim.

    Args:
        first: One memory text.
        second: Another.

    Returns:
        True when the second adds nothing the first does not say.
    """
    one, other = normalise_text(first), normalise_text(second)
    if one == other:
        return True
    if not one or not other or _numbers(one) != _numbers(other):
        return False
    stems, other_stems = _stems(one), _stems(other)
    overlap = len(stems & other_stems) / len(stems | other_stems)
    return overlap >= DUPLICATE_OVERLAP


def find_duplicate[M: MemoryContent](text: str, memories: Sequence[M]) -> M | None:
    """Find a memory that already states what a text says.

    Args:
        text: A new memory text.
        memories: The memories to compare with, stored or drafts.

    Returns:
        The first memory stating the same fact (see :func:`same_fact`), or None.
    """
    for memory in memories:
        if same_fact(memory.text, text):
            return memory
    return None


# -- Selection -----------------------------------------------------------------


@dataclass(frozen=True)
class _VacancyTerms:
    """A vacancy's words, prepared once for scoring many memories against it.

    Attributes:
        padded: The normalised text with a space on either side.
        stems: The stems of every word.
        significant: The stems of the words that say something about fit.
    """

    padded: str
    stems: set[str]
    significant: set[str]

    @classmethod
    def of(cls, vacancy_text: str) -> _VacancyTerms:
        """Prepare a vacancy's words.

        Args:
            vacancy_text: The vacancy's title and description.

        Returns:
            The prepared words.
        """
        normalised = normalise_text(vacancy_text)
        return cls(
            padded=f" {normalised} ",
            stems=_stems(normalised),
            significant=significant_words(vacancy_text),
        )

    def tag_fits(self, tag: str) -> bool:
        """Tell whether a tag occurs in the vacancy.

        Args:
            tag: A memory tag.

        Returns:
            True when the tag occurs as a phrase, or every word of it occurs.
        """
        words = normalise_text(tag)
        if not words:
            return False
        return f" {words} " in self.padded or _stems(words) <= self.stems

    def score(self, memory: MemoryContent) -> int:
        """Score how well a memory fits the vacancy; see :func:`relevance`.

        Args:
            memory: A memory.

        Returns:
            The score.
        """
        tags = sum(1 for tag in memory.tags if self.tag_fits(tag))
        words = significant_words(f"{memory.text} {memory.hint}")
        return _TAG_WEIGHT * tags + len(words & self.significant)


def relevance(memory: MemoryContent, vacancy_text: str) -> int:
    """Score how well a memory fits a vacancy.

    Args:
        memory: A memory.
        vacancy_text: The vacancy's title and description.

    Returns:
        Three points per tag found in the vacancy plus one per significant
        word the memory's text and hint share with it; 0 for no overlap.
    """
    return _VacancyTerms.of(vacancy_text).score(memory)


def eligible_memories(
    memories: Sequence[Memory], purpose: MemoryUse | str
) -> list[Memory]:
    """Keep the memories that may be sent to a model for one purpose.

    Args:
        memories: Any memories.
        purpose: "cv", "letter" or "interview".

    Returns:
        The memories allowed for that purpose that are not sensitive, in the
        order given.

    Raises:
        ValueError: If the purpose is not one of the three.
    """
    use = MemoryUse(purpose)
    return [m for m in memories if use in m.use_in and not m.sensitive]


def rank_memories(
    memories: Sequence[Memory],
    purpose: MemoryUse | str,
    vacancy_text: str,
    *,
    limit: int = SELECT_LIMIT,
) -> list[Memory]:
    """Pick and order the memories for one purpose and vacancy.

    Args:
        memories: Any memories, e.g. from :func:`list_memories`.
        purpose: "cv", "letter" or "interview".
        vacancy_text: The vacancy's title and description.
        limit: The most memories to return.

    Returns:
        The eligible memories (see :func:`eligible_memories`), best fitting
        first and the most recently changed first among equals, trimmed to
        ``limit``. With no more than ``limit`` eligible, all of them.

    Raises:
        ValueError: If the purpose is not one of the three.
    """
    candidates = eligible_memories(memories, purpose)
    return most_relevant(candidates, vacancy_text, limit=limit)


def most_relevant(
    memories: Sequence[Memory], text: str, *, limit: int = SELECT_LIMIT
) -> list[Memory]:
    """Order memories by how well they fit a text, without filtering them.

    Args:
        memories: Any memories.
        text: A vacancy, or any other text to compare with.
        limit: The most memories to return.

    Returns:
        The memories, best fitting first (see :func:`relevance`) and the most
        recently changed first among equals, trimmed to ``limit``.
    """
    terms = _VacancyTerms.of(text)
    scores = {m.id: terms.score(m) for m in memories}
    ranked = sorted(
        memories,
        key=lambda m: (scores[m.id], m.updated_at.timestamp(), m.id),
        reverse=True,
    )
    return ranked[: max(limit, 0)]


def select_memories(
    user: str,
    purpose: MemoryUse | str,
    vacancy_text: str,
    *,
    limit: int = SELECT_LIMIT,
) -> list[Memory]:
    """Pick a user's memories for one purpose and vacancy.

    Args:
        user: Name of an existing user.
        purpose: "cv", "letter" or "interview".
        vacancy_text: The vacancy's title and description.
        limit: The most memories to return.

    Returns:
        See :func:`rank_memories`. Sensitive memories are never included.

    Raises:
        MemoryStoreError: If the user does not exist.
        ValueError: If the purpose is not one of the three.
    """
    return rank_memories(list_memories(user), purpose, vacancy_text, limit=limit)


# -- Prompt material -------------------------------------------------------------


def memories_payload(memories: Sequence[Memory]) -> list[dict[str, Any]]:
    """Turn memories into the labelled prompt material for applicant facts.

    Where a memory came from is left out on purpose: it can name another
    employer, which has no place in this vacancy's letter. A sensitive memory
    is never included, whoever passes it in.

    Args:
        memories: Memories chosen by :func:`select_memories`.

    Returns:
        One entry per memory: ``label`` ("memory 3", what an answer cites),
        ``kind``, ``text``, ``noted_on`` (the date it was last changed, for
        weighing it against older sources) and, when set, ``hint`` and ``tags``.
    """
    entries: list[dict[str, Any]] = []
    for memory in memories:
        if memory.sensitive:
            logger.warning(f"Sensitive {memory.label} was kept out of a prompt")
            continue
        entry: dict[str, Any] = {
            "label": memory.label,
            "kind": memory.kind.value,
            "text": memory.text,
            "noted_on": memory.updated_at.date().isoformat(),
        }
        if memory.hint:
            entry["hint"] = memory.hint
        if memory.tags:
            entry["tags"] = list(memory.tags)
        entries.append(entry)
    return entries


def select_memories_payload(
    user: str,
    purpose: MemoryUse | str,
    vacancy_text: str,
    *,
    limit: int = SELECT_LIMIT,
) -> list[dict[str, Any]]:
    """Select a user's memories for a vacancy and return them as prompt material.

    Args:
        user: Name of an existing user.
        purpose: "cv", "letter" or "interview".
        vacancy_text: The vacancy's title and description.
        limit: The most memories to include.

    Returns:
        See :func:`memories_payload`; empty when nothing is eligible.

    Raises:
        MemoryStoreError: If the user does not exist.
        ValueError: If the purpose is not one of the three.
    """
    return memories_payload(select_memories(user, purpose, vacancy_text, limit=limit))


def memory_labels(payload: Sequence[dict[str, Any]]) -> set[str]:
    """Return the labels a model may cite, for validating its citations.

    Args:
        payload: What :func:`memories_payload` returned for the prompt.

    Returns:
        The labels, case-folded, such as {"memory 3", "memory 7"}.
    """
    return {str(entry["label"]).casefold() for entry in payload}


# -- Showing memories -----------------------------------------------------------

_ORIGINS = {
    MemorySource.MANUAL: "added by hand",
    MemorySource.TEXT_IMPORT: "taken from a text",
    MemorySource.LETTER_NOTES: "taken from letter notes",
    MemorySource.INTERVIEW_NOTES: "taken from interview notes",
}


def describe_origin(memory: MemoryDraft) -> str:
    """Say in words where a memory came from.

    Args:
        memory: A stored memory or a draft.

    Returns:
        Such as "taken from letter notes (notes for the Findwhere letter)".
    """
    origin = _ORIGINS[memory.source]
    return f"{origin} ({memory.source_detail})" if memory.source_detail else origin


def memory_matches(memory: MemoryContent, query: str) -> bool:
    """Tell whether a memory matches a search, ignoring case and accents.

    Args:
        memory: A memory.
        query: Words to look for; every one must occur in the text, the tags,
            the hint or the kind.

    Returns:
        True for an empty query or when every word occurs.
    """
    haystack = normalise_text(
        " ".join([memory.text, memory.hint, memory.kind.value, *memory.tags])
    )
    return all(word in haystack for word in normalise_text(query).split())
