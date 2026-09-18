"""The house style every generated text follows, and a floor under it.

Everything job-scout writes for the applicant to read or send (letters, interview
questions and answers, reviews, feedback, CV wording) used to read as machine
written: dashes joining every other clause, bullet lists where a sentence would
do, stock words such as "passionate" or "naadloos", and a uniform polish no
person produces on a working day. An employer who notices that reads the whole
application differently.

Two layers. :data:`HOUSE_STYLE` goes into every prompt that produces prose, so
the model writes plainly in the first place. :func:`humanise` then cleans what
a model does anyway and a rule can repair safely: dashes, markdown emphasis,
emoji. :func:`ai_tells` finds the stock phrases a rule cannot rewrite, so the
applicant can be told where to look.

The prompts themselves matter as much as the rules: a model copies the
punctuation of the instructions it is given, so prompt text must not use the
dashes it forbids.
"""

from __future__ import annotations

import re

HOUSE_STYLE = (
    "HOUSE STYLE. This applies to every sentence you write for the applicant and "
    "overrides your own habits. Write like a capable person writing plainly on a "
    "normal working day, not like a polished assistant.\n"
    "1. Punctuation: never use an em dash or an en dash, and never a spaced "
    "hyphen to join two parts of a sentence. Use a full stop, a comma, a colon or "
    "brackets instead. A hyphen belongs only inside a word (e-commerce, "
    "B2B-klanten) or in a range of numbers (2019-2021).\n"
    "2. No formatting. No bullet points or numbered lists unless the output format "
    "itself is a list, no bold, no italics, no headings, no emoji and no "
    "exclamation marks. Write sentences.\n"
    "3. Plain words. Do not use: passionate, excited, thrilled, eager, delve, "
    "leverage, navigate, landscape, tapestry, robust, seamless, cutting-edge, "
    "dynamic, synergy, holistic, pivotal, crucial, vital, foster, elevate, "
    "unlock, empower, journey, testament, 'fast-paced', 'I am confident that', "
    "'furthermore', 'moreover', 'additionally', 'not only ... but also'. In "
    "Dutch do not use: passie, gepassioneerd, 'met veel enthousiasme', "
    "uitdagend or uitdaging as praise, dynamisch, naadloos, cruciaal, "
    "essentieel, 'van onschatbare waarde', synergie, holistisch, 'in de snel "
    "veranderende wereld', 'graag wil ik', and no 'Daarnaast', 'Bovendien', "
    "'Kortom' or 'Al met al' at the start of a sentence.\n"
    "4. Do not polish everything to the same shine. Mix short and long "
    "sentences. Say a thing once. No groups of three adjectives or three "
    "parallel clauses, no rhetorical questions, no closing sentence that sums up "
    "what was just said, and no compliments to the reader or the company.\n"
    "5. Be specific rather than impressive: a number, a tool, a place or a "
    "result beats an adjective. When there is nothing specific to say, write "
    "less.\n"
    "6. Keep the applicant's register. Dutch is direct and plain, as Dutch "
    "people write to each other at work. English is plain international "
    "business English without sales language.\n"
)

# Phrases a reader recognises as generated. Matched case-insensitively on word
# boundaries; each entry is what the applicant is shown, so keep them readable.
_TELLS_EN = (
    "passionate",
    "excited to",
    "thrilled",
    "eager to",
    "delve",
    "leverage",
    "navigate the",
    "landscape",
    "tapestry",
    "robust",
    "seamless",
    "seamlessly",
    "cutting-edge",
    "dynamic environment",
    "synergy",
    "holistic",
    "pivotal",
    "foster",
    "elevate",
    "unlock",
    "empower",
    "testament",
    "fast-paced",
    "I am confident that",
    "furthermore",
    "moreover",
    "not only",
)
_TELLS_NL = (
    "passie",
    "gepassioneerd",
    "met veel enthousiasme",
    "uitdagende",
    "dynamische",
    "naadloos",
    "naadloze",
    "cruciaal",
    "cruciale",
    "van onschatbare waarde",
    "synergie",
    "holistisch",
    "snel veranderende",
    "graag wil ik",
    "kortom",
    "al met al",
)
_LONGEST_FIRST = sorted(_TELLS_EN + _TELLS_NL, key=len, reverse=True)
_TELL_PATTERN = re.compile(
    r"(?<![\w-])(?:" + "|".join(re.escape(t) for t in _LONGEST_FIRST) + r")(?![\w-])",
    re.IGNORECASE,
)

_EM_OR_EN = "\u2014\u2013"
# A dash between two digits is a range, whatever dash the model chose.
_DIGIT_RANGE = re.compile(rf"(\d)\s*[{_EM_OR_EN}]\s*(\d)")
_DIGIT_SPACED_HYPHEN = re.compile(r"(\d) - (\d)")
# A dash between words joins two parts of a sentence; a comma does that job.
_CLAUSE_DASH = re.compile(rf"\s*[{_EM_OR_EN}]\s*")
_SPACED_HYPHEN = re.compile(r"(?<=\S) - (?=\S)")
_BOLD = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1", re.DOTALL)
_ITALIC = re.compile(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])")
_EMOJI = re.compile(
    "["
    "\U0001f000-\U0001faff"  # pictographs, emoticons, symbols and flags
    "\u2600-\u27bf"  # miscellaneous symbols and dingbats
    "\ufe0f"  # emoji presentation selector
    "]"
)
_SPACE_BEFORE_PUNCT = re.compile(r"[ \t]+([,.;:!?)])")
_DOUBLE_COMMA = re.compile(r",(\s*,)+")
_COMMA_STOP = re.compile(r",\s*([.;:!?])")
_MULTI_SPACE = re.compile(r"[ \t]{2,}")


def humanise(text: str) -> str:
    """Remove the marks of generated text that a rule can repair safely.

    Dashes become the punctuation a person would type: a range keeps a plain
    hyphen, a dash joining two parts of a sentence becomes a comma. Markdown
    emphasis loses its asterisks, emoji disappear, and the typographic
    ellipsis becomes three full stops. Line breaks and a leading "- " bullet
    are kept, because a letter paragraph may deliberately hold a list.

    Args:
        text: Generated text.

    Returns:
        The same text in plain punctuation.
    """
    if not text:
        return text
    lines = [_humanise_line(line) for line in text.split("\n")]
    return "\n".join(lines)


def _humanise_line(line: str) -> str:
    """Clean one line, leaving a leading bullet marker alone.

    Args:
        line: One line of generated text.

    Returns:
        The cleaned line.
    """
    bullet = re.match(r"^(\s*[-*]\s+)", line)
    prefix = "- " if bullet else ""
    body = line[bullet.end() :] if bullet else line
    body = _DIGIT_RANGE.sub(r"\1-\2", body)
    body = _DIGIT_SPACED_HYPHEN.sub(r"\1-\2", body)
    body = _CLAUSE_DASH.sub(", ", body)
    body = _SPACED_HYPHEN.sub(", ", body)
    body = _BOLD.sub(r"\2", body)
    body = _ITALIC.sub(r"\1", body)
    body = _EMOJI.sub("", body)
    body = body.replace("\u2026", "...")
    body = _tidy(body)
    return prefix + body if body else body.rstrip()


def _tidy(text: str) -> str:
    """Repair the punctuation the replacements can leave behind.

    Args:
        text: A cleaned line.

    Returns:
        The line without doubled commas, a comma before a full stop, a space
        before punctuation, doubled spaces or a leading comma.
    """
    text = _DOUBLE_COMMA.sub(",", text)
    text = _COMMA_STOP.sub(r"\1", text)
    text = _SPACE_BEFORE_PUNCT.sub(r"\1", text)
    text = _MULTI_SPACE.sub(" ", text)
    return text.strip().lstrip(",").strip()


def ai_tells(text: str) -> list[str]:
    """Find the stock phrases that make a text read as generated.

    A rule cannot rewrite "passionate" into what the applicant actually means,
    so these are reported rather than replaced.

    Args:
        text: Generated text.

    Returns:
        Each phrase found, lower-cased, in order of first appearance.
    """
    found: list[str] = []
    for match in _TELL_PATTERN.finditer(text or ""):
        phrase = match.group(0).lower()
        if phrase not in found:
            found.append(phrase)
    return found
