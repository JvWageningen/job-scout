"""Put generated prose in the house style without changing what it says.

:func:`job_scout.writing_style.humanise` is the clean-up floor, and it reads a
dash as a range only when a digit sits on both sides. Generated prose holds
ranges it cannot see: a salary written as "\u20ac 3.500 \u2013 \u20ac 4.800" or
"42k\u201355k", and a period that ends in a month or a word, such as
"jan 2019 \u2013 dec 2021" or "2019 \u2013 heden". Left to humanise, each one
becomes two items in a comma list and the range is lost. Some text is not the
model's to restyle at all: an e-mail address or a link, and, in a review or a
style guide, a phrase quoted from the applicant's own document.

:func:`clean_prose` wraps humanise with exactly that care: it turns those
ranges into the plain hyphen the house style asks for, and leaves e-mail
addresses, links and (on request) quoted text exactly as the model wrote them.
Every generator that restyles prose calls this rather than humanise directly.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from job_scout.writing_style import humanise

# A month name, Dutch or English, full or abbreviated, followed by a year.
_MONTH_YEAR = (
    r"(?:jan|feb|mrt|maa?rt|mar|apr|mei|may|jun|jul|aug|sep|okt|oct|nov|dec)"
    r"[a-z]*\.?[ \t]+\d{4}"
)
# What may open the far end of a range: an amount, a month and year, or the
# word a CV uses for an open-ended period.
_RANGE_END = (
    r"(?:[\u20ac$\u00a3]|eur\b)[ \t]?\d|\d"
    r"|(?:heden|huidig|nu|now|present|current|today|vandaag)\b|" + _MONTH_YEAR
)
# A dash (em, en, a hyphen or the double hyphen of plain text, spaced or not)
# after a number, an amount with a thousands, millions or EUR suffix, or a
# year. Only spaces and tabs, so a range never swallows the line break before
# a bullet.
_RANGE = re.compile(
    r"(\d(?:[ \t]?(?:k|m|mln|mio|miljoen|million|bn|mrd|eur|euro)\b)?)"
    r"[ \t]*(?:[\u2013\u2014]|-{1,3})[ \t]*(?=" + _RANGE_END + ")",
    re.IGNORECASE,
)

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_LINK = re.compile(r"(?:https?://|www\.)[^\s<>\"]+", re.IGNORECASE)
# Text quoted from somewhere else: straight, curly or low double quotes, a
# curly single quote, backticks, or a single dash shown in single quotes or
# brackets, the way a style guide shows the mark it forbids.
_QUOTED = (
    re.compile(r"\"[^\"\n]*\""),
    re.compile(r"\u201c[^\u201d\n]*\u201d"),
    re.compile(r"\u201e[^\u201c\u201d\n]*[\u201c\u201d]"),
    re.compile(r"\u2018[^\u2019\n]*\u2019"),
    re.compile(r"`[^`\n]*`"),
    re.compile(r"'[ \t]*[\u2013\u2014-][ \t]*'"),
    re.compile(r"\([ \t]*[\u2013\u2014-][ \t]*\)"),
)
# A list marker opening a line: a dash, an asterisk, a bullet sign, or a number
# of one or two digits followed by a full stop or a bracket. A digit counts only
# then, so a line that opens with a year or an amount is left alone.
_LIST_MARKER = re.compile(
    r"^[ \t]*(?:[-*\u2022\u2023\u25aa\u25e6\u2043]|\d{1,2}[.)])[ \t]+"
)
# A blank line between paragraphs, kept as it was when lists are flattened.
_PARAGRAPH_BREAK = re.compile(r"(\n[ \t]*\n)")
_SENTENCE_END = (".", "!", "?")
_CLAUSE_END = (":", ",", ";")
# Each kept span is replaced by one character from the private use area, which
# no rule in humanise matches, and put back afterwards.
_FIRST_PLACEHOLDER = 0xE000
_LAST_PLACEHOLDER = 0xF8FF


def normalise_ranges(text: str) -> str:
    """Write every range as the plain hyphen the house style allows.

    Args:
        text: Generated text.

    Returns:
        The text with a dash between two amounts, or after a year that opens a
        period, turned into an unspaced hyphen: "\u20ac 3.500-\u20ac 4.800",
        "42k-55k", "jan 2019-dec 2021", "2019-heden".
    """
    return _RANGE.sub(r"\1-", text)


def _capitalised(text: str) -> str:
    """Return *text* with its first letter in upper case.

    Args:
        text: A list item about to start a sentence.

    Returns:
        The item as a sentence opening.
    """
    return text[:1].upper() + text[1:]


def _closed(text: str) -> str:
    """End a sentence built from a list item with a full stop if it has none.

    Args:
        text: The line the last item was joined onto.

    Returns:
        The line ending in sentence punctuation.
    """
    if text.endswith(_SENTENCE_END):
        return text
    return text.rstrip(",;") + "."


def _joined(before: str, item: str) -> str:
    """Join one list item onto the line before it as running text.

    Args:
        before: The line so far, e.g. "Two things:" or an earlier item.
        item: The item without its marker.

    Returns:
        One line: after a colon, comma or semicolon the item simply follows;
        otherwise it becomes a sentence of its own.
    """
    if before.endswith(_CLAUSE_END):
        return f"{before} {item}"
    return f"{_closed(before)} {_capitalised(item)}"


def _flatten_paragraph(paragraph: str) -> str:
    """Turn the list lines of one paragraph into running sentences.

    Args:
        paragraph: Text without blank lines.

    Returns:
        The paragraph with every marked line joined onto the line before it.
        A single marked line on its own only loses its marker, so a label
        such as a source name does not gain a full stop.
    """
    lines: list[str] = []
    ends_in_item = joined = False
    for line in paragraph.split("\n"):
        marker = _LIST_MARKER.match(line)
        if marker is None:
            lines.append(line)
            ends_in_item = False
            continue
        item = line[marker.end() :].strip()
        if not item:
            continue
        if lines and lines[-1].strip():
            lines[-1] = _joined(lines[-1].rstrip(), item)
            joined = True
        else:
            lines.append(_capitalised(item))
        ends_in_item = True
    if ends_in_item and joined:
        lines[-1] = _closed(lines[-1])
    return "\n".join(lines)


def lists_to_sentences(text: str) -> str:
    """Write a list as the sentences a person would say instead.

    For text that is never meant to hold a list, such as a spoken interview
    answer or a question: the markers go ("-", "*", a bullet sign, "1." or
    "2)") and each item is joined onto the line before it, so "Two things:"
    followed by two items reads "Two things: I planned the audits. I wrote the
    procedure." Blank lines between paragraphs stay, and a line that opens
    with a year or an amount is not a list item.

    Args:
        text: Generated text.

    Returns:
        The text without list markers.
    """
    parts = _PARAGRAPH_BREAK.split(text)
    return "".join(
        part if index % 2 else _flatten_paragraph(part)
        for index, part in enumerate(parts)
    )


def clean_prose(
    text: str, *, keep_quotes: bool = False, flatten_lists: bool = False
) -> str:
    """Put generated prose in the house style, keeping what it must not change.

    Args:
        text: Prose as a model wrote it.
        keep_quotes: Leave quoted text exactly as written. Use it where the
            model quotes the applicant's own document, as a review or a
            learned style guide does.
        flatten_lists: Turn list lines into sentences (see
            :func:`lists_to_sentences`). Use it for text that is never a
            list, such as an interview question or a spoken answer; a letter
            may hold a list on purpose and keeps its markers.

    Returns:
        The prose cleaned by :func:`humanise`, with ranges written as plain
        hyphens and e-mail addresses, links and, when asked, quotations kept
        as they were.
    """
    if not text:
        return text
    patterns = (_EMAIL, _LINK, *(_QUOTED if keep_quotes else ()))
    masked, kept = _mask(text, patterns)
    if flatten_lists:
        masked = lists_to_sentences(masked)
    return _unmask(humanise(normalise_ranges(masked)), kept)


def clean_items(
    items: object, fields: Sequence[str], *, flatten_lists: bool = False
) -> None:
    """Clean the named prose fields of every object in a parsed JSON list.

    Meant for a model response that is parsed but not yet validated, so any
    length limit the validation applies holds for the cleaned text. Anything
    that is not a list of objects with string fields is left for the
    validation to reject.

    Args:
        items: A parsed JSON value, normally a list of objects.
        fields: The keys whose string values are prose.
        flatten_lists: Turn list lines into sentences, for fields that are
            never meant to hold a list.
    """
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        for field in fields:
            if isinstance(item.get(field), str):
                item[field] = clean_prose(item[field], flatten_lists=flatten_lists)


def _mask(text: str, patterns: Sequence[re.Pattern[str]]) -> tuple[str, list[str]]:
    """Swap every match of the patterns for a placeholder humanise ignores.

    Args:
        text: Text to mask.
        patterns: What to keep, applied in order.

    Returns:
        The masked text and the kept spans, indexed by placeholder.
    """
    kept: list[str] = []

    def hide(match: re.Match[str]) -> str:
        if _FIRST_PLACEHOLDER + len(kept) > _LAST_PLACEHOLDER:
            return match.group(0)
        kept.append(match.group(0))
        return chr(_FIRST_PLACEHOLDER + len(kept) - 1)

    for pattern in patterns:
        text = pattern.sub(hide, text)
    return text, kept


def _unmask(text: str, kept: list[str]) -> str:
    """Put the kept spans back, latest first so nested ones unfold.

    Args:
        text: Masked, cleaned text.
        kept: The spans :func:`_mask` took out.

    Returns:
        The text with every placeholder replaced by its original span.
    """
    for index in range(len(kept) - 1, -1, -1):
        text = text.replace(chr(_FIRST_PLACEHOLDER + index), kept[index])
    return text
