"""What every test of the house style wiring checks, in one place."""

from __future__ import annotations

import re

from job_scout.writing_style import HOUSE_STYLE

# What a model writes when nobody stops it: dashes joining clauses, a spaced
# hyphen doing the same job, and markdown bold.
GENERATED = "Ik werkte er \u2014 met plezier \u2014 drie jaar - en **graag** ook."
PLAIN = "Ik werkte er, met plezier, drie jaar, en graag ook."

_SPACED_HYPHEN = re.compile(r"(?<=\S) - (?=\S)")


def assert_plain(text: str) -> None:
    """Assert a generated text carries none of the marks the house style bans.

    Args:
        text: Text as a generator returned it.
    """
    assert "\u2014" not in text, text
    assert "\u2013" not in text, text
    assert "**" not in text, text
    assert not _SPACED_HYPHEN.search(text), text


def _clause_hyphens(text: str) -> list[str]:
    """Find spaced hyphens that join words rather than span a range of numbers.

    Args:
        text: Text to search.

    Returns:
        The surroundings of each one found.
    """
    found = []
    for match in _SPACED_HYPHEN.finditer(text):
        before, after = text[match.start() - 1], text[match.end()]
        if not (before.isdigit() and after.isdigit()):
            found.append(text[max(0, match.start() - 30) : match.end() + 30])
    return found


def assert_styled_prompt(prompt: str) -> None:
    """Assert a prompt carries the house style and none of the dashes it bans.

    A model copies the punctuation of its instructions, so a prompt that uses a
    dash invites one back. A spaced hyphen between two numbers is a range, as
    in a CV period the prompt quotes, and is allowed.

    Args:
        prompt: The prompt a generator sent to the model.
    """
    assert HOUSE_STYLE in prompt
    assert "\u2014" not in prompt
    assert "\u2013" not in prompt
    assert " -- " not in prompt
    assert _clause_hyphens(prompt) == []
