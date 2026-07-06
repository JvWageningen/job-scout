"""Lightweight title-based pre-filter to skip obviously irrelevant jobs."""

from __future__ import annotations

from loguru import logger

from job_scout.models import Config, JobListing


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
    title_lower = job.title.lower()

    for kw in config.title_exclude_keywords:
        if kw.lower() in title_lower:
            logger.debug(f"Title excluded ('{kw}'): {job.title}")
            return False

    if not config.title_include_keywords:
        return True

    for kw in config.title_include_keywords:
        if kw.lower() in title_lower:
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
