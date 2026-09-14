"""Lightweight title-based pre-filter to skip obviously irrelevant jobs.

Matching is morpheme-aware rather than a plain substring test, because a job
board for the Dutch market makes naive substring matching actively harmful:

- ``"AI"`` as a substring matches *Maintenance*, *Repair*, *Trainee*, *detail*.
- ``"bouw"`` as an exclude keyword matches *werktuigbouwkunde* (mechanical
  engineering) and ``"zorg"`` matches *kwaliteitszorg* (quality assurance) --
  both of which are squarely in scope for a measurement/physics profile.

The obvious repair -- strict ``\\b`` word boundaries -- is worse still, because
Dutch and English titles inflect the keyword (*engineer* -> *engineering*,
*optica* -> *optical*, *kwaliteit* -> *kwaliteitscontroleur*), so the substring
behaviour is load-bearing. Germanic compounding then adds the mirror problem:
the head noun comes last (*Testingenieur*, *Projectengineer*, *Nanophotonics*),
so anchoring only to a word start misses it.

The rule below therefore matches on morpheme boundaries, asymmetrically:

- **Acronyms** (<= 3 chars, upper-case, e.g. ``AI``, ``HR``, ``SAP``) must
  match as standalone tokens -- bounded on *both* sides -- so ``AI`` no longer
  means *Maintenance*, *Repair* or *Retail*.
- **Include** keywords match at a word start **or** a word end, so both
  *Optical* (start) and *Testingenieur* (end) are admitted.
- **Exclude** keywords match at a word start **only**. The asymmetry is
  deliberate: for a positive signal the compound head matters (a
  *Testingenieur* is an engineer), for a negative signal it must not
  (*machinebouw* is high-tech, not construction).
"""

from __future__ import annotations

import re
from functools import lru_cache

from loguru import logger

from job_scout.models import Config, JobListing

# Letter class used for morpheme-boundary lookaround, applied to a lower-cased
# title. Includes the Latin-1/Latin-A range so accented characters count as
# letters rather than as boundaries.
_LETTERS = r"a-z0-9À-ɏ"

_MAX_ACRONYM_LEN = 3


@lru_cache(maxsize=512)
def _compile(keyword: str, *, head: bool) -> re.Pattern[str]:
    """Compile one keyword into its matching pattern.

    Args:
        keyword: Raw keyword from the user's configuration.
        head: True for include keywords, which may also match a compound head
            at the end of a word; False for exclude keywords, which anchor to
            a word start only.

    Returns:
        A pattern to apply to the lower-cased title.
    """
    stem = re.escape(keyword.lower())

    if len(keyword) <= _MAX_ACRONYM_LEN and keyword.isupper():
        # Acronyms only mean anything as standalone tokens: "AI Engineer" yes,
        # "Maintenance" no. Requiring a boundary on *both* sides is what kills
        # the mid-word noise, so this stays case-insensitive and keeps the
        # documented "matches regardless of case" behaviour for tokens like
        # "CRO".
        return re.compile(rf"(?<![{_LETTERS}]){stem}(?![{_LETTERS}])")

    if head:
        return re.compile(rf"(?<![{_LETTERS}]){stem}|{stem}(?![{_LETTERS}])")
    return re.compile(rf"(?<![{_LETTERS}]){stem}")


def keyword_matches(keyword: str, title: str, *, head: bool) -> bool:
    """Check whether one keyword matches a job title.

    Args:
        keyword: Keyword to look for.
        title: Job title, in its original casing.
        head: True to also allow a match on a compound head (word end).

    Returns:
        True if the keyword matches the title.
    """
    if not keyword.strip():
        return False
    return bool(_compile(keyword, head=head).search(title.lower()))


def passes_title_filter(job: JobListing, config: Config) -> bool:
    """Check whether a job title passes the configured keyword filters.

    A job is rejected if its title contains any exclude keyword, or if
    include keywords are configured and the title contains none of them.

    Args:
        job: Job listing to check.
        config: Application configuration with title filter keywords.

    Returns:
        True if the job should proceed to full evaluation.
    """
    hits = [
        kw
        for kw in config.title_include_keywords
        if keyword_matches(kw, job.title, head=True)
    ]

    for kw in config.title_exclude_keywords:
        if keyword_matches(kw, job.title, head=False):
            # Log the surviving include hits too: a veto that overrules several
            # positive signals is usually a sign of a bad exclude keyword.
            logger.debug(
                "Title excluded ('{}', despite include hits {}): {}",
                kw,
                hits or "none",
                job.title,
            )
            return False

    if not config.title_include_keywords:
        return True

    if hits:
        return True

    logger.debug(f"Title not matched: {job.title}")
    return False


def filter_jobs_by_title(
    jobs: list[JobListing], config: Config
) -> tuple[list[JobListing], int]:
    """Filter a list of jobs by title keywords.

    Args:
        jobs: Jobs to filter.
        config: Application configuration with title filter keywords.

    Returns:
        Tuple of (passed_jobs, filtered_count).
    """
    if not config.title_include_keywords and not config.title_exclude_keywords:
        return jobs, 0

    passed = [j for j in jobs if passes_title_filter(j, config)]
    filtered = len(jobs) - len(passed)
    if filtered:
        logger.info(f"Title filter: {filtered} jobs filtered, {len(passed)} remaining")
    return passed, filtered
