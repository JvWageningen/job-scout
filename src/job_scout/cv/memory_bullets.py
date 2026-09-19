"""Checking the bullets a memory adds to a tailored CV entry.

CV tailoring (:mod:`job_scout.cv.tailor`) may give an experience entry one new
bullet for each memory the entry's patch names. The name proves nothing by
itself: a model can name any memory it was given and write whatever it likes
under it. So every new bullet is held against the memory it claims to state:

* The memory must belong to the entry. It may not say the applicant worked at
  another organisation ("I worked at Globex as Head of Data"), and it may not
  name another employer or school on the CV without naming this entry's own.
* The bullet must say what the memory says. It shares at least two of the
  memory's significant words or numbers, every number and every name written
  with a capital in it appears in the memory or in the entry as it was, and it
  does not put its work at an organisation other than the entry's ("at Globex").
* A memory backs one new bullet in the whole CV, and a wish or a condition
  (kind preference or constraint) backs none.

New bullets come after the entry's own ones, so the bullets beyond the entry's
original count are the ones checked. The checks read words, not meaning, and
can refuse a fair bullet; :mod:`job_scout.cv.tailor` then keeps the entry's own
bullets rather than failing the whole CV. They cannot pass an invented number,
a name the memory does not hold, or a memory about another employer.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from job_scout.cv.models import (
    CVDocument,
    EducationSection,
    ExperienceEntry,
    ExperienceSection,
)
from job_scout.memories import (
    MemoryKind,
    normalise_text,
    parse_memory_label,
    significant_words,
)

# Wishes and conditions tell a letter what the applicant wants; they are never
# a line on a CV.
_WISHES = frozenset({MemoryKind.PREFERENCE.value, MemoryKind.CONSTRAINT.value})
# Significant words or numbers a new bullet must share with its memory.
_MIN_SHARED = 2

# An organisation's name: capitalised words, such as "Globex Industries".
_NAME = r"[A-Z][\w&'.]*(?:[ \t]+[A-Z][\w&'.]*)*"
# A memory that places the applicant at an organisation: "I worked at Globex",
# "werkte als analist bij Globex", "as Head of Data at Globex". The bare noun
# "work" is left out: "the reporting work for Finance" names a department.
_PLACED_AT = (
    re.compile(
        r"\b(?i:worked|working|works|employed|i work|werkte|werkten|werkt|ik werk|"
        r"werkzaam|gewerkt|in dienst)\b[^.;:]{0,40}?\b(?i:at|for|bij|voor)\s+"
        rf"(?P<name>{_NAME})"
    ),
    re.compile(rf"\b(?i:as|als)\s[^.;:]{{1,40}}?\s(?i:at|bij)\s+(?P<name>{_NAME})"),
)
# A bullet is a line under one employer, so any "at Globex" or "bij Globex" in
# it that is not that employer puts the line at another one.
_BULLET_PLACES = (*_PLACED_AT, re.compile(rf"\b(?i:at|bij)\s+(?P<name>{_NAME})"))
_NUMBER = re.compile(r"\d+")
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+")
_LETTERS = re.compile(r"[^\W\d_]+")


def _numbers(text: str) -> set[str]:
    """Return the numbers written in a text.

    Args:
        text: Any text.

    Returns:
        Each run of digits.
    """
    return set(_NUMBER.findall(text))


def _names(text: str) -> set[str]:
    """Return the words a text writes with a capital without opening a sentence.

    Args:
        text: A bullet.

    Returns:
        Those words, normalised: names of people, places, tools and firms.
    """
    names: set[str] = set()
    for sentence in _SENTENCE_BREAK.split(text):
        words = _LETTERS.findall(sentence)[1:]
        names.update(normalise_text(w) for w in words if len(w) > 1 and not w.islower())
    return names


def _entry_text(entry: ExperienceEntry) -> str:
    """Return everything an entry said before tailoring.

    Args:
        entry: The entry as it is in the original CV.

    Returns:
        Its title, organisation, period, description and bullets.
    """
    parts = [entry.title, entry.organisation, entry.period, entry.description]
    return "\n".join([*parts, *entry.bullets])


def _states(bullet: str, memory: str, entry_text: str) -> bool:
    """Tell whether a new bullet states a memory and adds nothing to it.

    Args:
        bullet: The new bullet.
        memory: The text of the memory it claims to state.
        entry_text: What the entry said before, from :func:`_entry_text`.

    Returns:
        True when the bullet shares enough of the memory's words and every
        number and name in it comes from the memory or the entry.
    """
    source = f"{memory}\n{entry_text}"
    if not _numbers(bullet) <= _numbers(source):
        return False
    if not _names(bullet) <= set(normalise_text(source).split()):
        return False
    anchors = significant_words(memory) | _numbers(memory)
    shared = (significant_words(bullet) | _numbers(bullet)) & anchors
    return bool(anchors) and len(shared) >= min(_MIN_SHARED, len(anchors))


def _placed_elsewhere(
    text: str, own: set[str], patterns: Sequence[re.Pattern[str]] = _PLACED_AT
) -> bool:
    """Tell whether a text puts the applicant's work at another organisation.

    Args:
        text: A memory's text or a new bullet.
        own: The significant words of the entry's organisation.
        patterns: The phrasings that place work at a named organisation.

    Returns:
        True when the text names such an organisation and it shares no word
        with the entry's own.
    """
    for pattern in patterns:
        for match in pattern.finditer(text):
            named = significant_words(match.group("name"))
            if named and not named & own:
                return True
    return False


def _organisations(doc: CVDocument) -> list[str]:
    """Return every employer and school the CV names.

    Args:
        doc: The original CV.

    Returns:
        The organisations of its experience entries and the schools of its
        education entries.
    """
    names: list[str] = []
    for section in doc.all_sections():
        if isinstance(section, ExperienceSection):
            names.extend(entry.organisation for entry in section.entries)
        elif isinstance(section, EducationSection):
            names.extend(study.school for study in section.entries)
    return [name for name in names if name.strip()]


def _assign(
    bullets: list[str], labels: list[str], backs: Callable[[str, str], bool]
) -> list[str] | None:
    """Give every new bullet a memory of its own.

    Args:
        bullets: The new bullets.
        labels: The memories that may back them.
        backs: Tells whether a bullet states a memory, given both.

    Returns:
        One label per bullet, in bullet order; None when some bullet cannot
        have a memory to itself.
    """
    options = [[lab for lab in labels if backs(bullet, lab)] for bullet in bullets]
    owner: dict[str, int] = {}

    def place(index: int, tried: set[str]) -> bool:
        for label in options[index]:
            if label in tried:
                continue
            tried.add(label)
            if label not in owner or place(owner[label], tried):
                owner[label] = index
                return True
        return False

    if all(place(index, set()) for index in range(len(bullets))):
        return sorted(owner, key=owner.__getitem__)
    return None


class MemoryBullets:
    """The memories of one tailoring, each handed to at most one new bullet."""

    def __init__(self, memories: Sequence[Mapping[str, Any]], doc: CVDocument) -> None:
        """Keep the memories that may back a bullet and the CV's organisations.

        Args:
            memories: The memories that were in the prompt.
            doc: The original CV.
        """
        self._texts: dict[str, str] = {}
        for memory in memories:
            label = parse_memory_label(memory.get("label"))
            if label and memory.get("kind") not in _WISHES:
                self._texts[label] = str(memory.get("text", ""))
        self._organisations = _organisations(doc)
        self._used: set[str] = set()

    def _belongs(self, memory: str, own: set[str]) -> bool:
        """Tell whether a memory may add a bullet under an entry.

        Args:
            memory: The memory's text.
            own: The significant words of the entry's organisation.

        Returns:
            False when it describes work at another organisation, or names
            another employer or school on the CV and not the entry's own.
        """
        if _placed_elsewhere(memory, own):
            return False
        words = significant_words(memory)
        if words & own:
            return True
        return not any(
            (significant_words(name) - own) & words for name in self._organisations
        )

    def back(
        self, entry: ExperienceEntry, new: list[str], cited: Sequence[str]
    ) -> list[str] | None:
        """Find the memory behind each new bullet of one entry.

        Args:
            entry: The entry as it is in the original CV.
            new: The bullets beyond the entry's own count, cleaned.
            cited: The labels the entry's patch names.

        Returns:
            One label per new bullet, now used up; None, using nothing up,
            when some new bullet has no memory of its own behind it or puts
            its work at another organisation.
        """
        own = significant_words(entry.organisation)
        if any(_placed_elsewhere(bullet, own, _BULLET_PLACES) for bullet in new):
            return None
        labels = [
            label
            for label in dict.fromkeys(cited)
            if label in self._texts
            and label not in self._used
            and self._belongs(self._texts[label], own)
        ]
        before = _entry_text(entry)

        def backs(bullet: str, label: str) -> bool:
            return _states(bullet, self._texts[label], before)

        assigned = _assign(new, labels, backs)
        if assigned is not None:
            self._used.update(assigned)
        return assigned
