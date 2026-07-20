"""Find a vacancy on the employer's own careers page and check availability.

For a job that made the notify cut, this searches the web for the same vacancy
on the company's *own* site (or its applicant-tracking system) rather than a job
board, so the candidate can apply at the source. It then re-checks availability
on that page, reusing the pruner's open/filled detection.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from loguru import logger
from pydantic import BaseModel

from job_scout.pruner import PruneOutcome, check_vacancy_open
from job_scout.websearch import SearchResult, web_search

# Aggregators / boards that are never the employer's own page.
_JOB_BOARDS: frozenset[str] = frozenset(
    [
        "linkedin.com",
        "indeed.com",
        "glassdoor.com",
        "glassdoor.nl",
        "nationalevacaturebank.nl",
        "joblift.nl",
        "jobrapido.com",
        "monsterboard.nl",
        "monster.com",
        "jobbird.com",
        "adzuna.nl",
        "jooble.org",
        "talent.com",
        "werkzoeken.nl",
        "vacatures.nl",
        "neuvoo.nl",
        "trovit.nl",
        "careerjet.nl",
        "stepstone.nl",
        "intermediair.nl",
        "magnet.me",
        "youngcapital.nl",
        "tempo-team.nl",
        "randstad.nl",
        "yacht.nl",
        "undutchables.nl",
        "hays.nl",
        "michaelpage.nl",
        "ecommerceguide.com",
        "kununu.com",
        "indeed.nl",
        "nl.indeed.com",
        "simplyhired.com",
        "jobisjob.nl",
        "emplooi.nl",
    ]
)
# Applicant-tracking systems: a company subdomain here is effectively official.
_ATS_DOMAINS: frozenset[str] = frozenset(
    [
        "homerun.co",
        "greenhouse.io",
        "lever.co",
        "recruitee.com",
        "workable.com",
        "personio.com",
        "personio.nl",
        "personio.de",
        "bamboohr.com",
        "smartrecruiters.com",
        "teamtailor.com",
        "ashbyhq.com",
        "jobvite.com",
        "myworkdayjobs.com",
        "join.com",
        "carerix.com",
        "factorialhr.com",
    ]
)
_LEGAL_SUFFIXES = re.compile(
    r"\b(b\.?v\.?|n\.?v\.?|group|nederland|holding|international|inc|ltd|gmbh)\b",
    re.IGNORECASE,
)


class OfficialSource(BaseModel):
    """The employer's own posting for a vacancy, if found."""

    url: str | None = None
    title: str | None = None
    available: bool | None = None  # None = could not determine
    reason: str = ""


def _normalize_company(name: str) -> str:
    """Reduce a company name to comparable alphanumerics (no legal suffixes)."""
    stripped = _LEGAL_SUFFIXES.sub("", name)
    return re.sub(r"[^a-z0-9]", "", stripped.lower())


def _domain(url: str) -> str:
    """Return the lowercased host of a URL without a leading www."""
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _registered_domain(host: str) -> str:
    """Return the last two labels of a host (best-effort registered domain)."""
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _is_job_board(host: str) -> bool:
    """True if the host is a known aggregator/job board."""
    return _registered_domain(host) in _JOB_BOARDS or host in _JOB_BOARDS


def _score_result(result: SearchResult, company_norm: str) -> int:
    """Rank how likely a result is the employer's own posting (higher = better).

    Args:
        result: The search result.
        company_norm: Normalized company name.

    Returns:
        A score; 0 or below means "not an official page".
    """
    host = _domain(result.url)
    if not host or _is_job_board(host):
        return 0
    host_norm = re.sub(r"[^a-z0-9]", "", host)
    score = 0
    if company_norm and company_norm in host_norm:
        score += 10  # company name in the domain — strongest signal
    if _registered_domain(host) in _ATS_DOMAINS:
        score += 6  # applicant-tracking system
    if re.search(r"werkenbij|careers|vacature|jobs|/job", result.url, re.IGNORECASE):
        score += 2
    return score


def _select_official(results: list[SearchResult], company: str) -> SearchResult | None:
    """Pick the best official-page candidate from search results."""
    company_norm = _normalize_company(company)
    scored = [(_score_result(r, company_norm), r) for r in results]
    ranked = [(s, r) for s, r in scored if s > 0]
    if not ranked:
        return None
    ranked.sort(key=lambda sr: sr[0], reverse=True)
    return ranked[0][1]


def find_official_source(
    title: str,
    company: str,
    *,
    use_browser: bool = False,
    timeout: int = 15,
    searxng_url: str | None = None,
    api_key: str | None = None,
) -> OfficialSource:
    """Search for a vacancy on the employer's own site and check availability.

    Args:
        title: Job title.
        company: Company name.
        use_browser: Retry blocked pages with Playwright during the check.
        timeout: Per-request timeout in seconds.
        searxng_url: Optional SearXNG instance URL for reliable search.
        api_key: Optional Brave Search API key for reliable search.

    Returns:
        An OfficialSource with the URL and availability, or empty when none found.
    """
    if not company:
        return OfficialSource(reason="no company name")
    results = web_search(
        f"{title} {company} vacature",
        max_results=8,
        timeout=timeout,
        searxng_url=searxng_url,
        api_key=api_key,
    )
    best = _select_official(results, company)
    if best is None:
        return OfficialSource(reason="no official employer page found in results")

    from datetime import UTC, datetime  # noqa: PLC0415

    from job_scout.models import JobListing  # noqa: PLC0415

    probe = JobListing(
        title=title,
        company=company,
        location="",
        url=best.url,
        source="official",
        seen_at=datetime.now(UTC),
    )
    check = check_vacancy_open(probe, timeout=timeout, use_browser=use_browser)
    if check.outcome == PruneOutcome.OPEN:
        available: bool | None = True
    elif check.outcome in (PruneOutcome.FILLED, PruneOutcome.GONE):
        available = False
    else:
        available = None
    logger.info(
        f"Official source for {title} @ {company}: {best.url} (available={available})"
    )
    return OfficialSource(
        url=best.url, title=best.title, available=available, reason=check.reason
    )
