"""Tell Dutch from English.

A stopword vote rather than a model. The only two candidates share almost no
high-frequency function words, so on any real letter or vacancy the vote is
decisive, and it costs no dependency. Words that are common in both languages
("is", "in", "we", "was", "of") are deliberately left out of both lists, because
they would vote for whichever language happened to list them.
"""

from __future__ import annotations

import re

from job_scout.letters.models import LetterLanguage

_DUTCH = frozenset(
    {
        "de", "het", "een", "en", "van", "ik", "mijn", "je", "jij", "u", "uw",
        "zijn", "op", "voor", "met", "bij", "naar", "niet", "ook", "als", "dat",
        "die", "wat", "wij", "graag", "heb", "heeft", "hebben", "deze", "dit",
        "om", "te", "aan", "er", "door", "maar", "over", "tot", "wordt",
        "worden", "kunnen", "zal", "zou", "veel", "onze", "ons", "hun", "ben",
    }
)  # fmt: skip

_ENGLISH = frozenset(
    {
        "the", "and", "to", "i", "my", "you", "your", "for", "with", "on", "at",
        "as", "that", "this", "be", "are", "have", "has", "would", "will", "can",
        "an", "by", "from", "our", "not", "it", "their", "which", "am", "been",
        "were", "these", "those", "about", "into",
    }
)  # fmt: skip

_WORD = re.compile(r"[a-zA-ZÀ-ɏ]+")

# Below this many stopword hits the text is too short to vote on.
_MIN_VOTES = 5


def detect_language(
    text: str, *, default: LetterLanguage = LetterLanguage.NL
) -> LetterLanguage:
    """Guess whether a text is Dutch or English.

    Args:
        text: Any prose -- a vacancy, a letter, a CV.
        default: Returned when the text is too short or evenly split to call.

    Returns:
        The detected language, or ``default`` when there is no clear answer.
    """
    words = [w.lower() for w in _WORD.findall(text)]
    dutch = sum(1 for w in words if w in _DUTCH)
    english = sum(1 for w in words if w in _ENGLISH)
    if dutch + english < _MIN_VOTES or dutch == english:
        return default
    return LetterLanguage.NL if dutch > english else LetterLanguage.EN
