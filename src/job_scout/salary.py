"""Deterministic salary extraction from job-description text.

The LLM evaluator only sees a truncated slice of each description and does not
always parse the "wat bieden wij" salary line (which in Dutch listings usually
appears late in the text). This module scans the *full* description with a
conservative regex as a backstop, so a clearly-stated below-minimum salary is
still caught even when the model misses it.

It is intentionally conservative: amounts are only read near salary keywords and
must fall in a plausible range, so unrelated figures (holiday allowance, budgets)
are not mistaken for pay.
"""

from __future__ import annotations

import re

# Keywords that introduce a salary figure in Dutch/English listings.
_SALARY_CONTEXT = re.compile(
    r"(salaris|maandsalaris|bruto|verdien|beloning|salary|"
    r"per\s+maand|per\s+jaar|o\.?b\.?v\.?\s+40\s+uur)",
    re.IGNORECASE,
)
# A EUR amount, e.g. "€ 3.255,55", "€3500", "3.500 euro", "4160".
_AMOUNT = re.compile(
    r"(?:€|eur)\s*(\d[\d.]*(?:,\d+)?)|(\d[\d.]*)\s*(?:euro|eur)\b",
    re.IGNORECASE,
)
_YEARLY = re.compile(
    r"per\s+jaar|jaarsalaris|jaarbasis|per\s+annum|/\s*jaar", re.IGNORECASE
)

_MONTHLY_MIN = 1200
_MONTHLY_MAX = 20000
_CONTEXT_WINDOW = 140


def _parse_amount(raw: str) -> int | None:
    """Parse a Dutch-formatted EUR amount string into an integer.

    Handles ``.`` as a thousands separator and ``,`` as a decimal comma, e.g.
    ``"3.255,55"`` -> ``3255`` and ``"3500"`` -> ``3500``.

    Args:
        raw: The captured amount string.

    Returns:
        The integer euro amount, or None if it cannot be parsed.
    """
    integer_part = raw.split(",")[0].replace(".", "").strip()
    if not integer_part.isdigit():
        return None
    return int(integer_part)


def _amounts_in_window(text: str, keyword_end: int) -> list[int]:
    """Return plausible monthly EUR amounts near a salary keyword.

    Args:
        text: The full (lowercased) description.
        keyword_end: Index just past the matched salary keyword.

    Returns:
        Monthly euro amounts found in the window, range-filtered.
    """
    window = text[keyword_end : keyword_end + _CONTEXT_WINDOW]
    is_yearly = bool(_YEARLY.search(window))
    values: list[int] = []
    for match in _AMOUNT.finditer(window):
        parsed = _parse_amount(match.group(1) or match.group(2) or "")
        if parsed is None:
            continue
        monthly = round(parsed / 12) if (is_yearly or parsed >= 12000) else parsed
        if _MONTHLY_MIN <= monthly <= _MONTHLY_MAX:
            values.append(monthly)
    return values


def extract_salary_range(text: str | None) -> tuple[int | None, int | None]:
    """Extract a monthly gross salary range (EUR) from description text.

    Args:
        text: The job description (may be None).

    Returns:
        ``(min, max)`` monthly euro amounts, or ``(None, None)`` when no
        confident salary figure is found.
    """
    if not text:
        return None, None
    # Scraped descriptions markdown-escape punctuation (e.g. "3\.255,55"); drop
    # the backslashes so the amount regex sees clean numbers.
    lowered = text.lower().replace("\\", "")
    amounts: list[int] = []
    for keyword in _SALARY_CONTEXT.finditer(lowered):
        amounts.extend(_amounts_in_window(lowered, keyword.end()))
    if not amounts:
        return None, None
    return min(amounts), max(amounts)
