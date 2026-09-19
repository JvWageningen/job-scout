"""Checking what the applicant's memories add to a tailored CV.

CV tailoring (:mod:`job_scout.cv.tailor`) may give an experience entry one new
bullet for each memory it states. A model can name any memory it was given and
write whatever it likes under it, so every new bullet is held against the
memory behind it:

* The memory must belong to the entry. It may not put the applicant's work at
  another organisation ("I worked at Globex as Head of Data", "freelance work
  for Bakkerij Bol", "a consultant for Hooli"), it may not name another
  employer or school on the CV without naming this entry's own, and a memory
  dated only outside the entry's period does not belong to it.
* The bullet must say what the memory says and add nothing to it. Every
  number in it, in digits or in words of either language, comes from the
  memory; every name written with a capital comes from the memory or the
  entry; every year it dates falls within the entry's period; and it does not
  put its work at another organisation. It must also share at least two of
  the memory's significant words or numbers. A bullet that passes every other
  check but shares fewer, such as a translation, is put to the model as a
  judge (see :meth:`MemoryBullets.pending`), and only an explicit yes lets it
  through.
* A memory backs one new bullet in the whole CV, and a wish or a condition
  (kind preference or constraint) backs none.

The checks read words, not meaning. They cannot pass a number or a name the
memory does not hold, a year outside the entry's period, or a memory phrased
as work for another organisation. They do not catch a claim made in ordinary
lower-case words ("led a team", "company-wide"), and an undated memory that
names no organisation may go under any entry. When a check fails,
:mod:`job_scout.cv.tailor` keeps the entry's own bullets rather than failing
the whole CV.

While memories are in the prompt the same words also guard what tailoring
rewords (see :meth:`MemoryBullets.vouches` and
:meth:`MemoryBullets.vouches_for_text`): a description, a reworded bullet or
the profile text may not take up a memory about other work, put work at an
organisation the CV does not name, or bring in a number or a name that
neither the CV nor a fitting memory holds.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from job_scout.cv.models import (
    CVDocument,
    EducationSection,
    ExperienceEntry,
    ExperienceSection,
    ListSection,
    SkillsSection,
    TextSection,
)
from job_scout.memories import (
    OFF_CV_KINDS,
    normalise_text,
    parse_memory_label,
    significant_words,
    today_here,
)

# Wishes and conditions tell a letter what the applicant wants; they are never
# a line on a CV.
_WISHES = frozenset(kind.value for kind in OFF_CV_KINDS)
# Significant words or numbers a new bullet must share with its memory before
# code vouches for it without asking the model.
_MIN_SHARED = 2

# An organisation's name: capitalised words, such as "Globex Industries".
_NAME = r"[A-Z][\w&'.]*(?:[ \t]+[A-Z][\w&'.]*)*"
# Words that say the work was done for someone other than the employer: a
# client, a side job, volunteering. "for Finance" alone names a department.
_CUE = (
    r"(?i:freelance[rd]?|freelancing|consultant|consulting|contractor|contracted|"
    r"client|klant|opdracht|opdrachtgever|zzp\w*|interim|side project|bijproject|"
    r"volunteer\w*|vrijwillig\w*|bijbaan|internship|stage)"
)
_PREP = r"(?i:at|for|with|bij|voor)"
# Work for an organisation marked by such a word: "freelance work for Bakkerij
# Bol", "een opdracht voor Hooli", "for Hooli as a consultant".
_ENGAGED = (
    re.compile(rf"\b{_CUE}\b[^.;:]{{0,40}}?\b{_PREP}\s+(?P<name>{_NAME})"),
    re.compile(rf"\b{_PREP}\s+(?P<name>{_NAME})[^.;:]{{0,30}}?\b{_CUE}\b"),
)
# A memory that places the applicant at an organisation: "I worked at Globex",
# "werkte als analist bij Globex", "as Head of Data at Globex", and the work
# for a client above. The bare noun "work" is left out: "the reporting work
# for Finance" names a department.
_PLACED_AT = (
    re.compile(
        r"\b(?i:worked|working|works|employed|i work|werkte|werkten|werkt|ik werk|"
        r"werkzaam|gewerkt|in dienst)\b[^.;:]{0,40}?\b(?i:at|for|bij|voor)\s+"
        rf"(?P<name>{_NAME})"
    ),
    re.compile(rf"\b(?i:as|als)\s[^.;:]{{1,40}}?\s(?i:at|bij)\s+(?P<name>{_NAME})"),
    *_ENGAGED,
)
# A bullet is a line under one employer, so any "at Globex" or "bij Globex" in
# it that is not that employer puts the line at another one.
_BULLET_PLACES = (*_PLACED_AT, re.compile(rf"\b(?i:at|bij)\s+(?P<name>{_NAME})"))
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+")
_LETTERS = re.compile(r"[^\W\d_]+")
_DIGITS = re.compile(r"\d+")

# -- Numbers, in digits or in words -------------------------------------------

_NUMBER_VALUES = {
    "zero": 0,
    "nul": 0,
    "one": 1,
    "een": 1,
    "two": 2,
    "twee": 2,
    "three": 3,
    "drie": 3,
    "four": 4,
    "vier": 4,
    "five": 5,
    "vijf": 5,
    "six": 6,
    "zes": 6,
    "seven": 7,
    "zeven": 7,
    "eight": 8,
    "acht": 8,
    "nine": 9,
    "negen": 9,
    "ten": 10,
    "tien": 10,
    "eleven": 11,
    "elf": 11,
    "twelve": 12,
    "twaalf": 12,
    "thirteen": 13,
    "dertien": 13,
    "fourteen": 14,
    "veertien": 14,
    "fifteen": 15,
    "vijftien": 15,
    "sixteen": 16,
    "zestien": 16,
    "seventeen": 17,
    "zeventien": 17,
    "eighteen": 18,
    "achttien": 18,
    "nineteen": 19,
    "negentien": 19,
    "twenty": 20,
    "twintig": 20,
    "thirty": 30,
    "dertig": 30,
    "forty": 40,
    "veertig": 40,
    "fifty": 50,
    "vijftig": 50,
    "sixty": 60,
    "zestig": 60,
    "seventy": 70,
    "zeventig": 70,
    "eighty": 80,
    "tachtig": 80,
    "ninety": 90,
    "negentig": 90,
    "hundred": 100,
    "honderd": 100,
    "thousand": 1000,
    "duizend": 1000,
    "million": 10**6,
    "miljoen": 10**6,
    "billion": 10**9,
    "miljard": 10**9,
}
# "one" and "een" are also a pronoun and the Dutch article: on their own they
# are not read as a number, only inside a number such as "one hundred".
_ARTICLES = frozenset({"one", "een"})
# Dutch writes a number as one word: "vijfentwintig", "honderdtwintig".
_DUTCH_SCALES = (("miljoen", 10**6), ("duizend", 1000), ("honderd", 100))
_DUTCH_TENS = re.compile(
    r"(een|twee|drie|vier|vijf|zes|zeven|acht|negen)en"
    r"(twintig|dertig|veertig|vijftig|zestig|zeventig|tachtig|negentig)"
)
# Number words with no single value, compared as the same idea in either
# language: "halved" meets "gehalveerd", "twice" meets "verdubbeld".
_IDEA_WORDS = (
    ("half", "half halve helft halved halving halves gehalveerd halveerde halveren"),
    (
        "double",
        "double doubled doubling twice dubbel verdubbeld verdubbelde verdubbelen "
        "tweemaal",
    ),
    ("triple", "triple tripled verdrievoudigd verdrievoudigde"),
    ("third", "third thirds derde"),
    ("quarter", "quarter quarters kwart"),
    ("dozen", "dozen dozens dozijn"),
    ("hundreds", "hundreds honderden"),
    ("thousands", "thousands duizenden"),
    ("millions", "millions miljoenen"),
    ("first", "first eerste"),
    ("second", "second tweede"),
    ("fourth", "fourth vierde"),
    ("fifth", "fifth vijfde"),
)
_NUMBER_IDEAS = {word: idea for idea, words in _IDEA_WORDS for word in words.split()}


def _dutch_number(word: str) -> int | None:
    """Read a Dutch number written as one word.

    Args:
        word: A normalised word, such as "tweehonderdvijfentwintig".

    Returns:
        Its value, or None when the word is not a number.
    """
    if not word:
        return None
    for scale_word, scale in _DUTCH_SCALES:
        head, found, tail = word.partition(scale_word)
        if not found:
            continue
        times = _dutch_number(head) if head else 1
        rest = _dutch_number(tail) if tail else 0
        if times is None or rest is None:
            return None
        return times * scale + rest
    compound = _DUTCH_TENS.fullmatch(word)
    if compound:
        return _NUMBER_VALUES[compound.group(1)] + _NUMBER_VALUES[compound.group(2)]
    return _NUMBER_VALUES.get(word)


def _word_value(word: str) -> int | None:
    """Read one word as a number, in English or Dutch.

    Args:
        word: A normalised word.

    Returns:
        Its value, or None when the word is not a number.
    """
    if word in _NUMBER_VALUES:
        return _NUMBER_VALUES[word]
    return _dutch_number(word)


def _run_value(run: Sequence[tuple[str, int]]) -> str | None:
    """Add up number words in a row, such as "two hundred twenty five".

    Args:
        run: The words in a row with their values.

    Returns:
        The number in digits; None for "one" or "een" on its own.
    """
    if len(run) == 1 and run[0][0] in _ARTICLES:
        return None
    total, current = 0, 0
    for _, value in run:
        if value >= 1000:
            total += max(current, 1) * value
            current = 0
        elif value == 100:
            current = max(current, 1) * 100
        else:
            current += value
    return str(total + current)


_SCALES = frozenset({100, 1000, 10**6, 10**9})


def _joins(before: int, after: int) -> bool:
    """Tell whether two number words in a row make one number.

    Args:
        before: The value of the first word.
        after: The value of the word after it.

    Returns:
        True for "two hundred", "hundred twenty" and "twenty five"; False
        for two numbers that only stand next to each other.
    """
    if after in _SCALES or (before in _SCALES and after < before):
        return True
    return before in range(20, 100, 10) and 1 <= after <= 9


def _numbers(text: str) -> set[str]:
    """Return the numbers a text states, in digits or in words.

    "forty", "veertig" and "40" are the same number, "vijfentwintig" and
    "twenty five" are 25, and "halved" and "gehalveerd" the same idea.

    Args:
        text: Any text.

    Returns:
        Each number in digits, and the number ideas without one value.
    """
    found: set[str] = set()
    run: list[tuple[str, int]] = []
    for word in [*normalise_text(text).split(), ""]:
        value = _word_value(word)
        if value is not None and (not run or _joins(run[-1][1], value)):
            run.append((word, value))
            continue
        total = _run_value(run) if run else None
        if total is not None:
            found.add(total)
        run = [(word, value)] if value is not None else []
        if value is not None:
            continue
        found.update(str(int(digits)) for digits in _DIGITS.findall(word))
        if word in _NUMBER_IDEAS:
            found.add(_NUMBER_IDEAS[word])
    return found


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


# -- Dates --------------------------------------------------------------------

_YEAR = re.compile(r"(?<!\d)(19[5-9]\d|20\d\d)(?!\d)")
_MONTH_WORDS = (
    "jan|january|januari|feb|february|februari|mar|march|maart|apr|april|may|mei|"
    "jun|june|juni|jul|july|juli|aug|august|augustus|sep|sept|september|oct|"
    "october|okt|oktober|nov|november|dec|december"
)
# A year is a date, not a count ("2000 customers"), after one of these words
# or a month, or next to a dash or a slash.
_BEFORE_DATE = re.compile(
    r"(?i)(?:\b(?:in|since|sinds|from|van|vanaf|until|tot|to|per|during|tijdens|"
    rf"{_MONTH_WORDS})\s+|[-/\u2013\u2014]\s*)$"
)
_AFTER_DATE = re.compile(r"\s*[-/\u2013\u2014]")
# A period that runs to now: "2021 - present", "sinds 2020", "2019 - heden".
_OPEN_PERIOD = re.compile(
    r"(?i)\b(?:present|now|current|currently|today|ongoing|since|heden|nu|"
    r"huidig|lopend|sinds)\b|[-\u2013\u2014]\s*$"
)


def _dated_years(text: str) -> set[int]:
    """Return the years a text gives as dates.

    Args:
        text: A memory or a bullet.

    Returns:
        Each year written as a date: after "in", "since", "van" and the like
        or a month, or next to a dash or a slash.
    """
    years: set[int] = set()
    for match in _YEAR.finditer(text):
        before, after = text[: match.start()], text[match.end() :]
        if _BEFORE_DATE.search(before) or _AFTER_DATE.match(after):
            years.add(int(match.group(1)))
    return years


def _span(period: str) -> tuple[int, int] | None:
    """Read the years an entry's period covers.

    Args:
        period: Such as "2019 - 2025", "sinds 2020" or "jan 2021 - heden".

    Returns:
        The first and last year, the last being this year for a period that
        runs to now; None when the period holds no year.
    """
    years = [int(year) for year in _YEAR.findall(period)]
    if not years:
        return None
    start, end = min(years), max(years)
    if _OPEN_PERIOD.search(period.strip()):
        end = max(end, today_here().year)
    return start, end


def _outside(years: Iterable[int], span: tuple[int, int] | None) -> list[int]:
    """Return the years that fall outside a period.

    Args:
        years: Dated years.
        span: The period, or None when it is not known.

    Returns:
        The years before its start or after its end; none for an unknown
        period.
    """
    if span is None:
        return []
    return [year for year in years if not span[0] <= year <= span[1]]


# -- What a text says -----------------------------------------------------------


def _entry_text(entry: ExperienceEntry) -> str:
    """Return everything an entry said before tailoring.

    Args:
        entry: The entry as it is in the original CV.

    Returns:
        Its title, organisation, period, description and bullets.
    """
    parts = [entry.title, entry.organisation, entry.period, entry.description]
    return "\n".join([*parts, *entry.bullets])


def _document_text(doc: CVDocument) -> str:
    """Return the words of a CV that describe the applicant.

    Details and contact sections are left out: a date of birth or a phone
    number is no evidence for a profile text.

    Args:
        doc: The original CV.

    Returns:
        The text of its prose, entries, skills and lists.
    """
    parts: list[str] = [doc.headline]
    for section in doc.all_sections():
        if isinstance(section, TextSection):
            parts.append(section.body)
        elif isinstance(section, ExperienceSection):
            parts.extend(_entry_text(entry) for entry in section.entries)
        elif isinstance(section, EducationSection):
            parts.extend(
                f"{s.degree}\n{s.school}\n{s.period}\n{s.courses}\n{s.note}"
                for s in section.entries
            )
        elif isinstance(section, SkillsSection):
            parts.extend(item.name for item in section.items)
        elif isinstance(section, ListSection):
            parts.extend(section.items)
    return "\n".join(parts)


def _floor(bullet: str, memory: str, entry: ExperienceEntry) -> bool:
    """Tell whether a new bullet adds no number or name to its memory.

    Args:
        bullet: The new bullet.
        memory: The text of the memory it claims to state.
        entry: The entry as it is in the original CV.

    Returns:
        True when every number in the bullet is in the memory (or in the
        entry's own title or organisation, as in "Studio 54") and every name
        written with a capital is in the memory or the entry. A year from
        the entry's period is not enough: it may not replace the memory's.
    """
    allowed = _numbers(memory) | _numbers(f"{entry.title}\n{entry.organisation}")
    if not _numbers(bullet) <= allowed:
        return False
    source = f"{memory}\n{_entry_text(entry)}"
    return _names(bullet) <= set(normalise_text(source).split())


def _overlaps(bullet: str, memory: str) -> bool:
    """Tell whether a bullet shares enough of a memory's words to state it.

    Args:
        bullet: The new bullet.
        memory: The text of the memory it claims to state.

    Returns:
        True when it shares at least two of the memory's significant words
        or numbers (all of them, when the memory has fewer).
    """
    anchors = significant_words(memory) | _numbers(memory)
    shared = (significant_words(bullet) | _numbers(bullet)) & anchors
    return bool(anchors) and len(shared) >= min(_MIN_SHARED, len(anchors))


def _placed_elsewhere(
    text: str, own: set[str], patterns: Sequence[re.Pattern[str]] = _PLACED_AT
) -> bool:
    """Tell whether a text puts the applicant's work at another organisation.

    Work for a client counts only when the text does not name the entry's
    own organisation: a consultant's "opdracht voor Ahold" is still work for
    their employer when it says so.

    Args:
        text: A memory's text or a new bullet.
        own: The significant words of the entry's organisation.
        patterns: The phrasings that place work at a named organisation.

    Returns:
        True when the text names such an organisation and it shares no word
        with the entry's own.
    """
    names_own = bool(significant_words(text) & own)
    for pattern in patterns:
        if names_own and pattern in _ENGAGED:
            continue
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


@dataclass(frozen=True)
class Pair:
    """A new bullet and a memory that code cannot vouch for by its words.

    Attributes:
        bullet: The new bullet, cleaned.
        label: The memory's label, such as "memory 3".
        memory: The memory's text.
    """

    bullet: str
    label: str
    memory: str


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
        self._cv_text = _document_text(doc)
        self._used: set[str] = set()
        self._judged: set[tuple[str, str]] = set()

    @property
    def labels(self) -> list[str]:
        """The labels of the memories that may back a bullet."""
        return list(self._texts)

    def _belongs(
        self, memory: str, own: set[str], span: tuple[int, int] | None
    ) -> bool:
        """Tell whether a memory may add a bullet under an entry.

        Args:
            memory: The memory's text.
            own: The significant words of the entry's organisation.
            span: The years of the entry's period, if known.

        Returns:
            False when it describes work at another organisation, names
            another employer or school on the CV and not the entry's own, or
            dates itself only outside the entry's period.
        """
        if _placed_elsewhere(memory, own):
            return False
        years = _dated_years(memory)
        if years and len(_outside(years, span)) == len(years):
            return False
        words = significant_words(memory)
        if words & own:
            return True
        return not any(
            (significant_words(name) - own) & words for name in self._organisations
        )

    def _usable(self, entry: ExperienceEntry, cited: Sequence[str]) -> list[str]:
        """Return the cited memories still free that belong to an entry.

        Args:
            entry: The entry as it is in the original CV.
            cited: The labels that may back its new bullets.

        Returns:
            Those labels, each once, in the order given.
        """
        own, span = significant_words(entry.organisation), _span(entry.period)
        return [
            label
            for label in dict.fromkeys(cited)
            if label in self._texts
            and label not in self._used
            and self._belongs(self._texts[label], own, span)
        ]

    @staticmethod
    def _refused(entry: ExperienceEntry, new: Sequence[str]) -> bool:
        """Tell whether a new bullet is out of place under an entry whatever backs it.

        Args:
            entry: The entry as it is in the original CV.
            new: The new bullets.

        Returns:
            True when one puts its work at another organisation or dates it
            outside the entry's period.
        """
        own, span = significant_words(entry.organisation), _span(entry.period)
        return any(
            _placed_elsewhere(bullet, own, _BULLET_PLACES)
            or _outside(_dated_years(bullet), span)
            for bullet in new
        )

    def pending(
        self, entry: ExperienceEntry, new: list[str], cited: Sequence[str]
    ) -> list[Pair]:
        """Find the new bullets only the model can tell a memory states.

        A bullet that passes every check but shares too few of a memory's
        words, as a Dutch memory written as an English bullet does, is not
        refused outright: the caller asks the model and passes the pairs it
        confirms to :meth:`accept`.

        Args:
            entry: The entry as it is in the original CV.
            new: The bullets beyond the entry's own count, cleaned.
            cited: The labels that may back them.

        Returns:
            The pairs to judge; none when the bullets are out of place.
        """
        if not new or self._refused(entry, new):
            return []
        pairs: list[Pair] = []
        for label in self._usable(entry, cited):
            memory = self._texts[label]
            pairs.extend(
                Pair(bullet=bullet, label=label, memory=memory)
                for bullet in new
                if _floor(bullet, memory, entry) and not _overlaps(bullet, memory)
            )
        return pairs

    def accept(self, pairs: Iterable[Pair]) -> None:
        """Record the pairs the model confirmed: the bullet states the memory.

        Args:
            pairs: Pairs from :meth:`pending` that were judged to hold.
        """
        self._judged.update((pair.bullet, pair.label) for pair in pairs)

    def back(
        self, entry: ExperienceEntry, new: list[str], cited: Sequence[str]
    ) -> list[str] | None:
        """Find the memory behind each new bullet of one entry.

        Args:
            entry: The entry as it is in the original CV.
            new: The bullets beyond the entry's own count, cleaned.
            cited: The labels that may back them: the ones the entry's patch
                names, or every label when it names none.

        Returns:
            One label per new bullet, now used up; None, using nothing up,
            when some new bullet has no memory of its own behind it, puts
            its work at another organisation or dates it outside the entry.
        """
        if self._refused(entry, new):
            return None

        def backs(bullet: str, label: str) -> bool:
            memory = self._texts[label]
            if not _floor(bullet, memory, entry):
                return False
            return _overlaps(bullet, memory) or (bullet, label) in self._judged

        assigned = _assign(new, self._usable(entry, cited), backs)
        if assigned is not None:
            self._used.update(assigned)
        return assigned

    def _fitting(self, entry: ExperienceEntry) -> list[str]:
        """Return the texts of every memory that belongs to an entry.

        Args:
            entry: The entry as it is in the original CV.

        Returns:
            The texts, used or not.
        """
        own, span = significant_words(entry.organisation), _span(entry.period)
        return [text for text in self._texts.values() if self._belongs(text, own, span)]

    def vouches(self, entry: ExperienceEntry, text: str, cv_extra: str) -> bool:
        """Tell whether a reworded description or bullet may stand under an entry.

        Args:
            entry: The entry as it is in the original CV.
            text: The reworded description or bullet, cleaned.
            cv_extra: More of the CV the text may draw on, such as its skills.

        Returns:
            False when it puts work at another organisation, takes up the
            words of a memory that belongs to other work, or holds a number
            or a name that neither the entry, ``cv_extra`` nor a memory that
            belongs to the entry holds.
        """
        own, span = significant_words(entry.organisation), _span(entry.period)
        if _placed_elsewhere(text, own, _BULLET_PLACES):
            return False
        before = _entry_text(entry)
        added = significant_words(text) - significant_words(before)
        for memory in self._texts.values():
            if self._belongs(memory, own, span):
                continue
            if len(added & significant_words(memory)) >= _MIN_SHARED:
                return False
        source = "\n".join([before, cv_extra, *self._fitting(entry)])
        return _numbers(text) <= _numbers(source) and _names(text) <= set(
            normalise_text(source).split()
        )

    def vouches_for_text(self, text: str) -> bool:
        """Tell whether a reworded text section, such as the profile, may stand.

        Args:
            text: The reworded body, cleaned.

        Returns:
            False when it puts work at an organisation the CV does not name,
            or holds a number or a name that neither the CV nor a memory that
            is not a wish holds.
        """
        own: set[str] = set()
        for name in self._organisations:
            own |= significant_words(name)
        if _placed_elsewhere(text, own, _BULLET_PLACES):
            return False
        source = "\n".join([self._cv_text, *self._texts.values()])
        return _numbers(text) <= _numbers(source) and _names(text) <= set(
            normalise_text(source).split()
        )
