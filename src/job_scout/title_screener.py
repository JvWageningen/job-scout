"""Batch LLM-based title screening to filter irrelevant jobs cheaply."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed

from loguru import logger

from job_scout.evaluator import _extract_json
from job_scout.llm.base import LLMClient, LLMError
from job_scout.llm.factory import get_llm_client
from job_scout.models import Config, JobListing

BATCH_SIZE = 80
SCREENING_TIMEOUT = 60


def _screen_batch_parallel(
    args: tuple[list[JobListing], str, str, LLMClient],
) -> list[JobListing]:
    """Screen a batch of jobs for parallel execution.

    Args:
        args: Tuple of (batch, profile, negative_desc, client).

    Returns:
        Jobs that passed screening.
    """
    batch, profile, negative_desc, client = args
    return _screen_batch(batch, profile, negative_desc, client)


def _build_screening_prompt(
    jobs: list[JobListing],
    profile: str,
    negative_desc: str,
) -> str:
    """Build the prompt for batch title screening.

    Args:
        jobs: Job listings to screen.
        profile: Candidate's profile description.
        negative_desc: Description of roles/criteria to reject.

    Returns:
        Complete prompt string.
    """
    lines = [f"{i + 1}. {job.title} @ {job.company}" for i, job in enumerate(jobs)]
    job_list = "\n".join(lines)

    return (
        "Quick title screening. Based ONLY on the job title and company "
        "name, decide which jobs COULD be relevant. Do NOT research "
        "companies or look anything up. Just compare titles to the "
        "profile.\n\n"
        f"CANDIDATE PROFILE:\n{profile}\n\n"
        f"ROLES TO REJECT:\n{negative_desc}\n\n"
        f"JOB TITLES:\n{job_list}\n\n"
        'Respond with ONLY this JSON, nothing else: {"keep": [1, 3, ...]}\n\n'
        "Rules:\n"
        "- KEEP jobs that COULD match the profile based on title alone\n"
        "- REMOVE only CLEARLY irrelevant titles\n"
        "- When in doubt, KEEP the job"
    )


def _screen_batch(
    jobs: list[JobListing],
    profile: str,
    negative_desc: str,
    client: LLMClient,
) -> list[JobListing]:
    """Screen a single batch of jobs via one LLM call.

    Args:
        jobs: Jobs in this batch.
        profile: Candidate's profile description.
        negative_desc: Description of roles to reject.
        client: LLM client to use for this call.

    Returns:
        Jobs that the LLM marked as potentially relevant.
        Returns all jobs on any error (fail-open).
    """
    prompt = _build_screening_prompt(jobs, profile, negative_desc)

    try:
        output = client.complete(prompt, purpose="screening", timeout=SCREENING_TIMEOUT)
        data = _extract_json(output)
        keep_indices = data.get("keep")
        if not isinstance(keep_indices, list):
            logger.warning("Screening response missing 'keep' list")
            return jobs

        valid = set()
        for idx in keep_indices:
            try:
                n = int(idx)
                if 1 <= n <= len(jobs):
                    valid.add(n)
            except (ValueError, TypeError):
                continue

        return [jobs[i - 1] for i in sorted(valid)]

    except json.JSONDecodeError as e:
        logger.warning(f"Screening JSON parse error: {e}")
        return jobs
    except LLMError as e:
        logger.warning(f"Screening LLM call failed: {e}")
        return jobs


def _batch_jobs(
    jobs: list[JobListing],
    batch_size: int = BATCH_SIZE,
) -> list[list[JobListing]]:
    """Split a job list into batches.

    Args:
        jobs: Full list of jobs to split.
        batch_size: Maximum jobs per batch.

    Returns:
        List of job sublists.
    """
    return [jobs[i : i + batch_size] for i in range(0, len(jobs), batch_size)]


def screen_job_titles(
    jobs: list[JobListing],
    config: Config,
    *,
    client: LLMClient | None = None,
) -> tuple[list[JobListing], int]:
    """Screen job titles via batch LLM calls in parallel.

    Sends all titles to the LLM in batches and asks which ones could
    be relevant. Processes batches in parallel for efficiency.
    Returns all jobs on error (fail-open).

    Args:
        jobs: Jobs that passed keyword filtering.
        config: Application configuration with profile description.
        client: LLM client to use; if None, one is built from config.

    Returns:
        Tuple of (kept_jobs, screened_count).
    """
    if not jobs:
        return [], 0

    if not config.profile_description:
        logger.warning("No profile — skipping title screening")
        return jobs, 0

    if client is None:
        client = get_llm_client(config)

    batch_size = (
        config.zai_screening_batch_size
        if config.llm_provider in ("zai", "kilo_cli")
        else BATCH_SIZE
    )
    batches = list(_batch_jobs(jobs, batch_size))

    if not batches:
        return [], 0

    # Screen batches in parallel with bounded thread pool
    kept: list[JobListing] = []
    max_workers = min(config.max_parallel_evaluations, len(batches))

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all batch screening tasks
        futures = []
        for batch in batches:
            future = executor.submit(
                _screen_batch_parallel,
                (
                    batch,
                    config.profile_description,
                    config.negative_description,
                    client,
                ),
            )
            futures.append(future)

        # Process results as they complete
        for future in as_completed(futures):
            batch_results = future.result()
            kept.extend(batch_results)

    screened = len(jobs) - len(kept)
    if screened:
        logger.info(f"Title screening: {screened} removed, {len(kept)} remaining")
    return kept, screened
