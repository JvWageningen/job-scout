"""Detect and prune job vacancies that are filled or no longer available.

The pruner fetches each job's original posting and decides whether the vacancy
is still open. It is deliberately conservative: a job is only pruned on a strong
signal (an explicit "filled"/"closed" notice or a gone/expired page). Anything
ambiguous or unreachable is left untouched so a still-open vacancy is never
dropped.

Detection signals, in order of reliability:

- LinkedIn: expired postings redirect to a generic search with a
  ``expired_jd_redirect`` marker — a clean, keyless signal.
- HTTP status: 404 / 410 / 451 mean the posting is gone.
- Text scan: explicit Dutch/English "vacancy filled / no longer available"
  phrases on the fetched page.
- Optional LLM judgement for ambiguous pages (e.g. a company careers page).

Indeed and some other boards block plain HTTP requests (HTTP 403 + captcha);
an optional Playwright fallback renders those pages when the ``browser`` extra
is installed.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from enum import StrEnum
from typing import TYPE_CHECKING

import requests
from loguru import logger
from pydantic import BaseModel

if TYPE_CHECKING:
    from job_scout.database import Database
    from job_scout.llm.base import LLMClient
    from job_scout.models import Config, JobListing

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}
_DEFAULT_TIMEOUT = 15
_MAX_TEXT_CHARS = 6000

# Specific phrases that reliably indicate a vacancy is filled/closed. Kept
# specific (not bare words like "gesloten") to avoid pruning still-open jobs.
_FILLED_PHRASES: frozenset[str] = frozenset(
    [
        # English
        "no longer accepting applications",
        "no longer available",
        "this job is no longer",
        "this position is no longer",
        "position has been filled",
        "position is now filled",
        "this position has been filled",
        "vacancy has been filled",
        "role has been filled",
        "applications are closed",
        "we are no longer accepting",
        "job posting has expired",
        "this posting has expired",
        "posting is no longer active",
        # Dutch
        "vacature is vervuld",
        "deze vacature is vervuld",
        "vacature is gesloten",
        "deze vacature is gesloten",
        "vacature is verlopen",
        "deze vacature is verlopen",
        "niet meer beschikbaar",
        "reageren is niet meer mogelijk",
        "solliciteren is niet meer mogelijk",
        "helaas is deze vacature",
    ]
)


class PruneOutcome(StrEnum):
    """Result of checking whether a vacancy is still open."""

    OPEN = "open"
    FILLED = "filled"
    GONE = "gone"
    UNKNOWN = "unknown"


class PruneCheck(BaseModel):
    """Outcome of a single vacancy check."""

    outcome: PruneOutcome
    reason: str
    signal: str | None = None

    @property
    def should_prune(self) -> bool:
        """Whether this outcome warrants pruning the job."""
        return self.outcome in (PruneOutcome.FILLED, PruneOutcome.GONE)


class PruneStats(BaseModel):
    """Aggregate counts for a prune sweep."""

    checked: int = 0
    pruned: int = 0
    still_open: int = 0
    unknown: int = 0


def _strip_html(html: str) -> str:
    """Return lowercased visible-ish text from raw HTML.

    Args:
        html: Raw HTML string.

    Returns:
        Lowercased text with tags removed, truncated for scanning.
    """
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return text.lower()[: _MAX_TEXT_CHARS * 4]


def _scan_filled_phrases(text: str) -> str | None:
    """Return the first fill/closed phrase found in *text*, if any.

    Args:
        text: Lowercased page text.

    Returns:
        The matched phrase, or None if no fill signal is present.
    """
    return next((phrase for phrase in _FILLED_PHRASES if phrase in text), None)


def _fetch_page_browser(url: str, timeout: int) -> tuple[int, str] | None:
    """Fetch a page via Playwright, for boards that block plain requests.

    Args:
        url: Page URL.
        timeout: Timeout in seconds.

    Returns:
        Tuple of (status_code, html), or None if rendering is unavailable.
    """
    try:
        from playwright.sync_api import sync_playwright  # noqa: PLC0415
    except ImportError:
        logger.debug("Playwright not installed; skipping browser fallback")
        return None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(user_agent=_HEADERS["User-Agent"])
            resp = page.goto(url, timeout=timeout * 1000, wait_until="domcontentloaded")
            status = resp.status if resp else 0
            html = page.content()
            browser.close()
            return status, html
    except Exception as exc:  # noqa: BLE001 - Playwright raises broad errors
        logger.debug(f"Browser fetch failed for {url}: {exc}")
        return None


def _fetch_page(url: str, timeout: int, use_browser: bool) -> tuple[int, str] | None:
    """Fetch a page, falling back to a browser render when blocked.

    Args:
        url: Page URL.
        timeout: Timeout in seconds.
        use_browser: Whether to retry blocked pages with Playwright.

    Returns:
        Tuple of (status_code, html), or None on total failure.
    """
    try:
        resp = requests.get(
            url, headers=_HEADERS, timeout=timeout, allow_redirects=True
        )
    except requests.RequestException as exc:
        logger.debug(f"Request failed for {url}: {exc}")
        return _fetch_page_browser(url, timeout) if use_browser else None
    if resp.status_code == 403 and use_browser:
        rendered = _fetch_page_browser(url, timeout)
        if rendered is not None:
            return rendered
    return resp.status_code, resp.text


def _check_linkedin(url: str, timeout: int) -> PruneCheck:
    """Check a LinkedIn job via its expired-redirect behaviour.

    Open postings stay at ``/jobs/view/<id>``; expired ones redirect to a
    generic search tagged ``expired_jd_redirect``.

    Args:
        url: LinkedIn job URL.
        timeout: Timeout in seconds.

    Returns:
        A PruneCheck; UNKNOWN if the request fails.
    """
    try:
        resp = requests.get(
            url, headers=_HEADERS, timeout=timeout, allow_redirects=True
        )
    except requests.RequestException as exc:
        return PruneCheck(outcome=PruneOutcome.UNKNOWN, reason=f"request failed: {exc}")
    final = resp.url
    if "expired_jd_redirect" in final or "/jobs/view/" not in final:
        return PruneCheck(
            outcome=PruneOutcome.GONE,
            reason="LinkedIn redirected an expired posting away from the job view",
            signal="expired_jd_redirect",
        )
    return PruneCheck(outcome=PruneOutcome.OPEN, reason="still at LinkedIn job view")


def _llm_vacancy_judgment(job: JobListing, text: str, client: LLMClient) -> PruneCheck:
    """Ask the LLM whether a fetched page shows the vacancy as open or filled.

    Args:
        job: The job being checked.
        text: Lowercased page text.
        client: LLM client.

    Returns:
        A PruneCheck derived from the model's verdict; UNKNOWN on any error.
    """
    from job_scout.evaluator import _extract_json  # noqa: PLC0415
    from job_scout.llm.base import LLMError  # noqa: PLC0415

    prompt = (
        f"Job: {job.title} at {job.company}.\n"
        "Below is text from its web page. Decide if this vacancy is still open "
        "for applications, or filled/closed/removed. Respond ONLY as JSON: "
        '{"status": "open" | "filled" | "unknown"}.\n\n'
        f"PAGE TEXT:\n{text[:_MAX_TEXT_CHARS]}"
    )
    try:
        raw = client.complete(prompt, purpose="quick_eval")
        status = str(_extract_json(raw).get("status", "unknown")).lower()
    except (LLMError, ValueError) as exc:
        return PruneCheck(outcome=PruneOutcome.UNKNOWN, reason=f"LLM error: {exc}")
    if status == "filled":
        return PruneCheck(
            outcome=PruneOutcome.FILLED, reason="LLM judged filled/closed", signal="llm"
        )
    if status == "open":
        return PruneCheck(outcome=PruneOutcome.OPEN, reason="LLM judged open")
    return PruneCheck(outcome=PruneOutcome.UNKNOWN, reason="LLM inconclusive")


def check_vacancy_open(
    job: JobListing,
    *,
    timeout: int = _DEFAULT_TIMEOUT,
    use_browser: bool = False,
    client: LLMClient | None = None,
) -> PruneCheck:
    """Determine whether a job's vacancy is still open.

    Args:
        job: Job to check (uses ``job.url``).
        timeout: Per-request timeout in seconds.
        use_browser: Retry blocked pages with Playwright when available.
        client: Optional LLM client for judging ambiguous pages.

    Returns:
        A PruneCheck describing the outcome.
    """
    url = job.url or ""
    if not url:
        return PruneCheck(outcome=PruneOutcome.UNKNOWN, reason="no url")

    if "linkedin.com/jobs/view" in url:
        return _check_linkedin(url, timeout)

    fetched = _fetch_page(url, timeout, use_browser)
    if fetched is None:
        return PruneCheck(outcome=PruneOutcome.UNKNOWN, reason="fetch failed")
    status, html = fetched
    if status in (404, 410, 451):
        return PruneCheck(
            outcome=PruneOutcome.GONE, reason=f"HTTP {status}", signal=str(status)
        )
    if status == 403 or status >= 500:
        return PruneCheck(
            outcome=PruneOutcome.UNKNOWN, reason=f"HTTP {status} (blocked/error)"
        )
    text = _strip_html(html)
    phrase = _scan_filled_phrases(text)
    if phrase:
        return PruneCheck(
            outcome=PruneOutcome.FILLED, reason="fill phrase found", signal=phrase
        )
    if client is not None:
        return _llm_vacancy_judgment(job, text, client)
    return PruneCheck(outcome=PruneOutcome.OPEN, reason="no fill signal found")


def prune_jobs(
    jobs: list[JobListing],
    db: Database,
    config: Config,
    *,
    dry_run: bool = False,
    client: LLMClient | None = None,
) -> PruneStats:
    """Check a set of jobs and mark filled/gone vacancies as EXPIRED.

    Args:
        jobs: Jobs to check (typically active, non-applied listings).
        db: Database for persisting expirations.
        config: Application configuration.
        dry_run: When True, report but do not persist changes.
        client: Optional LLM client for ambiguous-page judgement.

    Returns:
        PruneStats with per-outcome counts.
    """
    stats = PruneStats()
    if not jobs:
        return stats

    use_browser = getattr(config, "prune_use_browser", False)
    max_workers = min(getattr(config, "max_parallel_evaluations", 5), len(jobs))
    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
        future_to_job = {
            executor.submit(
                check_vacancy_open, job, use_browser=use_browser, client=client
            ): job
            for job in jobs
        }
        for future in as_completed(future_to_job):
            job = future_to_job[future]
            _apply_prune_result(job, future.result(), db, stats, dry_run=dry_run)
    logger.info(
        f"Prune sweep: checked={stats.checked} pruned={stats.pruned} "
        f"open={stats.still_open} unknown={stats.unknown}"
    )
    return stats


def _apply_prune_result(
    job: JobListing,
    check: PruneCheck,
    db: Database,
    stats: PruneStats,
    *,
    dry_run: bool,
) -> None:
    """Record a single check result into stats and (optionally) the database.

    Args:
        job: The checked job.
        check: The check outcome.
        db: Database for persistence.
        stats: Mutable stats to update.
        dry_run: When True, do not persist.
    """
    stats.checked += 1
    if check.should_prune:
        stats.pruned += 1
        logger.info(
            f"Prune ({check.outcome}): {job.title} @ {job.company} "
            f"[{check.signal or check.reason}]"
        )
        if not dry_run and job.id:
            db.mark_expired(job.id, reason=f"{check.outcome}: {check.reason}")
    elif check.outcome == PruneOutcome.UNKNOWN:
        stats.unknown += 1
    else:
        stats.still_open += 1
