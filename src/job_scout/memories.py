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
  the applicant wants off their CV simply lacks ``cv``, and a wish or a
  condition never has it.
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
from datetime import date, datetime
from enum import StrEnum
from math import ceil
from typing import Any, Self
from zoneinfo import ZoneInfo

from loguru import logger
from pydantic import (
    BaseModel,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from job_scout.config import list_users, user_db_path
from job_scout.database import Database

MAX_TEXT_CHARS = 600
MAX_HINT_CHARS = 200
MAX_TAGS = 8
MAX_TAG_CHARS = 40
MAX_SOURCE_DETAIL_CHARS = 200
# More memories than this in one prompt crowd out the CV they supplement. With
# fewer eligible memories all of them go, and the model decides with the text,
# hints and tags.
SELECT_LIMIT = 25
# A matching tag says more about fit than one shared word in the text.
_TAG_WEIGHT = 3

# The key the memories are added under in the applicant's facts.
MEMORIES_SOURCE_KEY = "memories"
# How to use memories, for a prompt that describes its memories itself, such
# as one that quotes them as a section of their own.
MEMORY_USE_GUIDE = (
    "Each memory has a label, a kind and a text, and often a hint on when it "
    "applies and tags; one added by hand may have neither. Use a memory only "
    "where its text, hint or tags fit this vacancy and leave the others out. A "
    "memory is the applicant's own statement and counts as evidence like the "
    "CV, except that a memory of kind preference or constraint is a wish or a "
    "condition, never experience. Never stretch a memory beyond what it says. "
    "Each memory has noted_on, the date it was last changed and how long ago "
    "that was: when two memories disagree, the more recent one holds. Read any "
    "relative time in a memory against its noted_on. Never state as current an "
    "availability, a start date, a plan or a course in progress that has passed "
    "or is several months old; leave it out, or say it as the memory dates it."
)
# One entry for the source guide of every prompt that receives memories as a
# key of the applicant's facts.
MEMORY_GUIDE = (
    "memories holds facts the applicant asked job-scout to remember about "
    "themselves: projects, results, skills, circumstances and wishes, often ones "
    "they left off their CV on purpose. " + MEMORY_USE_GUIDE
)
# The rule for CV tailoring, where the structure of the CV must stay intact.
MEMORY_CV_RULE = (
    "A memory may reword or add a bullet or a description under the existing "
    "role, study or project it belongs to, or add to the profile text. It never "
    "adds a role, an employer, a date, a school or a skill item that the CV does "
    "not already have. A wish or a condition (kind preference or constraint) "
    "goes nowhere on the CV, also not in the profile text."
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
# Kinds that describe a wish or a condition: useful for a letter or an
# interview, never a line on a CV. Every memory of these kinds loses the CV use,
# however it was made (see MemoryContent).
OFF_CV_KINDS: frozenset[MemoryKind] = frozenset(
    {MemoryKind.PREFERENCE, MemoryKind.CONSTRAINT}
)


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
            interview. Empty keeps the memory without using it anywhere. A
            wish or a condition (see :data:`OFF_CV_KINDS`) never has cv.
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

    @model_validator(mode="after")
    def _wishes_stay_off_the_cv(self) -> Self:
        """A wish or a condition is not experience and never goes on a CV."""
        if self.kind in OFF_CV_KINDS and MemoryUse.CV in self.use_in:
            self.use_in = [use for use in self.use_in if use is not MemoryUse.CV]
        return self


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


# A memory's label as a model may write it back: "memory 3", "Memory #3",
# "memory3", "memories 3", or in Dutch "herinnering 3" and "herinnering nr. 3",
# possibly after "your" or "je". The prompts always say "memory <id>".
_LABEL_WORD = r"(?:memor(?:y|ies)|herinnering(?:en)?)"
_WRITTEN_LABEL = re.compile(
    rf"(?:(?:your|my|the|je|jouw|mijn|de)\s+)?{_LABEL_WORD}\s*"
    r"(?:#|nr\.?|no\.?)?\s*(\d+)\b",
    re.IGNORECASE,
)


def parse_memory_label(value: object, *, bare_number: bool = False) -> str | None:
    """Read one memory citation the way a model wrote it.

    Letters, interview sets and CV tailoring all cite memories by label, and a
    model does not always copy the label exactly. Every place that checks a
    citation reads it here, so they accept the same spellings.

    Args:
        value: Text that starts with a label ("Memory #3", "herinnering 3
            (dbt)"), or a memory id as a whole number.
        bare_number: Also read text that is only a number, such as "3". Only
            for a field that holds nothing but memory citations: elsewhere a
            number names something else.

    Returns:
        The label as prompts write it, such as "memory 3"; None when the value
        names no memory.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return memory_label(value) if value > 0 else None
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    if bare_number and text.isascii() and text.isdigit():
        return parse_memory_label(int(text))
    match = _WRITTEN_LABEL.match(text)
    return parse_memory_label(int(match.group(1))) if match else None


class ForgottenMemory(BaseModel):
    """A memory the applicant deleted, kept so it is not captured again.

    Automatic capture reads the notes of every new letter or interview, and
    notes are often typed again with a small change. Without this record a
    deleted memory would come straight back from them.

    Attributes:
        id: The record's number, to erase this one record alone.
        text: The deleted memory's text.
        sensitive: Whether it was private; a private one is never shown to a
            model, not even as something to leave out.
        forgotten_at: When it was deleted.
    """

    id: int = 0
    text: str
    sensitive: bool = False
    forgotten_at: datetime


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
    """Delete one memory, and record it as forgotten.

    Automatic capture does not bring a forgotten memory back in the same or
    similar words (see :func:`list_forgotten` and
    :func:`repeats_forgotten`). For one that is not private the model is also
    asked to leave out other wordings of it; a private one never reaches a
    model, so it is compared in code only, and a fact written quite
    differently can come back, marked private. Adding it by hand or through a
    reviewed import still works.

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
    """Delete all of a user's memories, and record each as forgotten.

    Notes already captured stay recorded too, so regenerating a letter with
    the same notes costs no model call and brings nothing back.

    Args:
        user: Name of an existing user.

    Returns:
        How many memories were deleted.

    Raises:
        MemoryStoreError: If the user does not exist.
    """
    return _database(user).delete_all_memories()


def list_forgotten(user: str) -> list[ForgottenMemory]:
    """Return the memories a user deleted, most recently deleted first.

    Args:
        user: Name of an existing user.

    Returns:
        The forgotten memories, private ones included.

    Raises:
        MemoryStoreError: If the user does not exist.
    """
    rows = _database(user).get_forgotten_memories()
    return [ForgottenMemory.model_validate(row) for row in rows]


def clear_forgotten(user: str) -> int:
    """Erase the record of deleted memories.

    The texts are overwritten on disk and the database file is compacted,
    so their wording does not linger in it (see
    :meth:`Database.clear_forgotten_memories`). Automatic capture may find
    those facts again in new notes.

    Args:
        user: Name of an existing user.

    Returns:
        How many records were erased.

    Raises:
        MemoryStoreError: If the user does not exist.
    """
    return _database(user).clear_forgotten_memories()


def forget_completely(user: str, forgotten_id: int) -> bool:
    """Erase the record of one deleted memory, leaving the others.

    Its wording is then no longer sent along with automatic capture, and new
    notes may bring the fact back.

    Args:
        user: Name of an existing user.
        forgotten_id: The record's number, from :func:`list_forgotten`.

    Returns:
        True when the record was erased, False when there was none.

    Raises:
        MemoryStoreError: If the user does not exist.
    """
    return _database(user).remove_forgotten_memory(forgotten_id)


# -- Comparing text --------------------------------------------------------------

# Endings stripped before words are compared, longest first, so "projects" and
# "projecten" meet "project", "testing" and "tested" meet "test", and a field
# meets its role: "engineering" and "engineer", "management" and "manager",
# "ontwikkeling" and "ontwikkelaar".
_SUFFIXES = ("ingen", "ment", "aar", "ing", "ers", "er", "en", "es", "ed", "e", "s")
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
    """Strip common English or Dutch endings, keeping at least four letters.

    Endings are stripped again until none applies, so "engineering",
    "engineer" and "engineers" all meet at "engin".

    Args:
        word: A normalised word.

    Returns:
        The word without its endings.
    """
    while True:
        for suffix in _SUFFIXES:
            if word.endswith(suffix) and len(word) - len(suffix) >= 4:
                word = word[: -len(suffix)]
                break
        else:
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


# Comparing two memory texts. Words that turn a claim around when they are
# added, dropped or swapped must match exactly: a negation, a number and a
# modal verb ("I will finish" is not "I finished"). Negations all count as one.
_NEGATION_TEXT = (
    "not no never none nothing nobody neither nor cannot "
    "niet geen nooit niets niemand noch nergens"
)
_MODAL_TEXT = (
    "will ll would shall should can could may might must want wants wanted wish "
    "hope prefer rather zal zult zullen zou zouden kan kun kunt kunnen kon konden "
    "mag mogen mocht moet moeten moest wil wilt willen wilde wens hoop liever"
)
# Numbers written as words, compared like digits. The Dutch "een" is left out:
# it is also the article, and a different number on the other side differs.
_NUMBER_WORD_TEXT = (
    "one two three four five six seven eight nine ten eleven twelve thirteen "
    "fourteen fifteen sixteen seventeen eighteen nineteen twenty thirty forty "
    "fifty sixty seventy eighty ninety hundred hundreds thousand thousands "
    "million millions billion dozen half third thirds quarter quarters twice "
    "double triple doubled tripled halved first second fourth fifth "
    "twee drie vier vijf zes zeven acht negen tien elf twaalf dertien veertien "
    "vijftien zestien zeventien achttien negentien twintig dertig veertig "
    "vijftig zestig zeventig tachtig negentig honderd honderden duizend "
    "duizenden miljoen miljoenen miljard dozijn halve helft derde kwart "
    "tweemaal dubbel verdubbeld verdrievoudigd gehalveerd eerste tweede vierde "
    "vijfde"
)
# Words that carry no claim of their own: articles, pronouns, forms of "be",
# "have" and "do", and prepositions that only link. Words such as "since",
# "until", "voor" or "only" are not here: they change what is claimed.
_FUNCTION_WORD_TEXT = (
    "a an the i me my mine we us our you your he him his she her it its they "
    "them their this that these those who whom whose which what am is are was "
    "were be been being have has had having do does did done to of in on at by "
    "for with from into onto as and or but so also too very just there here "
    "then than m ve s d "
    "de het een ik mij me mijn wij we ons onze jij je jou jouw u uw hij hem "
    "zijn zij ze haar hun hen dit dat deze die wie wat welke ben bent is was "
    "waren geweest heb hebt heeft hebben had hadden gehad doe doet deed deden "
    "gedaan te van in op aan bij met uit naar om door en of maar dus ook er "
    "daar hier dan"
)
# Words that relate two things in a set order: a comparison, a direction or a
# replacement. The word after one is part of the claim: "rather in Utrecht
# than in Amsterdam" is not "rather in Amsterdam than in Utrecht".
_RELATION_TEXT = (
    "than over from to into instead versus vs before after dan boven van naar tot na"
)
_NEGATIONS = frozenset(_NEGATION_TEXT.split())
_MODALS = frozenset(_MODAL_TEXT.split())
_NUMBER_WORDS = frozenset(_NUMBER_WORD_TEXT.split())
_FUNCTION_WORDS = frozenset(_FUNCTION_WORD_TEXT.split())
_RELATIONS = frozenset(_RELATION_TEXT.split())
# English contractions, spelt out before comparing so "don't" meets "do not".
_CONTRACTIONS = (
    (re.compile(r"\b(?:can['\u2019]t|cannot)\b"), "can not"),
    (re.compile(r"\bwon['\u2019]t\b"), "will not"),
    (re.compile(r"n['\u2019]t\b"), " not"),
)


def _claim_words(text: str) -> tuple[str, ...]:
    """Return the normalised words of a text, with contractions spelt out.

    Args:
        text: A memory text.

    Returns:
        The words, in order.
    """
    folded = text.casefold()
    for pattern, replacement in _CONTRACTIONS:
        folded = pattern.sub(replacement, folded)
    return tuple(normalise_text(folded).split())


def _marker(word: str) -> str:
    """Return what a word must match exactly in another text, if anything.

    Args:
        word: A normalised word.

    Returns:
        "not" for any negation, the word itself for a number or a modal verb,
        and "" for any other word.
    """
    if word in _NEGATIONS:
        return "not"
    if word in _NUMBER_WORDS or word in _MODALS or any(c.isdigit() for c in word):
        return word
    return ""


def _relations(words: tuple[str, ...]) -> frozenset[tuple[str, str]]:
    """Pair each relation word with the stem of the first content word after it.

    Args:
        words: The normalised words of a text, in order.

    Returns:
        Pairs such as ("than", "amsterdam") for "than in Amsterdam".
    """
    pairs: set[tuple[str, str]] = set()
    for index, word in enumerate(words):
        if word not in _RELATIONS:
            continue
        following = (
            later
            for later in words[index + 1 :]
            if later not in _RELATIONS
            and later not in _FUNCTION_WORDS
            and not _marker(later)
        )
        target = next(following, None)
        if target is not None:
            pairs.add((word, _stem(target)))
    return frozenset(pairs)


@dataclass(frozen=True)
class _Claim:
    """What a memory text claims, reduced for comparing it with another.

    Attributes:
        words: Its normalised words, in order.
        markers: Its negations, numbers and modal verbs, sorted, with repeats.
        content: The stems of its other words that carry meaning.
        relations: Each comparison or direction word with the word after it.
    """

    words: tuple[str, ...]
    markers: tuple[str, ...]
    content: frozenset[str]
    relations: frozenset[tuple[str, str]]

    @classmethod
    def of(cls, text: str) -> _Claim:
        """Reduce a memory text.

        Args:
            text: A memory text.

        Returns:
            Its claim.
        """
        words = _claim_words(text)
        markers = tuple(sorted(filter(None, (_marker(word) for word in words))))
        content = frozenset(
            _stem(word)
            for word in words
            if not _marker(word) and word not in _FUNCTION_WORDS
        )
        return cls(
            words=words,
            markers=markers,
            content=content,
            relations=_relations(words),
        )

    def negated(self) -> bool:
        """Tell whether the text holds a negation.

        Returns:
            True when one of its markers is a negation.
        """
        return "not" in self.markers

    def adds_nothing_to(self, known: _Claim) -> bool:
        """Tell whether this claim says nothing the known one does not.

        A number or a modal verb may be left out, never added or changed.

        Args:
            known: The claim to compare with.

        Returns:
            True when the negation is the same, every marker, content word
            and relation of this claim is in the known one.
        """
        return (
            self.negated() == known.negated()
            and set(self.markers) <= set(known.markers)
            and self.content <= known.content
            and self.relations <= known.relations
        )


def same_fact(first: str, second: str) -> bool:
    """Tell whether a new memory text adds nothing to a known one.

    Code only catches what is plainly the same; telling two wordings of one
    fact apart is left to the model, which sees the existing memories. So
    the second text repeats the first only when they are equal once case,
    accents, punctuation and contractions are ignored, or when both hold:

    * the same negations, numbers (in digits or words) and modal verbs. A
      different number is a different fact ("since 2019" is not "since
      2020"), and so is a flipped wish ("I do not want to travel").
    * every word of the second that carries meaning (after light stemming)
      occurs in the first. Another employer, another tool or any added
      detail makes a new fact; a shorter wording of the same fact does not.

    Word order is not compared, except for the word after a comparison or
    direction word (than, over, from, to, dan, van, naar and the like): "I
    would rather work in Utrecht than in Amsterdam" is not repeated by the
    same sentence turned around.

    Args:
        first: The known memory text.
        second: The new text.

    Returns:
        True when the second adds nothing the first does not say. Not
        symmetric: a longer second text that adds a detail is new.
    """
    known, new = _Claim.of(first), _Claim.of(second)
    if known.words == new.words:
        return True
    if not known.words or not new.words or known.markers != new.markers:
        return False
    return new.content <= known.content and new.relations <= known.relations


# Of the meaning words a draft shares with a deleted private memory, this part
# of the shorter text makes it the same matter for code. Such a memory never
# reaches the model, so code is the only check and is loose on purpose.
_PRIVATE_OVERLAP = 0.6


def repeats_forgotten(item: ForgottenMemory, text: str) -> bool:
    """Tell whether a proposed memory brings a deleted one back.

    Looser than :func:`same_fact`, because leaving a real fact out of
    automatic capture costs little (the applicant can add it by hand) while
    bringing a deleted one back breaks a promise. The text repeats the
    deleted memory when:

    * :func:`same_fact` says so, or
    * it adds nothing to it, even when it leaves a number out ("Ik heb een
      burn-out gehad" after "Ik heb in 2022 een burn-out gehad"), or
    * the deleted memory was private and the text has the same negation, no
      number or modal verb the deleted one lacks, and shares most of the
      meaning words of the shorter of the two ("doorgemaakt" for "gehad").

    Args:
        item: A memory the applicant deleted.
        text: A proposed memory text.

    Returns:
        True when the text should not be captured.
    """
    if same_fact(item.text, text):
        return True
    old, new = _Claim.of(item.text), _Claim.of(text)
    if not old.words or not new.words:
        return False
    if new.adds_nothing_to(old):
        return True
    if not item.sensitive or new.negated() != old.negated():
        return False
    if not set(new.markers) <= set(old.markers):
        return False
    smaller = min(len(old.content), len(new.content))
    needed = max(1, ceil(_PRIVATE_OVERLAP * smaller))
    return len(old.content & new.content) >= needed


def private_overlap(private_text: str, text: str) -> tuple[int, float]:
    """Measure how much a text shares with a private memory.

    Numbers count as shared words too: "2021" and "operatie" in both is
    already a strong sign that the text speaks of the same private matter.

    Args:
        private_text: The text of a private memory, stored or deleted.
        text: A proposed memory text.

    Returns:
        How many meaning words and markers the two share, and what part of
        the shorter text that is (0.0 when either is empty).
    """
    old, new = _Claim.of(private_text), _Claim.of(text)
    old_terms = old.content | set(old.markers)
    new_terms = new.content | set(new.markers)
    shared = len(old_terms & new_terms)
    base = min(len(old_terms), len(new_terms))
    return shared, (shared / base if base else 0.0)


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
        raw: The text as written, for words that only count in capitals.
        padded: The normalised text with a space on either side.
        stems: The stems of every word.
        significant: The stems of the words that say something about fit.
    """

    raw: str
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
            raw=vacancy_text,
            padded=f" {normalised} ",
            stems=_stems(normalised),
            significant=significant_words(vacancy_text),
        )

    def _word_fits(self, word: str) -> bool:
        """Tell whether one word of a tag occurs in the vacancy.

        A word of one or two letters, or a stopword, is also an everyday word
        ("it", "go", "team"), so it counts only where the vacancy writes it
        in capitals, as in "IT" or "HR".

        Args:
            word: A normalised word of a tag.

        Returns:
            True when it occurs.
        """
        if len(word) < 3 or word in _STOPWORDS:
            capitals = rf"(?<!\w){re.escape(word.upper())}(?!\w)"
            return re.search(capitals, self.raw) is not None
        return _stem(word) in self.stems

    def tag_fits(self, tag: str) -> bool:
        """Tell whether a tag occurs in the vacancy.

        Args:
            tag: A memory tag.

        Returns:
            True when a tag of several words occurs as a phrase, or when every
            word of the tag occurs (see :meth:`_word_fits`).
        """
        words = normalise_text(tag).split()
        if not words:
            return False
        if len(words) > 1 and f" {' '.join(words)} " in self.padded:
            return True
        return all(self._word_fits(word) for word in words)

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
        Three points per tag found in the vacancy (see
        :meth:`_VacancyTerms.tag_fits`) plus one per significant word the
        memory's text and hint share with it; 0 for no overlap.
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
        order given. A wish or a condition is never eligible for a CV, even
        one built without validation.

    Raises:
        ValueError: If the purpose is not one of the three.
    """
    use = MemoryUse(purpose)
    return [
        m
        for m in memories
        if use in m.use_in
        and not m.sensitive
        and not (use is MemoryUse.CV and m.kind in OFF_CV_KINDS)
    ]


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

_MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
# The zone the applicant lives in; "today" is their day, not the server's.
_HOME = ZoneInfo("Europe/Amsterdam")


def today_here() -> date:
    """Return the applicant's current date.

    Returns:
        Today in the Netherlands.
    """
    return datetime.now(_HOME).date()


def spoken_date(day: date) -> str:
    """Write a date the way the prompts and pages do.

    Args:
        day: A date.

    Returns:
        Such as "19 September 2026": the month as a word, never a number,
        so no date with hyphens ends up in a memory or a prompt.
    """
    return f"{day.day} {_MONTH_NAMES[day.month - 1]} {day.year}"


def _age(then: date, today: date) -> str:
    """Say how long ago a date was.

    Args:
        then: The earlier date.
        today: The current date.

    Returns:
        Such as "today", "5 days ago", "6 months ago" or "2 years ago".
    """
    days = (today - then).days
    months = (today.year - then.year) * 12 + today.month - then.month
    if today.day < then.day:
        months -= 1
    if days <= 0:
        return "today"
    if months < 1:
        return "1 day ago" if days == 1 else f"{days} days ago"
    if months < 12:
        return "1 month ago" if months == 1 else f"{months} months ago"
    years = months // 12
    return "1 year ago" if years == 1 else f"{years} years ago"


def noted_on(memory: Memory, today: date | None = None) -> str:
    """Say when a memory was last changed and how long ago that was.

    Args:
        memory: A stored memory.
        today: The current date; the applicant's today when None.

    Returns:
        Such as "19 March 2026 (6 months ago)".
    """
    changed = memory.updated_at.astimezone(_HOME).date()
    return f"{spoken_date(changed)} ({_age(changed, today or today_here())})"


def memories_payload(
    memories: Sequence[Memory], *, today: date | None = None
) -> list[dict[str, Any]]:
    """Turn memories into the labelled prompt material for applicant facts.

    Where a memory came from is left out on purpose: it can name another
    employer, which has no place in this vacancy's letter. A sensitive memory
    is never included, whoever passes it in.

    Args:
        memories: Memories chosen by :func:`select_memories`.
        today: The current date, for how long ago each memory was noted; the
            applicant's today when None.

    Returns:
        One entry per memory: ``label`` ("memory 3", what an answer cites),
        ``kind``, ``text``, ``noted_on`` (the date it was last changed and
        how long ago that was, for weighing it against older sources and for
        reading relative time in it) and, when set, ``hint`` and ``tags``.
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
            "noted_on": noted_on(memory, today),
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
    today: date | None = None,
) -> list[dict[str, Any]]:
    """Select a user's memories for a vacancy and return them as prompt material.

    Args:
        user: Name of an existing user.
        purpose: "cv", "letter" or "interview".
        vacancy_text: The vacancy's title and description.
        limit: The most memories to include.
        today: The current date; the applicant's today when None.

    Returns:
        See :func:`memories_payload`; empty when nothing is eligible.

    Raises:
        MemoryStoreError: If the user does not exist.
        ValueError: If the purpose is not one of the three.
    """
    chosen = select_memories(user, purpose, vacancy_text, limit=limit)
    return memories_payload(chosen, today=today)


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
