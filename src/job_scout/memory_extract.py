"""Turn free text into memories, on request or automatically from notes.

Two ways in, one extraction. :func:`extract_memories` asks the model once for
the facts about the applicant in a text and returns them as drafts: the
dashboard shows them to tick, edit and save, and ``job-scout memory import``
saves them after asking. :func:`capture_from_notes` runs the same extraction
on the notes the applicant typed for a letter or an interview and saves what
it finds straight away, in the background, so a fact mentioned once for one
vacancy is there for the next.

The model judges what is worth keeping, how to generalise it and whether it
repeats a memory in other words; code keeps a floor under that judgement.
Every draft is put in the house style, checked against the limits of
:class:`job_scout.memories.MemoryContent`, compared with what is already
remembered (see :func:`job_scout.memories.same_fact`, which only catches the
plainly identical) and capped in number. Wishes and conditions never go on a
CV, and a text, hint or tag that plainly says the applicant has a private
matter is marked sensitive even when the model did not say so. Sensitive
memories are never shown to the model, not even here as existing memories;
they are compared with in code only.

Automatic capture remembers the hash of every notes text it handled, so
generating again with the same notes costs no second model call. It also
leaves out every memory the applicant deleted, so notes typed again with a
change do not bring one back. It can be switched off per user with the
``memory_auto_capture`` setting.
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
from pydantic import BaseModel, Field, ValidationError

from job_scout.config import build_effective_config, set_config_value, user_db_path
from job_scout.database import Database
from job_scout.llm.base import LLMClient, LLMError
from job_scout.llm.factory import get_llm_client
from job_scout.memories import (
    ALL_USES,
    MAX_HINT_CHARS,
    MAX_SOURCE_DETAIL_CHARS,
    MAX_TEXT_CHARS,
    ForgottenMemory,
    Memory,
    MemoryContent,
    MemoryDraft,
    MemoryKind,
    MemorySource,
    MemoryUse,
    add_memories,
    find_duplicate,
    list_forgotten,
    list_memories,
    most_relevant,
    normalise_tags,
    normalise_text,
    require_memory_user,
    same_fact,
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
# Deleted memories shown to the model so it does not propose them again in
# other words, the most recently deleted first. Code compares with all of them.
MAX_FORGOTTEN_IN_PROMPT = 40
# Notes shorter than this are an instruction ("kort houden"), not a fact.
MIN_NOTES_WORDS = 3
# A capture that has not finished after this long was stopped halfway (a
# restart during the model call); the notes may then be captured again.
CLAIM_STALE_AFTER = timedelta(minutes=30)

# Kinds that describe a wish or a condition: useful for a letter or an
# interview, never a line on a CV.
_OFF_CV_KINDS = frozenset({MemoryKind.PREFERENCE, MemoryKind.CONSTRAINT})
# The floor under the model's own "sensitive" judgement: phrases in which the
# applicant is the one a private matter happens to (health, pregnancy, faith,
# politics and union membership, sexuality, a criminal record, debts and
# benefits, divorce, caring for a relative). A bare topic word is not enough:
# "bijstand", "vakbond", "divorce" or "sick leave" also name the work of a
# klantmanager, an HR adviser, a family lawyer or an analyst, and a work fact
# marked private would silently be kept out of every document. Those cases are
# left to the model. Matched on normalised text: "burn-out" reads "burn out"
# and "I'm" reads "i m".
_PARTIES = (
    "vakbond|trade union|union|politieke partij|political party|partij|party|"
    "pvda|vvd|cda|d66|groenlinks|sp|pvv|bbb|christenunie|sgp|ja21|volt|fvd|"
    "denk|nsc"
)
_ILLNESSES = (
    "burn out|burnout|depressie|depression|angststoornis|anxiety disorder|adhd|"
    "autisme|autism|dyslexie|dyslexia|kanker|cancer|chronische ziekte|"
    "chronic illness|handicap|disability|beperking|diabetes|epilepsie|epilepsy"
)
_PRIVATE_PHRASES = (
    # Health and pregnancy.
    "(?:mijn|my) (?:burn out|burnout|depressie|depression|ziekte|illness|"
    "handicap|disability|beperking|ziekteverlof|sick leave|"
    "arbeidsongeschiktheid|overspannenheid|zwangerschap|zwangerschapsverlof|"
    "pregnancy|maternity leave|ouderschapsverlof|parental leave)",
    r"(?:ik ben|ik was|ik raakte|ik werd) (?:\w+ ){0,2}(?:overspannen|ziek|"
    "arbeidsongeschikt|zwanger|gediagnosticeerd)",
    r"(?:i am|i m|i was|i became|i got|i have been|i ve been) (?:\w+ ){0,2}"
    "(?:pregnant|chronically ill|seriously ill|diagnosed|burnt out|burned out)",
    "(?:i am|i m|i was|i have been|i ve been) on (?:sick|maternity|parental|"
    "medical) leave",
    "(?:ik ben|ik was|ik zat|ik zit) (?:met|in de|in) (?:ziekteverlof|"
    "zwangerschapsverlof|ouderschapsverlof|ziektewet|therapie|een burn out)",
    "(?:ik heb|ik had|ik kreeg|ik lijd aan|ik leed aan|ik leef met|i have|i ve|"
    "i had|i got|i suffer from|i suffered from|i live with) (?:een |a |an )?"
    f"(?:{_ILLNESSES})",
    "(?:hersteld|herstellende|herstel|recovered|recovering|recovery) "
    "(?:van|from) (?:een|a|an|mijn|my) (?:burn out|burnout|depressie|"
    "depression|ziekte|illness|operatie|surgery|kanker|cancer|ongeluk|accident)",
    # Faith.
    "(?:mijn|my) (?:geloof|religie|faith|religion|kerk|church|moskee|mosque)",
    "(?:ik ben|i am|i m) (?:een |a |an )?(?:moslim|muslim|christen|christian|"
    "jood|joods|jewish|katholiek|catholic|protestant|hindoe|hindu|boeddhist|"
    "buddhist|gelovig|atheist)",
    r"(?:ik vast|i fast|i observe|ik vier|i celebrate) (?:\w+ ){0,2}(?:ramadan|"
    "sabbat|sabbath|shabbat|suikerfeest|eid)",
    # Politics and union membership.
    "(?:ik ben|ik was|i am|i m|i was) (?:een |a |an )?(?:actief |active )?"
    rf"(?:lid|member) (?:van|of) (?:de |het |een |the |a )?(?:\w+ )?(?:{_PARTIES})",
    f"(?:ik stem|ik stemde|i vote|i voted) (?:op|for) (?:de |the )?(?:{_PARTIES})",
    "(?:mijn|my) (?:politieke|political) (?:voorkeur|partij|overtuiging|kleur|"
    "views|party|beliefs|preference|affiliation)",
    # Sexuality.
    "(?:ik ben|i am|i m) (?:een |a )?(?:homo|homoseksueel|lesbisch|lesbienne|"
    "biseksueel|gay|lesbian|bisexual|transgender|trans|queer|non binair|"
    "non binary)",
    # A criminal record.
    "(?:mijn|my) (?:strafblad|criminal record|veroordeling)",
    "(?:ik heb|ik had|i have|i ve|i had) (?:een |a )?(?:strafblad|criminal record)",
    r"(?:ik ben|ik werd|i was|i have been|i ve been) (?:\w+ )?(?:veroordeeld|"
    "convicted)",
    # Debts and benefits.
    "(?:mijn|my) (?:schulden|debts|uitkering|faillissement|bankruptcy|"
    "bewindvoerder|bewindvoering)",
    "(?:ik heb|ik had|i have|i ve|i had) (?:een |a )?(?:schulden|debts|"
    "uitkering|bewindvoerder)",
    r"(?:ik zit|ik zat|ik leef|ik leefde) (?:\w+ ){0,2}(?:in de |van de |in )"
    "(?:bijstand|uitkering|schuldsanering|schulden|bewindvoering|wsnp)",
    "(?:ik ontvang|ik ontving|ik kreeg) (?:een )?(?:bijstand|uitkering)",
    "(?:i am|i m|i was|i have been|i ve been|i live|i lived) (?:in debt|"
    "on benefits|on welfare|bankrupt)",
    # Divorce and caring for a relative.
    "(?:mijn|my) (?:scheiding|echtscheiding|divorce|mantelzorg)",
    "(?:ik ben|ik was|i am|i m|i was|i got) (?:gescheiden|divorced|mantelzorger)",
    r"(?:ik zorg|ik zorgde|i care|i cared|i look after|i looked after) "
    r"(?:\w+ ){0,4}(?:voor |for )?(?:mijn|my) (?:zieke |sick |ill )?(?:vader|"
    "moeder|ouders|partner|man|vrouw|father|mother|parents|husband|wife)",
)
_PRIVATE_MATTER = re.compile(rf"\b(?:{'|'.join(_PRIVATE_PHRASES)})\b")
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
    "person. Leave out anything the EXISTING MEMORIES already say, and "
    "anything the FORGOTTEN MEMORIES say in any wording: the applicant deleted "
    "those and does not want them back.\n"
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


class MemoryExtraction(BaseModel):
    """What one extraction proposed.

    Attributes:
        drafts: Proposed memories that state something not yet remembered,
            at most :data:`MAX_DRAFTS`. Nothing is stored.
        truncated: The text may hold more than was proposed: the model
            returned as many memories as it was allowed, or more new ones
            than :data:`MAX_DRAFTS`. Running the text again after saving the
            drafts gets the rest; say so to the applicant.
    """

    drafts: list[MemoryDraft] = Field(default_factory=list)
    truncated: bool = False


@dataclass(frozen=True)
class _Origin:
    """Where the text being turned into memories came from.

    Attributes:
        source: How the memories are made.
        detail: Where the text came from in words, possibly empty. Stored
            with each draft and never put in the prompt: it can hold a
            company name from a scraped vacancy.
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

    Only the kind of source is named. The detail stays out: it is not
    needed to find facts, and a company name in it comes from a vacancy an
    outsider wrote, which must not end up among the instructions.

    Args:
        origin: Where the text came from.

    Returns:
        One sentence.
    """
    return f"{_CONTEXTS[origin.source]}."


def _forgotten_block(forgotten: Sequence[ForgottenMemory]) -> str:
    """List the deleted memories the model may see, most recent first.

    Private ones are left out: they never reach a model.

    Args:
        forgotten: The memories the applicant deleted.

    Returns:
        A JSON list of their texts, or "[]" when there are none.
    """
    shown = [item.text for item in forgotten if not item.sensitive]
    return json.dumps(shown[:MAX_FORGOTTEN_IN_PROMPT], ensure_ascii=False)


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


def _prompt(
    text: str,
    existing: Sequence[Memory],
    forgotten: Sequence[ForgottenMemory],
    origin: _Origin,
) -> str:
    """Build the one extraction prompt.

    Args:
        text: The text to read.
        existing: Every stored memory.
        forgotten: The memories the applicant deleted, to leave out.
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
        "FORGOTTEN MEMORIES (deleted by the applicant, never propose them "
        "again):\n"
        f"{_forgotten_block(forgotten)}\n\n"
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
    """Tell whether a text plainly says the applicant has a private matter.

    The floor under the model's own ``sensitive`` judgement: it catches the
    obvious cases the model missed and nothing subtle. Only phrases in which
    the applicant is the one it happens to count, such as "ik ben zwanger",
    "my divorce" or "recovered from a burnout"; a topic word alone ("bijstand",
    "sick leave") also describes someone's work and is left to the model.

    Args:
        text: A memory text, hint or tag.

    Returns:
        True when such a phrase occurs.
    """
    return _PRIVATE_MATTER.search(normalise_text(text)) is not None


def _draft(item: dict[str, Any], origin: _Origin) -> MemoryDraft | None:
    """Turn one proposed memory into a validated draft.

    The private-matter floor is applied to the text, the hint and every tag:
    all three reach the generators.

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
    kind, tags = _kind(item.get("kind")), normalise_tags(item.get("tags"))
    hint = hint if len(hint) <= MAX_HINT_CHARS else ""
    private = any(touches_private_matter(part) for part in (text, hint, *tags))
    try:
        return MemoryDraft(
            text=text,
            kind=kind,
            tags=tags,
            hint=hint,
            use_in=_uses(item.get("use_in"), kind),
            sensitive=_truthy(item.get("sensitive")) or private,
            source=origin.source,
            source_detail=origin.detail,
            job_id=origin.job_id,
        )
    except ValidationError as exc:
        logger.debug(f"Dropped a proposed memory: {exc}")
        return None


def _is_forgotten(text: str, forgotten: Sequence[ForgottenMemory]) -> bool:
    """Tell whether a text repeats a memory the applicant deleted.

    Args:
        text: A proposed memory text.
        forgotten: The memories the applicant deleted.

    Returns:
        True when it adds nothing to one of them (see ``same_fact``).
    """
    return any(same_fact(item.text, text) for item in forgotten)


def _new_only(
    drafts: Sequence[MemoryDraft],
    existing: Sequence[MemoryContent],
    forgotten: Sequence[ForgottenMemory],
) -> list[MemoryDraft]:
    """Drop drafts that repeat a known, a forgotten or an earlier draft.

    Args:
        drafts: Proposed memories, in the model's order.
        existing: Every stored memory, sensitive ones included.
        forgotten: Deleted memories that must not come back.

    Returns:
        The drafts that state something new, not capped.
    """
    kept: list[MemoryDraft] = []
    for draft in drafts:
        known = find_duplicate(draft.text, existing) or find_duplicate(draft.text, kept)
        if known is not None or _is_forgotten(draft.text, forgotten):
            logger.debug(f"Dropped a proposed memory already known: {draft.text!r}")
            continue
        kept.append(draft)
    return kept


def keep_new(
    drafts: Sequence[MemoryDraft],
    existing: Sequence[MemoryContent],
    forgotten: Sequence[ForgottenMemory] = (),
) -> list[MemoryDraft]:
    """Drop drafts that repeat a stored memory or an earlier draft, and cap them.

    Args:
        drafts: Proposed memories, in the model's order.
        existing: Every stored memory, sensitive ones included.
        forgotten: Deleted memories that must not come back, for automatic
            capture; empty where the applicant reviews the drafts.

    Returns:
        At most :data:`MAX_DRAFTS` drafts that state something new.
    """
    return _new_only(drafts, existing, forgotten)[:MAX_DRAFTS]


def _ask(
    body: str,
    existing: Sequence[Memory],
    forgotten: Sequence[ForgottenMemory],
    origin: _Origin,
    client: LLMClient,
) -> list[dict[str, Any]]:
    """Make the one model call and return the proposed memories as objects.

    Args:
        body: The text to read, stripped and within the limit.
        existing: Every stored memory.
        forgotten: The memories the applicant deleted, to leave out.
        origin: Where the text came from.
        client: The model to ask.

    Returns:
        The objects in the model's list of memories.

    Raises:
        MemoryExtractionError: If the model's answer is unreadable.
        LLMError: If the model call fails.
    """
    prompt = _prompt(body, existing, forgotten, origin)
    raw = client.complete(prompt, purpose="cv_parsing", timeout=EXTRACT_TIMEOUT)
    return _items(raw)


def extract_memories(
    text: str,
    existing: Sequence[Memory],
    client: LLMClient,
    *,
    source: MemorySource | str,
    source_detail: str = "",
    job_id: int | None = None,
    forgotten: Sequence[ForgottenMemory] = (),
) -> MemoryExtraction:
    """Turn free text into proposed memories with one model call.

    Args:
        text: What the applicant wrote: notes, a pasted text, a document.
        existing: The applicant's stored memories, from
            :func:`job_scout.memories.list_memories`. Sensitive ones are
            compared with in code but never shown to the model.
        client: The model to ask.
        source: How the memories are made, e.g. "text_import".
        source_detail: Where the text came from in words, such as "notes for
            the Findwhere letter"; stored with every draft, never sent to the
            model.
        job_id: The vacancy the text was written for, stored for reference.
        forgotten: Memories the applicant deleted, from
            :func:`job_scout.memories.list_forgotten`, that must not come
            back. Automatic capture passes them; a reviewed import leaves
            them out, so the applicant can take a fact back on purpose.
            Private ones are compared with in code only.

    Returns:
        The drafts that state something not yet remembered, in the house
        style, at most :data:`MAX_DRAFTS`, and whether the text may hold
        more. No drafts for an empty text or when nothing is worth
        remembering. Nothing is stored.

    Raises:
        MemoryExtractionError: If the text is over :data:`MAX_INPUT_CHARS` or
            the model's answer is unreadable.
        ValueError: If the source is not a known one.
        LLMError: If the model call fails.
    """
    body = text.strip()
    if not body:
        return MemoryExtraction()
    if len(body) > MAX_INPUT_CHARS:
        raise MemoryExtractionError(
            f"The text is too long. Use at most {MAX_INPUT_CHARS:,} characters "
            "at a time."
        )
    origin = _Origin.of(source, source_detail, job_id)
    items = _ask(body, existing, forgotten, origin, client)
    proposed = [draft for draft in (_draft(i, origin) for i in items) if draft]
    fresh = _new_only(proposed, existing, forgotten)
    truncated = len(items) >= MAX_DRAFTS or len(fresh) > MAX_DRAFTS
    logger.info(
        f"Proposed {min(len(fresh), MAX_DRAFTS)} memories from {origin.source}; "
        f"{len(proposed) - len(fresh)} already known"
        + ("; the text may hold more" if truncated else "")
    )
    return MemoryExtraction(drafts=fresh[:MAX_DRAFTS], truncated=truncated)


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

    Memories the applicant deleted are left out, also when the notes were
    typed again with a change. The memories are read again just before
    storing, so a capture of other notes that finished meanwhile, or a delete
    made meanwhile, is respected.

    Args:
        user: Name of an existing user.
        notes: The notes as typed.
        origin: Where the notes were typed.
        client: The model to ask; the user's configured one when None.

    Returns:
        The stored memories.
    """
    model = client or get_llm_client(build_effective_config(user))
    found = extract_memories(
        notes,
        list_memories(user),
        model,
        source=origin.source,
        source_detail=origin.detail,
        job_id=origin.job_id,
        forgotten=list_forgotten(user),
    )
    if not found.drafts:
        return []
    fresh = keep_new(found.drafts, list_memories(user), list_forgotten(user))
    return add_memories(user, fresh)


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
    that fails is not recorded, so the next generation tries again. A memory
    the applicant deleted is never stored again (see
    :func:`job_scout.memories.list_forgotten`).

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
