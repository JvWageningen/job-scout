"""The user's letter style guide: derived from their own letters, stored as markdown.

The guide is the bridge between the user's past letters and every letter written
for them afterwards. It is derived once by the model from the example letters,
saved to ``data/users/<name>/letters/style.md`` where the user can edit it by hand,
and then handed verbatim to the letter writer as its instructions for voice.

Because it is handed over verbatim, it must teach style and nothing else. Any fact
that slips into the guide -- an employer, a project, a place -- would be obeyed as
an instruction and reappear in every future letter, so the derivation prompt spends
most of its effort keeping specifics out.
"""

from __future__ import annotations

import itertools
import re
import tempfile
from pathlib import Path

from loguru import logger

from job_scout.config import user_letters_dir
from job_scout.letters.models import ExampleLetter, LetterLanguage
from job_scout.llm.base import LLMClient, LLMError


class StyleError(RuntimeError):
    """Raised when a style guide cannot be derived or saved."""


STYLE_GUIDE_TITLE = "Letter style guide"

STYLE_GUIDE_HEADINGS: tuple[str, ...] = (
    "Voice and register",
    "Structure",
    "Dutch letters",
    "English letters",
    "Keep doing",
    "Improve",
    "Never",
)

# A single letter beyond this is truncated in the prompt: style shows in the first
# page, and one long document should not crowd out the others.
_MAX_EXAMPLE_WORDS = 700

_LANGUAGE_NAMES: dict[LetterLanguage, str] = {
    LetterLanguage.NL: "Dutch",
    LetterLanguage.EN: "English",
}

_WORD = re.compile(r"\S+")

_TITLE = re.compile(
    rf"^#[ \t]+{re.escape(STYLE_GUIDE_TITLE)}[ \t]*$", re.MULTILINE | re.IGNORECASE
)

# Greedy body, so a fence wrapping the whole guide is matched to its closing line.
_FENCED = re.compile(
    r"^[ \t]*```[\w+-]*[ \t]*\n(?P<body>.*)\n[ \t]*```[ \t]*$",
    re.MULTILINE | re.DOTALL,
)

_HEADINGS_BLOCK = "\n".join(
    [f"# {STYLE_GUIDE_TITLE}", *(f"## {heading}" for heading in STYLE_GUIDE_HEADINGS)]
)

_PROMPT = """\
You are writing a style guide for one job seeker's motivational letters, derived \
from letters they wrote themselves. The letters are at the end of this prompt.

HOW THE GUIDE IS USED
The guide is handed, word for word, to a writer (a language model) as its \
instructions every time it drafts a new letter for this person: in Dutch or \
English, to a different employer, for a different vacancy, with the facts taken \
from their CV. So the guide must tell that writer how this person writes, and \
nothing else.

WRITE INSTRUCTIONS, NOT AN ESSAY
- Address the writer directly, in the imperative: "Open with the role and your \
reason for applying.", "State gaps plainly, once, without apology.", "Keep \
paragraphs to three or four sentences." Do not describe, summarise or grade the \
letters.
- Ground every instruction in the letters. Prefer patterns that recur across \
several letters over something that happens once. Where it helps, quote a short \
phrase of a few words as evidence.
- Be concrete and give numbers: the target body length in words for each language, \
the number of paragraphs, typical paragraph and sentence length, and whether \
bulleted lists are used and for what.
- Be concise: short bullets, roughly 400 to 800 words in total.

WHAT EACH SECTION HOLDS
- Voice and register: formality, tone, sentence rhythm, how confident or modest \
the writer sounds, and above all how DIRECT they are. Say explicitly whether they \
state their motive for applying or for leaving, name gaps honestly, state \
conditions such as hours, contract or location, and say what they want to discuss \
in an interview.
- Structure: how the letters open, the order of the argument, how evidence is \
presented, and how they close.
- Dutch letters: what is specific to their Dutch letters -- salutation, closing, \
"u" or "je", length. Directness is a strength in a Dutch letter: tell the writer \
to preserve it, never to soften it.
- English letters: what is specific to their English letters -- salutation, \
closing, register, length, and what changes compared with their Dutch letters.
- Keep doing: genuine strengths that recur, so the writer keeps them.
- Improve: recurring weaknesses, each paired with the better alternative \
("Instead of ..., write ..."). The writer should produce the letter this person \
writes on their best day.
- Never: hard prohibitions. List the stock phrases this person has actually used, \
quoted so the writer can avoid them; flattery of the employer; and inventing any \
fact that is not in the CV, the vacancy or the user's notes.

LANGUAGES
{language_note}

STYLE ONLY -- NO FACTS (CRITICAL)
The guide must not contain a single fact a writer could copy into a letter to a \
different employer: no employer or organisation name, no project, product, team, \
person, place, date, qualification, figure from their career, or contact detail. \
Inside quoted phrases, replace every such specific with a bracketed placeholder \
such as [employer], [project], [product], [person], [place] or [date]: write \
"my work on [project] taught me", never the real project name.
Why this matters: the writer obeys the guide in every future letter. A guide that \
mentions a project makes every future letter mention that project, whatever the \
vacancy; a guide that names an employer puts that name into letters to other \
employers. Letterheads, addresses and signatures in the letters are not style: \
ignore them.

OUTPUT FORMAT
Return only the markdown guide: no preamble, no closing remarks, no code fence. \
Use exactly these headings, in this order, and add no other headings at these two \
levels. Put bulleted instructions under each heading.

{headings}

THE LETTERS
{letters}
"""


def style_guide_path(user: str) -> Path:
    """Return where a user's style guide is stored.

    Args:
        user: User name.

    Returns:
        Path to ``data/users/<user>/letters/style.md``.
    """
    return user_letters_dir(user) / "style.md"


def load_style_guide(user: str) -> str | None:
    """Load a user's style guide.

    Args:
        user: User name.

    Returns:
        The guide's markdown with surrounding whitespace removed, or None when the
        file does not exist or is blank.
    """
    path = style_guide_path(user)
    if not path.is_file():
        return None
    # utf-8-sig tolerates the byte-order mark some editors add on a hand edit.
    text = path.read_text(encoding="utf-8-sig").strip()
    return text or None


def save_style_guide(user: str, markdown: str) -> None:
    """Save a user's style guide, replacing any previous one.

    Args:
        user: User name.
        markdown: The guide's markdown.

    Raises:
        StyleError: If the guide is blank or cannot be written.
    """
    text = markdown.strip()
    if not text:
        raise StyleError("Refusing to save a blank style guide.")
    path = style_guide_path(user)
    temp: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            delete=False,
            suffix=".tmp",
        ) as stream:
            temp = Path(stream.name)
            stream.write(text + "\n")
        temp.replace(path)
    except OSError as exc:
        raise StyleError(f"Could not save the style guide: {exc}") from exc
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)
    logger.info("Saved letter style guide for user {} ({} chars)", user, len(text))


def derive_style_guide(examples: list[ExampleLetter], client: LLMClient) -> str:
    """Ask the model for a style guide drawn from the user's own letters.

    Args:
        examples: The user's past letters. Only their text, language and word count
            reach the prompt; file names are left out because they often name the
            employer.
        client: LLM client to call.

    Returns:
        The guide as markdown, unwrapped from any code fence and starting at its
        title.

    Raises:
        StyleError: If there are no examples, the model call fails, or the response
            lacks the title or any required heading.
    """
    if not examples:
        raise StyleError("Add at least one example letter before deriving a style.")
    prompt = _build_prompt(examples[:30])
    logger.info("Deriving letter style guide from {} example(s)", len(examples))
    try:
        raw = client.complete(prompt, purpose="cover_letter")
    except LLMError as exc:
        raise StyleError(f"The model could not derive a style guide: {exc}") from exc
    guide = _clean_response(raw)
    if not guide:
        raise StyleError("The model returned an empty style guide.")
    missing = _missing_headings(guide)
    if missing:
        names = ", ".join(f"'{name}'" for name in missing)
        raise StyleError(f"The style guide is missing required headings: {names}")
    return guide


def _build_prompt(examples: list[ExampleLetter]) -> str:
    """Assemble the derivation prompt.

    Args:
        examples: The user's past letters; must not be empty.

    Returns:
        The full prompt text.
    """
    letters = "\n\n".join(
        _format_example(number, example)
        for number, example in enumerate(examples, start=1)
    )
    return _PROMPT.format(
        language_note=_language_note(examples),
        headings=_HEADINGS_BLOCK,
        letters=letters,
    )


def _format_example(number: int, example: ExampleLetter) -> str:
    """Render one example letter for the prompt, truncated if very long.

    Args:
        number: 1-based position of the letter in the prompt.
        example: The letter.

    Returns:
        The letter wrapped in a labelled ``<letter>`` block.
    """
    language = _LANGUAGE_NAMES[example.language]
    body, truncated = _truncate_words(example.text, _MAX_EXAMPLE_WORDS)
    if truncated:
        body += (
            f"\n[... truncated after {_MAX_EXAMPLE_WORDS} of {example.words} words ...]"
        )
    return (
        f'<letter number="{number}" language="{language}" words="{example.words}">\n'
        f"{body}\n"
        "</letter>"
    )


def _truncate_words(text: str, limit: int) -> tuple[str, bool]:
    """Cut a text after a number of words, keeping its line breaks intact.

    Paragraph breaks are part of the style being learned, so the text is cut at a
    character offset rather than rejoined from a word list.

    Args:
        text: The text to cut.
        limit: Maximum number of words to keep.

    Returns:
        The possibly shortened text, and whether anything was cut.
    """
    words = list(itertools.islice(_WORD.finditer(text), limit + 1))
    if len(words) <= limit:
        return text.strip(), False
    return text[: words[limit - 1].end()].strip(), True


def _language_note(examples: list[ExampleLetter]) -> str:
    """Describe which languages the examples cover, with their word counts.

    Args:
        examples: The user's past letters; must not be empty.

    Returns:
        One line per language: its letter count and length range, or an
        instruction not to invent rules for a language with no letters.
    """
    lines = []
    for language, name in _LANGUAGE_NAMES.items():
        counts = [e.words for e in examples if e.language is language]
        if not counts:
            lines.append(
                f"- No {name} letters were provided. Under '## {name} letters', say "
                f"so, and tell the writer to apply the general sections to {name} "
                f"letters. Do not invent {name}-specific rules."
            )
            continue
        average = round(sum(counts) / len(counts))
        lines.append(
            f"- {len(counts)} {name} letter(s), {min(counts)} to {max(counts)} "
            f"words, average {average}. Base the {name} numbers on these."
        )
    return "\n".join(lines)


def _clean_response(raw: str) -> str:
    """Strip a surrounding code fence and any preamble before the guide's title.

    Args:
        raw: The model's response.

    Returns:
        The guide's markdown with surrounding whitespace removed.
    """
    text = raw.strip()
    fenced = _FENCED.search(text)
    if fenced and _TITLE.search(fenced.group("body")):
        text = fenced.group("body").strip()
    title = _TITLE.search(text)
    if title and title.start() > 0:
        logger.debug(
            "Dropping {} chars of preamble before the style guide", title.start()
        )
        text = text[title.start() :]
    return text.strip()


def _missing_headings(markdown: str) -> list[str]:
    """List the required headings a guide lacks.

    Args:
        markdown: The guide's markdown.

    Returns:
        The missing headings as they should appear, e.g. ``"## Never"``; empty when
        the guide is complete.
    """
    missing = [] if _TITLE.search(markdown) else [f"# {STYLE_GUIDE_TITLE}"]
    for heading in STYLE_GUIDE_HEADINGS:
        pattern = rf"^##[ \t]+{re.escape(heading)}[ \t]*$"
        if not re.search(pattern, markdown, re.MULTILINE | re.IGNORECASE):
            missing.append(f"## {heading}")
    return missing
