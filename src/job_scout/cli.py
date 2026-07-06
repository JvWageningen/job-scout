"""Click CLI entry point for job-scout."""

from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, cast

import click
from loguru import logger

from job_scout.config import (
    SECRET_FIELDS,
    USER_FIELDS,
    build_effective_config,
    get_data_dir,
    list_users,
    load_config,
    load_user_config,
    save_config,
    save_user_config,
    secrets_path,
    set_config_value,
    update_secrets,
    user_db_path,
    user_dir,
    user_logs_dir,
)
from job_scout.database import Database
from job_scout.evaluator import (
    check_llm_available,
    evaluate_fit,
    generate_keywords,
    quick_evaluate_fit,
)
from job_scout.llm.base import LLMClient, LLMError
from job_scout.llm.factory import get_llm_client
from job_scout.models import Config, JobListing, JobStatus, RunStats
from job_scout.notify import NotificationError, get_notifier
from job_scout.scheduler import (
    check_schedule_status,
    install_schedule,
    remove_schedule,
)
from job_scout.scraper import scrape_all_jobs
from job_scout.title_filter import filter_jobs_by_title
from job_scout.title_screener import screen_job_titles
from job_scout.travel import calculate_travel_times, is_within_travel_limits


def _setup_logging(verbose: bool = False) -> None:
    """Configure loguru console logging for CLI use.

    Args:
        verbose: Enable DEBUG-level console output.
    """
    logger.remove()
    logger.add(sys.stderr, level="DEBUG" if verbose else "INFO")


def _add_file_sink(logs_dir: Path) -> int:
    """Add a rotating file log sink and return its ID.

    Args:
        logs_dir: Directory for the log file (created if needed).

    Returns:
        Loguru sink ID for later removal with logger.remove().
    """
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = logs_dir / f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    sink_id: int = logger.add(log_file, level="DEBUG", rotation="10 MB")
    return sink_id


def _get_db() -> Database:
    """Return the global (backward-compat) database instance."""
    return Database(get_data_dir() / "jobs.db")


def _load_cv_text(config: Config) -> str:
    """Parse the CV PDF and append any manual notes.

    Args:
        config: Application configuration with cv_path and cv_notes.

    Returns:
        Extracted CV text plus any manual experience notes.
    """
    text = ""
    if config.cv_path:
        cv_path = Path(config.cv_path)
        if not cv_path.exists():
            click.echo(f"Warning: CV not found at {config.cv_path}", err=True)
        else:
            from job_scout.cv_parser import parse_cv  # noqa: PLC0415

            text = parse_cv(cv_path)
    if config.cv_notes:
        if text:
            text = text + "\n\n--- Additional experience ---\n" + config.cv_notes
        else:
            text = config.cv_notes
    return text


def _require_llm() -> Config:
    """Load global config and exit if the LLM provider is not ready.

    Returns:
        Loaded application configuration.
    """
    config = load_config()
    ok, err = check_llm_available(config)
    if not ok:
        click.echo(err, err=True)
        sys.exit(1)
    return config


def _require_single_user(user_name: str | None) -> str:
    """Return the target user name or exit with an error if ambiguous.

    Args:
        user_name: Explicit user name or None.

    Returns:
        The resolved user name.
    """
    users = list_users()
    if not users:
        click.echo(
            "No users initialized. Run 'job-scout init --user NAME' first.", err=True
        )
        sys.exit(1)
    if user_name:
        if user_name not in users:
            click.echo(
                f"User {user_name!r} not found. Available: {', '.join(users)}", err=True
            )
            sys.exit(1)
        return user_name
    if len(users) == 1:
        return users[0]
    click.echo(f"Multiple users ({', '.join(users)}). Pass --user NAME.", err=True)
    sys.exit(1)


def _evaluate_job(
    job: JobListing, config: Config, cv_text: str, client: LLMClient
) -> bool:
    """Run LLM evaluation and update job fields in-place.

    Args:
        job: Job to evaluate (mutated).
        config: Application configuration.
        cv_text: Extracted CV text.
        client: LLM client to use.

    Returns:
        True if the job passes fit, negative, and compensation filters.
    """
    fit, neg, comp = evaluate_fit(
        job,
        config.profile_description,
        cv_text,
        config.negative_description,
        client=client,
    )
    job.fit_score = fit.fit_score
    job.fit_reasoning = fit.reasoning
    job.negative_match = neg.matches_negative
    job.negative_reasoning = neg.reasoning
    job.salary_min = comp.salary_min
    job.salary_max = comp.salary_max
    job.salary_period = comp.salary_period
    job.vacation_days = comp.vacation_days
    job.compensation_reasoning = comp.reasoning
    if neg.matches_negative:
        return False
    if fit.fit_score < config.fit_score_threshold:
        return False
    return _passes_compensation_filter(job, config)


def _passes_compensation_filter(job: JobListing, config: Config) -> bool:
    """Check if a job's salary and vacation meet configured minimums.

    Jobs with unknown compensation always pass (fail-open).

    Args:
        job: Job with compensation fields populated.
        config: Application configuration with salary/vacation limits.

    Returns:
        True if the job passes compensation filters.
    """
    if (
        config.min_salary is not None
        and job.salary_max is not None
        and job.salary_max < config.min_salary
    ):
        logger.info(
            f"Salary too low ({job.salary_max} < {config.min_salary}): {job.title}"
        )
        return False
    if (
        config.max_salary is not None
        and job.salary_min is not None
        and job.salary_min > config.max_salary
    ):
        logger.info(
            f"Salary too high ({job.salary_min} > {config.max_salary}): {job.title}"
        )
        return False
    if (
        config.min_vacation_days is not None
        and job.vacation_days is not None
        and job.vacation_days < config.min_vacation_days
    ):
        logger.info(
            f"Too few vacation days "
            f"({job.vacation_days} < {config.min_vacation_days}): "
            f"{job.title}"
        )
        return False
    return True


def _calculate_travel_for_job(
    job: JobListing,
    config: Config,
    db: Database | None = None,
) -> JobListing:
    """Calculate travel times for a single job (for parallel execution).

    Args:
        job: Job to calculate travel times for.
        config: Application configuration.
        db: Optional database for caching travel time and geocode results.

    Returns:
        Job with travel_times, location_unknown, and distance_km populated.
    """
    if job.location:
        travel_times, location_unknown, distance = calculate_travel_times(
            job.location, config, db
        )
        job.travel_times = travel_times
        job.location_unknown = location_unknown
        job.distance_km = distance
    return job


def _apply_travel_filter(job: JobListing, config: Config) -> bool:
    """Check if a job with calculated travel times passes the filter.

    Assumes travel_times, location_unknown, and distance_km are already populated.

    Args:
        job: Job to check (expects travel times already calculated).
        config: Application configuration.

    Returns:
        True if job is reachable within configured limits.
    """
    return is_within_travel_limits(
        job.location, job.travel_times, config, job.location_unknown, job.distance_km
    )


def _process_jobs(
    new_jobs: list[JobListing],
    config: Config,
    cv_text: str,
    db: Database,
    dry_run: bool,
    *,
    full: bool = False,
    client: LLMClient | None = None,
) -> tuple[list[JobListing], RunStats]:
    """Evaluate and filter all new jobs.

    Args:
        new_jobs: Jobs to process.
        config: Application configuration.
        cv_text: Extracted CV text.
        db: Database for persisting results.
        dry_run: Skip persistence if True.
        full: When True, upsert existing rows instead of INSERT OR IGNORE.
        client: LLM client; built from config if None.

    Returns:
        Tuple of (matched_jobs, stats).
    """
    from job_scout.llm.factory import get_llm_client  # noqa: PLC0415

    stats = RunStats()
    matched: list[JobListing] = []
    llm_client: LLMClient = client if client is not None else get_llm_client(config)
    survivors = _run_quick_eval(
        new_jobs, config, cv_text, db, dry_run, full, llm_client, stats
    )

    if not survivors:
        return matched, stats

    # Run full evaluations in parallel with bounded thread pool
    max_workers = min(config.max_parallel_evaluations, len(survivors))
    jobs_to_save: list[JobListing] = []
    evaluated_jobs: list[JobListing] = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all evaluation tasks
        future_to_job = {}
        for job in survivors:
            future = executor.submit(
                _eval_job_full_parallel, (job, config, cv_text, llm_client, db)
            )
            future_to_job[future] = job

        # Process results as they complete
        for future in as_completed(future_to_job):
            job, passed, error_msg = future.result()
            logger.info(f"Evaluating: {job.title} @ {job.company}")
            if error_msg:
                logger.error(f"Evaluation error: {error_msg}")
                stats.errors.append(error_msg)
                job.status = JobStatus.REJECTED
                stats.rejected += 1
                jobs_to_save.append(job)
                continue
            stats.evaluated += 1
            if not passed:
                job.status = JobStatus.REJECTED
                stats.rejected += 1
                jobs_to_save.append(job)
            else:
                # Job passed LLM filters; will calculate travel times in parallel
                evaluated_jobs.append(job)

    # Calculate travel times in parallel for all jobs that passed LLM evaluation
    if evaluated_jobs:
        travel_workers = min(config.max_parallel_evaluations, len(evaluated_jobs))

        def calculate_travel(job: JobListing) -> JobListing:
            """Helper to calculate travel times for a single job."""
            return _calculate_travel_for_job(job, config, db)

        with ThreadPoolExecutor(max_workers=travel_workers) as travel_executor:
            # Submit all travel time calculation tasks
            travel_futures = {}
            for eval_job in evaluated_jobs:
                future = travel_executor.submit(calculate_travel, eval_job)  # type: ignore[arg-type]
                travel_futures[future] = eval_job

            # Process travel time results as they complete
            for future in as_completed(travel_futures):
                eval_job = cast(JobListing, future.result())
                # Now apply the travel filter (no I/O, just checking limits)
                if _apply_travel_filter(eval_job, config):
                    eval_job.status = JobStatus.MATCHED
                    matched.append(eval_job)
                    stats.matched += 1
                else:
                    eval_job.status = JobStatus.REJECTED
                    stats.rejected += 1
                jobs_to_save.append(eval_job)

    # Batch save all evaluated jobs to reduce database overhead
    if not dry_run and jobs_to_save:
        job_ids = db.save_jobs_batch(jobs_to_save, update_existing=full)
        for job, job_id in zip(jobs_to_save, job_ids, strict=True):
            job.id = job_id

    return matched, stats


def _eval_job_quick_parallel(
    args: tuple[JobListing, str, str, LLMClient, Database],
) -> tuple[JobListing, int]:
    """Evaluate a single job's fit score for parallel execution.

    Checks database cache first before calling LLM.

    Args:
        args: Tuple of (job, profile_desc, cv_text, llm_client, db).

    Returns:
        Tuple of (job, fit_score).
    """
    job, profile_desc, cv_text, llm_client, db = args
    # Check cache first
    cached_score, _ = db.get_cached_evaluation(job)
    if cached_score is not None:
        logger.info(f"Using cached quick-eval score ({cached_score}): {job.title}")
        return job, cached_score
    score = quick_evaluate_fit(job, profile_desc, cv_text, client=llm_client)
    return job, score


def _eval_job_full_parallel(
    args: tuple[JobListing, Config, str, LLMClient, Database],
) -> tuple[JobListing, bool, str | None]:
    """Evaluate a single job's full fit for parallel execution.

    Checks database cache first before calling LLM.

    Args:
        args: Tuple of (job, config, cv_text, llm_client, db).

    Returns:
        Tuple of (job, passed, error_msg).
    """
    job, config, cv_text, llm_client, db = args
    try:
        # Check cache first
        cached_score, cached_data = db.get_cached_evaluation(job)
        if cached_score is not None and cached_data is not None:
            logger.info(f"Using cached full-eval: {job.title}")
            # Populate job with cached evaluation results
            job.fit_score = cached_score
            job.fit_reasoning = cached_data["fit_reasoning"]
            job.negative_match = cached_data["negative_match"]
            job.negative_reasoning = cached_data["negative_reasoning"]
            job.salary_min = cached_data["salary_min"]
            job.salary_max = cached_data["salary_max"]
            job.salary_period = cached_data["salary_period"]
            job.vacation_days = cached_data["vacation_days"]
            job.compensation_reasoning = cached_data["compensation_reasoning"]
            # Reapply filters with cached data
            if job.negative_match:
                return job, False, None
            if cached_score < config.fit_score_threshold:
                return job, False, None
            return job, _passes_compensation_filter(job, config), None
        passed = _evaluate_job(job, config, cv_text, llm_client)
        return job, passed, None
    except RuntimeError as exc:
        return job, False, str(exc)


def _run_quick_eval(
    jobs: list[JobListing],
    config: Config,
    cv_text: str,
    db: Database,
    dry_run: bool,
    full: bool,
    llm_client: LLMClient,
    stats: RunStats,
) -> list[JobListing]:
    """Run the cheap first-pass quick-eval filter in parallel.

    Args:
        jobs: All jobs to screen.
        config: Application configuration.
        cv_text: Extracted CV text.
        db: Database for rejected rows.
        dry_run: Skip persistence if True.
        full: Use upsert instead of INSERT OR IGNORE.
        llm_client: LLM client to use.
        stats: Mutable stats object.

    Returns:
        Jobs that survived the quick-eval threshold.
    """
    if not jobs:
        return []

    survivors: list[JobListing] = []
    rejected: list[JobListing] = []

    # Run evaluations in parallel with bounded thread pool
    max_workers = min(config.max_parallel_evaluations, len(jobs))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all tasks and keep track of job indices
        future_to_idx = {}
        for idx, job in enumerate(jobs):
            future = executor.submit(
                _eval_job_quick_parallel,
                (job, config.profile_description, cv_text, llm_client, db),
            )
            future_to_idx[future] = (idx + 1, len(jobs))

        # Process results as they complete
        for future in as_completed(future_to_idx):
            idx, total = future_to_idx[future]
            job, score = future.result()
            logger.info(f"Quick eval [{idx}/{total}]: {job.title} @ {job.company}")
            if score < config.quick_eval_threshold:
                logger.info(
                    f"Quick-filtered (score={score}): {job.title} @ {job.company}"
                )
                job.fit_score = score
                job.status = JobStatus.REJECTED
                stats.quick_filtered += 1
                rejected.append(job)
            else:
                survivors.append(job)

    # Batch save rejected jobs to reduce database overhead
    if not dry_run and rejected:
        job_ids = db.save_jobs_batch(rejected, update_existing=full)
        for job, job_id in zip(rejected, job_ids, strict=True):
            job.id = job_id

    return survivors


def _send_notifications(
    matched: list[JobListing], db: Database, config: Config, dry_run: bool
) -> int:
    """Send notifications for matched jobs and retry pending ones.

    Args:
        matched: Newly matched jobs to notify about.
        db: Database for updating notification state.
        config: Application configuration with notification settings.
        dry_run: Skip actual sending if True.

    Returns:
        Number of notifications successfully sent.
    """
    if not matched:
        return 0

    sent = 0
    try:
        notifier = get_notifier(config)
    except NotificationError as e:
        logger.warning(f"Notification channel not available: {e}")
        return 0

    notification_mode = getattr(config, "notification_mode", "per_job")

    if notification_mode == "digest":
        if dry_run:
            click.echo(f"[DRY RUN] Would send digest notification: {len(matched)} jobs")
        else:
            try:
                notifier.send_digest(matched)
                for job in matched:
                    if job.id:
                        db.mark_notified(job.id)
                sent = len(matched)
                logger.info(f"Digest notification sent for {sent} jobs")
            except NotificationError as e:
                logger.warning(f"Digest notification failed: {e}")
                for job in matched:
                    if job.id:
                        db.mark_notification_pending(job.id)
    else:
        for job in matched:
            if dry_run:
                click.echo(
                    f"[DRY RUN] Would notify: {job.title} @ {job.company} "
                    f"({job.fit_score})"
                )
                continue
            try:
                notifier.send(job)
                if job.id:
                    db.mark_notified(job.id)
                sent += 1
            except NotificationError:
                if job.id:
                    db.mark_notification_pending(job.id)

    if not dry_run:
        for pending in db.get_pending_notifications():
            try:
                if notification_mode == "digest":
                    notifier.send_digest([pending])
                else:
                    notifier.send(pending)
                if pending.id:
                    db.mark_notified(pending.id)
                    sent += 1
            except NotificationError:
                pass
    return sent


def _merge_stats(target: RunStats, source: RunStats) -> None:
    """Copy pipeline stats from source into target.

    Args:
        target: Stats object to update in-place.
        source: Stats from _process_jobs.
    """
    target.quick_filtered = source.quick_filtered
    target.evaluated = source.evaluated
    target.matched = source.matched
    target.rejected = source.rejected
    target.errors = source.errors


def _filter_and_screen(
    jobs: list[JobListing], config: Config, stats: RunStats
) -> list[JobListing] | None:
    """Apply title filter then LLM title screening.

    Args:
        jobs: Jobs to filter.
        config: Application configuration.
        stats: Mutable stats to update.

    Returns:
        Screened jobs, or None if none survived.
    """
    candidates, title_filtered = filter_jobs_by_title(jobs, config)
    stats.title_filtered = title_filtered
    if not candidates:
        click.echo("No jobs passed the title filter.")
        _print_run_summary(stats)
        return None
    click.echo("Screening job titles…")
    screened, title_screened = screen_job_titles(candidates, config)
    stats.title_screened = title_screened
    if not screened:
        click.echo("No jobs passed title screening.")
        _print_run_summary(stats)
        return None
    return screened


def _run_pipeline(
    config: Config,
    db: Database,
    cv_text: str,
    *,
    dry_run: bool = False,
    full: bool = False,
    llm_client: LLMClient,
) -> tuple[RunStats, datetime, float]:
    """Core job search pipeline: scrape → filter → evaluate → notify.

    Args:
        config: Effective configuration.
        db: Database for persistence.
        cv_text: Extracted CV text.
        dry_run: Skip persistence and notification.
        full: Bypass dedup, upsert results, re-notify matches.
        llm_client: Shared LLM client for scraping and evaluation.

    Returns:
        Tuple of (stats, started_at, duration_seconds).
    """
    started_at = datetime.now()
    if full:
        logger.info("Full rerun: dedup bypassed, upsert enabled")
    click.echo("Scraping job listings…")
    all_jobs = scrape_all_jobs(config, llm_client)
    new_jobs = all_jobs if full else [j for j in all_jobs if not db.is_duplicate(j)]
    deduped = 0 if full else (len(all_jobs) - len(new_jobs))
    stats = RunStats(scraped=len(all_jobs), deduplicated=deduped)
    logger.info(f"Scraped {stats.scraped}, new: {len(new_jobs)}")
    if not new_jobs:
        click.echo("No new jobs found.")
        _print_run_summary(stats)
        duration = (datetime.now() - started_at).total_seconds()
        return stats, started_at, duration
    screened = _filter_and_screen(new_jobs, config, stats)
    if screened is None:
        duration = (datetime.now() - started_at).total_seconds()
        return stats, started_at, duration
    matched, run_stats = _process_jobs(
        screened, config, cv_text, db, dry_run, full=full, client=llm_client
    )
    _merge_stats(stats, run_stats)
    stats.notified = _send_notifications(matched, db, config, dry_run)
    _print_run_summary(stats)
    duration = (datetime.now() - started_at).total_seconds()
    return stats, started_at, duration


def _execute_run(name: str, *, dry_run: bool = False, full: bool = False) -> None:
    """Run the job search pipeline for a single named user.

    Args:
        name: User name.
        dry_run: Skip persistence and notification.
        full: Bypass dedup, upsert results, re-notify matches.
    """
    from job_scout.llm.factory import get_llm_client  # noqa: PLC0415

    config = build_effective_config(name)

    # Check if schedule is paused
    if config.schedule_paused:
        logger.info(f"Job search skipped for '{name}': schedule is paused")
        return

    if not config.profile_description:
        click.echo(
            f"No profile for '{name}'. Run 'job-scout init --user {name}' first.",
            err=True,
        )
        return
    ok, err = check_llm_available(config)
    if not ok:
        logger.error(f"LLM not available for '{name}': {err}")
        return
    db = Database(user_db_path(name))
    cv_text = _load_cv_text(config)
    llm_client = get_llm_client(config)
    stats, started_at, duration = _run_pipeline(
        config, db, cv_text, dry_run=dry_run, full=full, llm_client=llm_client
    )
    # Save run history only for non-dry-run executions
    if not dry_run:
        db.save_run_stats(stats, started_at, duration)


def _execute_run_global(*, dry_run: bool = False, full: bool = False) -> None:
    """Run the job search pipeline with global config (backward-compat path).

    Args:
        dry_run: Skip persistence and notification.
        full: Bypass dedup, upsert results, re-notify matches.
    """
    from job_scout.llm.factory import get_llm_client  # noqa: PLC0415

    config = load_config()
    if not config.profile_description:
        click.echo("No profile configured. Run 'job-scout init' first.", err=True)
        sys.exit(1)
    ok, err = check_llm_available(config)
    if not ok:
        click.echo(err, err=True)
        sys.exit(1)
    db = Database(get_data_dir() / "jobs.db")
    cv_text = _load_cv_text(config)
    llm_client = get_llm_client(config)
    stats, started_at, duration = _run_pipeline(
        config, db, cv_text, dry_run=dry_run, full=full, llm_client=llm_client
    )
    # Save run history only for non-dry-run executions
    if not dry_run:
        db.save_run_stats(stats, started_at, duration)


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose (DEBUG) logging")
def cli(verbose: bool) -> None:
    """job-scout: Automated job search and filtering tool."""
    _setup_logging(verbose)


@cli.command()
@click.option("--user", "user_name", default=None, help="User name to initialize")
def init(user_name: str | None) -> None:
    """First-time setup: create config and prompt for settings."""
    if user_name:
        if user_name == "all":
            click.echo("'all' is reserved. Choose a different user name.", err=True)
            sys.exit(1)
        _init_user(user_name)
    else:
        _init_global()


def _init_global() -> None:
    """Initialize global config (no-user backward-compat path)."""
    data_dir = get_data_dir()
    click.echo(f"Initializing job-scout in {data_dir}")
    config = load_config()
    config = _prompt_init(config)
    save_config(config)
    click.echo(f"Config saved to {data_dir / 'config.yaml'}")
    no_keywords = not config.keywords_dutch and not config.keywords_english
    msg = "Generate keywords from profile now? (requires LLM)"
    if no_keywords and click.confirm(msg):
        _run_keywords_refresh(config)


def _init_user(name: str) -> None:
    """Initialize a new user directory with prompted config.

    Args:
        name: User name (must not be 'all').
    """
    from job_scout.config import load_secrets

    udir = user_dir(name)
    udir.mkdir(parents=True, exist_ok=True)
    # Use only the user's own existing data so that a new user is always prompted
    # for their profile — avoids inheriting another user's global profile_description.
    user_data = load_user_config(name)
    config = Config(**{**load_secrets(), **user_data})
    config = _prompt_init(config)
    full_dump = config.model_dump()
    user_data = {k: v for k, v in full_dump.items() if k in USER_FIELDS}
    user_data["name"] = name
    save_user_config(name, user_data)
    secret_data = {k: str(v) for k, v in full_dump.items() if k in SECRET_FIELDS and v}
    if secret_data:
        update_secrets(secret_data)
        click.echo(f"API keys saved to {secrets_path()} (gitignored)")
    click.echo(f"User '{name}' initialized in {udir}")
    config_fresh = build_effective_config(name)
    no_kw = not config_fresh.keywords_dutch and not config_fresh.keywords_english
    if no_kw and click.confirm("Generate keywords now? (requires LLM)"):
        _run_keywords_refresh(config_fresh, user=name)


def _prompt_init(config: Config) -> Config:
    """Prompt for initial configuration values.

    Args:
        config: Existing config to update.

    Returns:
        Updated Config instance.
    """
    config.profile_description = click.prompt(
        "Profile description (desired roles/skills)",
        default=config.profile_description or "",
    )
    config.negative_description = click.prompt(
        "Negative description (what to avoid)",
        default=config.negative_description or "",
    )
    cv_path = click.prompt("Path to CV PDF", default=config.cv_path or "")
    if cv_path:
        if not Path(cv_path).exists():
            click.echo(f"Warning: file not found: {cv_path}", err=True)
        config.cv_path = cv_path
    cv_notes = click.prompt(
        "Extra experience not in CV (optional, leave blank to skip)",
        default=config.cv_notes or "",
    )
    config.cv_notes = cv_notes
    max_dist = click.prompt("Max distance from home in km (optional)", default="")
    if max_dist:
        config.max_distance_km = int(max_dist)
    min_sal = click.prompt("Minimum monthly salary EUR (optional)", default="")
    if min_sal:
        config.min_salary = int(min_sal)
    max_sal = click.prompt("Maximum monthly salary EUR (optional)", default="")
    if max_sal:
        config.max_salary = int(max_sal)
    min_vac = click.prompt("Minimum vacation days/year (optional)", default="")
    if min_vac:
        config.min_vacation_days = int(min_vac)
    config.ntfy_topic = click.prompt("ntfy.sh topic", default=config.ntfy_topic)
    ors_key = click.prompt("OpenRouteService API key (optional)", default="")
    if ors_key:
        config.ors_api_key = ors_key
    ns_key = click.prompt("NS API key (optional)", default="")
    if ns_key:
        config.ns_api_key = ns_key
    return config


@cli.command()
@click.option("--dry-run", is_flag=True, help="Evaluate without saving or notifying")
@click.option("--user", "user_name", default=None, help="Run for a specific user")
@click.option("--all", "all_users", is_flag=True, help="Run for all users")
@click.option("--full", is_flag=True, help="Re-scrape and re-notify all matches")
def run(dry_run: bool, user_name: str | None, all_users: bool, full: bool) -> None:
    """Execute a full job search cycle."""
    users = list_users()
    if not users:
        sink_id = _add_file_sink(user_logs_dir(None))
        try:
            _execute_run_global(dry_run=dry_run, full=full)
        finally:
            logger.remove(sink_id)
        return
    if user_name:
        if user_name not in users:
            click.echo(
                f"User {user_name!r} not found. Available: {', '.join(users)}", err=True
            )
            sys.exit(1)
        targets = [user_name]
    elif all_users or len(users) == 1:
        targets = users
    else:
        click.echo(
            f"Multiple users ({', '.join(users)}). Pass --user NAME or --all.", err=True
        )
        sys.exit(1)
    for name in targets:
        sink_id = _add_file_sink(user_logs_dir(name))
        try:
            logger.info(f"=== Run for user '{name}' ===")
            _execute_run(name, dry_run=dry_run, full=full)
        except Exception:
            logger.exception(f"Run failed for user '{name}'")
        finally:
            logger.remove(sink_id)


def _print_run_summary(stats: RunStats) -> None:
    """Print a tabular run summary to stdout.

    Args:
        stats: Statistics from the completed run.
    """
    click.echo("\nRun complete:")
    click.echo(f"  Scraped:        {stats.scraped}")
    click.echo(f"  Deduplicated:   {stats.deduplicated}")
    click.echo(f"  Title filtered: {stats.title_filtered}")
    click.echo(f"  Title screened: {stats.title_screened}")
    click.echo(f"  Quick filtered: {stats.quick_filtered}")
    click.echo(f"  Evaluated:      {stats.evaluated}")
    click.echo(f"  Matched:        {stats.matched}")
    click.echo(f"  Rejected:       {stats.rejected}")
    click.echo(f"  Notified:       {stats.notified}")
    if stats.errors:
        click.echo(f"  Errors:         {len(stats.errors)}")


@cli.group()
def keywords() -> None:
    """Manage search keywords."""


@keywords.command("refresh")
@click.option("--user", "user_name", default=None, help="User to refresh keywords for")
def keywords_refresh(user_name: str | None) -> None:
    """Regenerate search keywords from profile + CV using the configured LLM."""
    if user_name:
        if user_name not in list_users():
            click.echo(
                f"User '{user_name}' not found. "
                f"Run 'job-scout init --user {user_name}' first.",
                err=True,
            )
            sys.exit(1)
        user_cfg = load_user_config(user_name)
        if not user_cfg.get("profile_description"):
            click.echo(
                f"No profile configured for '{user_name}'. "
                f"Run 'job-scout init --user {user_name}' first.",
                err=True,
            )
            sys.exit(1)
        config = build_effective_config(user_name)
        ok, err = check_llm_available(config)
        if not ok:
            click.echo(err, err=True)
            sys.exit(1)
    else:
        config = _require_llm()
        if not config.profile_description:
            click.echo("No profile configured. Run 'job-scout init' first.", err=True)
            sys.exit(1)
    _run_keywords_refresh(config, user=user_name)


def _run_keywords_refresh(config: Config, *, user: str | None = None) -> None:
    """Core keyword refresh logic, shared between init and keywords refresh.

    Args:
        config: Current application configuration.
        user: Save keywords to this user's config; if None, saves to global config.
    """
    cv_text = _load_cv_text(config)
    click.echo("Generating keywords with LLM…")
    result = generate_keywords(config.profile_description, cv_text)
    kw_fields = {
        "keywords_dutch": result.dutch,
        "keywords_english": result.english,
        "title_include_keywords": result.title_include,
        "title_exclude_keywords": result.title_exclude,
    }
    if user:
        user_data = load_user_config(user)
        user_data.update(kw_fields)
        save_user_config(user, user_data)
    else:
        for k, v in kw_fields.items():
            setattr(config, k, v)
        save_config(config)
    click.echo(f"Dutch keywords ({len(result.dutch)}):   {', '.join(result.dutch)}")
    click.echo(f"English keywords ({len(result.english)}): {', '.join(result.english)}")
    click.echo(
        f"Title include ({len(result.title_include)}): "
        f"{', '.join(result.title_include)}"
    )
    click.echo(
        f"Title exclude ({len(result.title_exclude)}): "
        f"{', '.join(result.title_exclude)}"
    )


@cli.group()
def jobs() -> None:
    """View stored job listings."""


@jobs.command("list")
@click.option("--limit", default=20, show_default=True, help="Max results to show")
@click.option("--user", "user_name", default=None, help="User whose jobs to show")
def jobs_list(limit: int, user_name: str | None) -> None:
    """Show recent matching jobs."""
    target = _require_single_user(user_name)
    db = Database(user_db_path(target))
    matches = db.get_recent_matches(limit)
    if not matches:
        click.echo("No matching jobs found.")
        return
    for job in matches:
        _print_job(job)


@jobs.command("rejected")
@click.option("--limit", default=20, show_default=True, help="Max results to show")
@click.option("--user", "user_name", default=None, help="User whose jobs to show")
def jobs_rejected(limit: int, user_name: str | None) -> None:
    """Show recently rejected jobs with rejection reasons."""
    target = _require_single_user(user_name)
    db = Database(user_db_path(target))
    rejected = db.get_rejected_jobs(limit)
    if not rejected:
        click.echo("No rejected jobs found.")
        return
    for job in rejected:
        _print_rejected_job(job)


@jobs.command("update-status")
@click.argument("job_id", type=int)
@click.argument("status", type=click.Choice([s.value for s in JobStatus]))
@click.option("--notes", default=None, help="Optional notes for the status change")
@click.option("--user", "user_name", default=None, help="User whose job to update")
def jobs_update_status(
    job_id: int, status: str, notes: str | None, user_name: str | None
) -> None:
    """Update a job's lifecycle status.

    Args:
        job_id: ID of the job to update.
        status: New status for the job.
        notes: Optional notes to attach to the status update.
        user_name: User whose job to update (optional).
    """
    target = _require_single_user(user_name)
    db = Database(user_db_path(target))
    new_status = JobStatus(status)
    success = db.update_job_status(job_id, new_status, notes=notes)
    if success:
        msg = f"Updated job {job_id} status to {status.upper()}"
        if notes:
            msg += f" with notes: {notes}"
        click.echo(msg)
    else:
        click.echo(
            f"Failed to update job {job_id}: invalid transition or job not found"
        )


def _format_salary(job: JobListing) -> str:
    """Format salary range for display.

    Args:
        job: Job listing with salary fields.

    Returns:
        Human-readable salary string.
    """
    if job.salary_min is None and job.salary_max is None:
        return "Not specified"
    period = f"/{job.salary_period}" if job.salary_period else ""
    if job.salary_min == job.salary_max or job.salary_max is None:
        return f"€{job.salary_min}{period}"
    if job.salary_min is None:
        return f"€{job.salary_max}{period}"
    return f"€{job.salary_min}–{job.salary_max}{period}"


def _print_job(job: JobListing) -> None:
    """Print a matched job listing to stdout.

    Args:
        job: The job listing to display.
    """
    click.echo(f"\n{'=' * 60}")
    click.echo(f"Title:    {job.title}")
    click.echo(f"Company:  {job.company}")
    click.echo(f"Score:    {job.fit_score}/100 — {job.fit_reasoning}")
    click.echo(f"Salary:   {_format_salary(job)}")
    if job.compensation_reasoning:
        click.echo(f"Comp:     {job.compensation_reasoning}")
    if job.vacation_days is not None:
        click.echo(f"Vacation: {job.vacation_days} days/year")
    loc = job.location or "Unknown"
    if job.distance_km is not None:
        loc += f" ({job.distance_km} km)"
    click.echo(f"Location: {loc}")
    for tt in job.travel_times:
        if tt.available and tt.minutes is not None:
            click.echo(f"Travel ({tt.mode.value}): {int(tt.minutes)} min")
    click.echo(f"URL:      {job.url}")
    click.echo(f"Seen:     {job.seen_at.strftime('%Y-%m-%d %H:%M')}")


def _print_rejected_job(job: JobListing) -> None:
    """Print a rejected job listing with its rejection reason.

    Args:
        job: The rejected job listing to display.
    """
    click.echo(f"\n{'=' * 60}")
    click.echo(f"Title:   {job.title}")
    click.echo(f"Company: {job.company}")
    if job.negative_match:
        click.echo(f"Reason:  Negative match — {job.negative_reasoning}")
    elif job.fit_score is not None and job.fit_score < 60:
        click.echo(f"Reason:  Score {job.fit_score}/100 — {job.fit_reasoning}")
    else:
        reason_parts = []
        if job.compensation_reasoning:
            reason_parts.append(f"Compensation: {job.compensation_reasoning}")
        if reason_parts:
            click.echo(f"Reason:  {'; '.join(reason_parts)}")
        else:
            click.echo("Reason:  Travel, salary, or vacation filter")
    click.echo(f"URL:     {job.url}")


@cli.group()
def runs() -> None:
    """View run history and analytics."""


@runs.command("history")
@click.option("--limit", default=30, show_default=True, help="Max runs to show")
@click.option("--user", "user_name", default=None, help="User whose runs to show")
def runs_history(limit: int, user_name: str | None) -> None:
    """Show recent run history."""
    target = _require_single_user(user_name)
    db = Database(user_db_path(target))
    history = db.get_run_history(limit)
    if not history:
        click.echo("No run history found.")
        return
    click.echo(f"\n{'=' * 95}")
    click.echo(
        f"{'Date':<20} {'Scraped':<10} {'Matched':<10} {'Rejected':<10} "
        f"{'Notified':<10} {'Errors':<8} {'Duration':<12}"
    )
    click.echo(f"{'=' * 95}")
    for entry in history:
        date_str = entry.started_at.strftime("%Y-%m-%d %H:%M:%S")
        duration_str = f"{entry.duration_seconds:.1f}s"
        click.echo(
            f"{date_str:<20} {entry.scraped:<10} {entry.matched:<10} "
            f"{entry.rejected:<10} {entry.notified:<10} {entry.errors:<8} "
            f"{duration_str:<12}"
        )
    click.echo(f"{'=' * 95}")


@cli.group("config")
def config_group() -> None:
    """Manage configuration values."""


@config_group.command("show")
@click.option("--user", "user_name", default=None, help="Effective config for user")
def config_show(user_name: str | None) -> None:
    """Display the current configuration."""
    config = build_effective_config(user_name) if user_name else load_config()
    for key, val in config.model_dump().items():
        display = f"***{str(val)[-4:]}" if "key" in key.lower() and val else val
        click.echo(f"{key}: {display}")


@config_group.command("set")
@click.argument("key")
@click.argument("value")
@click.option("--user", "user_name", default=None, help="User to apply change to")
def config_set(key: str, value: str, user_name: str | None) -> None:
    """Set a configuration KEY to VALUE."""
    try:
        set_config_value(key, value, user=user_name)
        click.echo(f"Set {key} = {value}")
    except (ValueError, TypeError) as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)


@cli.group()
def sites() -> None:
    """Manage custom job-listing URLs to monitor per user."""


@sites.command("add")
@click.argument("url")
@click.option("--name", "site_name", default=None, help="Label for the site")
@click.option("--user", "user_name", default=None, help="User to configure")
def sites_add(url: str, site_name: str | None, user_name: str | None) -> None:
    """Add a custom site URL to monitor."""
    from urllib.parse import urlparse  # noqa: PLC0415

    target = _require_single_user(user_name)
    resolved_name = site_name or urlparse(url).hostname or url
    cfg = load_user_config(target)
    sites_list: list[dict[str, object]] = cfg.get("custom_sites", [])
    if any(s.get("url") == url for s in sites_list):
        click.echo(f"Already tracked: {url}")
        return
    sites_list.append({"name": resolved_name, "url": url, "enabled": True})
    cfg["custom_sites"] = sites_list
    save_user_config(target, cfg)
    click.echo(f"Added '{resolved_name}' ({url}) for user '{target}'")


@sites.command("list")
@click.option("--user", "user_name", default=None, help="User to show")
def sites_list_cmd(user_name: str | None) -> None:
    """List custom tracked sites for a user."""
    target = _require_single_user(user_name)
    cfg = load_user_config(target)
    sites_data: list[dict[str, object]] = cfg.get("custom_sites", [])
    if not sites_data:
        click.echo(f"No custom sites for user '{target}'.")
        return
    click.echo(f"Custom sites for '{target}':")
    for s in sites_data:
        status = "enabled" if s.get("enabled", True) else "disabled"
        click.echo(f"  [{status}] {s['name']}: {s['url']}")


@sites.command("remove")
@click.argument("identifier")
@click.option("--user", "user_name", default=None, help="User to configure")
def sites_remove(identifier: str, user_name: str | None) -> None:
    """Remove a custom site by URL or name."""
    target = _require_single_user(user_name)
    cfg = load_user_config(target)
    sites_data: list[dict[str, object]] = cfg.get("custom_sites", [])
    before = len(sites_data)
    sites_data = [
        s
        for s in sites_data
        if s.get("url") != identifier and s.get("name") != identifier
    ]
    if len(sites_data) == before:
        click.echo(f"No site matching '{identifier}' found.")
        return
    cfg["custom_sites"] = sites_data
    save_user_config(target, cfg)
    click.echo(f"Removed '{identifier}' for user '{target}'")


@cli.group("profile")
def profile_group() -> None:
    """Manage and view parsed CV profile information."""


@profile_group.command("cv-summary")
@click.option(
    "--user", "user_name", default=None, help="User to view (default: current user)"
)
def profile_cv_summary(user_name: str | None) -> None:
    """Display the structured CV profile (requires CV path and LLM parsing).

    Shows skills, years of experience, education, and past roles extracted
    from the candidate's CV using LLM-based parsing.
    """
    from job_scout.config import build_effective_config, user_db_path
    from job_scout.cv_parser import parse_cv
    from job_scout.cv_profile import get_or_parse_cv_profile

    target = _require_single_user(user_name)
    config = build_effective_config(target)

    if not config.cv_path:
        click.echo(
            f"No CV path configured. Run 'job-scout init --user {target}' first.",
            err=True,
        )
        sys.exit(1)

    # Parse raw CV text
    try:
        raw_cv_text = parse_cv(config.cv_path)
    except FileNotFoundError as e:
        click.echo(str(e), err=True)
        sys.exit(1)

    if not raw_cv_text:
        click.echo("Failed to extract text from CV file.", err=True)
        sys.exit(1)

    # Get LLM client
    try:
        client = get_llm_client(config)
    except LLMError as e:
        click.echo(f"LLM configuration error: {e}", err=True)
        sys.exit(1)

    # Check LLM availability
    ok, err = client.check_available()
    if not ok:
        click.echo(f"LLM not available: {err}", err=True)
        sys.exit(1)

    # Load or parse CV profile with caching
    db = Database(user_db_path(target))
    profile = get_or_parse_cv_profile(raw_cv_text, client, db)

    # Display the profile
    click.echo(f"\nCV Profile for '{target}':")
    click.echo("=" * 50)

    if profile.years_experience is not None:
        click.echo(f"Years of Experience: {profile.years_experience}")

    if profile.skills:
        click.echo(f"\nSkills ({len(profile.skills)}):")
        for skill in profile.skills:
            click.echo(f"  - {skill}")

    if profile.education:
        click.echo("\nEducation:")
        for edu in profile.education:
            click.echo(f"  - {edu}")

    if profile.past_roles:
        click.echo("\nPast Roles:")
        for role in profile.past_roles:
            dates = ""
            if role.start_date:
                dates = f" ({role.start_date}"
                if role.end_date:
                    dates += f" - {role.end_date})"
                else:
                    dates += " - present)"
            click.echo(f"  - {role.title} at {role.company}{dates}")
            if role.description:
                click.echo(f"    {role.description}")

    click.echo()


@profile_group.command("import-linkedin")
@click.option("--user", "user_name", default=None, help="User to import for")
@click.option(
    "--file",
    "export_file",
    default=None,
    type=click.Path(exists=True),
    help="Path to LinkedIn data export ZIP file",
)
@click.option(
    "--paste",
    "paste_mode",
    is_flag=True,
    help="Read profile text from stdin (paste from LinkedIn profile page)",
)
@click.option(
    "--url",
    "profile_url",
    default=None,
    help="LinkedIn profile URL (requires --allow-fetch)",
)
@click.option(
    "--allow-fetch",
    "allow_fetch",
    is_flag=True,
    help=(
        "Allow fetching profile from URL. WARNING: This may violate LinkedIn's ToS. "
        "Use at your own risk."
    ),
)
def profile_import_linkedin(
    user_name: str | None,
    export_file: str | None,
    paste_mode: bool,
    profile_url: str | None,
    allow_fetch: bool,
) -> None:
    """Import LinkedIn profile data to enrich CV profile.

    Three import methods:
    1. --file: LinkedIn data export ZIP (safest, from "Download your data")
    2. --paste: Paste LinkedIn profile page text (safe, manual)
    3. --url: Fetch from profile URL (risky, requires --allow-fetch, may violate ToS)

    The imported data fills gaps in the CV profile without overwriting existing data.
    """
    from job_scout.config import build_effective_config, user_db_path
    from job_scout.cv_parser import parse_cv
    from job_scout.cv_profile import get_or_parse_cv_profile
    from job_scout.linkedin_import import (
        LinkedInProfileImporter,
        compute_linkedin_hash,
        merge_linkedin_into_profile,
    )

    target = _require_single_user(user_name)
    config = build_effective_config(target)
    db = Database(user_db_path(target))

    # Get current CV profile
    if not config.cv_path:
        click.echo(
            f"No CV path configured. Run 'job-scout init --user {target}' first.",
            err=True,
        )
        sys.exit(1)

    try:
        raw_cv_text = parse_cv(config.cv_path)
    except FileNotFoundError as e:
        click.echo(str(e), err=True)
        sys.exit(1)

    if not raw_cv_text:
        click.echo("Failed to extract text from CV file.", err=True)
        sys.exit(1)

    # Get LLM client for CV profile parsing
    try:
        client = get_llm_client(config)
    except LLMError as e:
        click.echo(f"LLM configuration error: {e}", err=True)
        sys.exit(1)

    ok, err = client.check_available()
    if not ok:
        click.echo(f"LLM not available: {err}", err=True)
        sys.exit(1)

    # Load current CV profile
    current_profile = get_or_parse_cv_profile(raw_cv_text, client, db)

    # Import LinkedIn data based on method
    linkedin_data: dict[str, list[Any]] = {}

    if export_file:
        click.echo(f"Parsing LinkedIn export ZIP: {export_file}")
        try:
            linkedin_data = LinkedInProfileImporter.parse_export(export_file)
        except Exception as e:
            click.echo(f"Failed to parse export: {e}", err=True)
            sys.exit(1)

    elif paste_mode:
        click.echo(
            "Paste LinkedIn profile text (profile page or PDF export), "
            "then press Ctrl+D (Unix) or Ctrl+Z+Enter (Windows):"
        )
        try:
            pasted_text = sys.stdin.read()
        except KeyboardInterrupt:
            click.echo("Cancelled.", err=True)
            sys.exit(1)

        if not pasted_text.strip():
            click.echo("No text provided.", err=True)
            sys.exit(1)

        linkedin_data = LinkedInProfileImporter.parse_pasted_text(pasted_text)

    elif profile_url:
        if not allow_fetch:
            click.echo(
                "LinkedIn URL fetch requires --allow-fetch flag. "
                "This may violate LinkedIn's ToS. Use at your own risk.",
                err=True,
            )
            sys.exit(1)

        click.echo(f"Fetching LinkedIn profile from {profile_url}...")
        try:
            linkedin_data = LinkedInProfileImporter.fetch_profile_url(
                profile_url, allow_fetch=True
            )
        except Exception as e:
            click.echo(f"Failed to fetch profile: {e}", err=True)
            sys.exit(1)

    else:
        click.echo(
            "Must provide one of: --file, --paste, or --url (with --allow-fetch)",
            err=True,
        )
        sys.exit(1)

    if not linkedin_data or not any(
        linkedin_data.get(k) for k in ("skills", "education", "past_roles")
    ):
        click.echo("No data extracted from LinkedIn.", err=True)
        sys.exit(1)

    # Merge and show diff
    merged_profile, diff = merge_linkedin_into_profile(current_profile, linkedin_data)

    click.echo("\n" + "=" * 50)
    click.echo("Proposed changes:")
    click.echo("=" * 50)

    added_skills = diff.get("added_skills", [])
    if added_skills:
        click.echo(f"\nNew Skills ({len(added_skills)}):")
        for skill in added_skills:
            click.echo(f"  + {skill}")

    added_education = diff.get("added_education", [])
    if added_education:
        click.echo(f"\nNew Education ({len(added_education)}):")
        for edu in added_education:
            click.echo(f"  + {edu}")

    added_roles = diff.get("added_roles", [])
    if added_roles:
        click.echo(f"\nNew Roles ({len(added_roles)}):")
        for role in added_roles:
            role_dict = role if isinstance(role, dict) else role.model_dump()
            dates = ""
            if role_dict.get("start_date"):
                dates = f" ({role_dict['start_date']}"
                if role_dict.get("end_date"):
                    dates += f" - {role_dict['end_date']})"
                else:
                    dates += " - present)"
            click.echo(f"  + {role_dict['title']} at {role_dict['company']}{dates}")

    click.echo("\n" + "=" * 50)
    if click.confirm("Apply these changes?"):
        # Save merged profile to cache
        try:
            from job_scout.cv_parser import compute_cv_hash

            cv_hash = compute_cv_hash(raw_cv_text)
            cache_json = json.dumps(merged_profile.model_dump())
            db.save_cv_profile_cache(cv_hash, cache_json)

            # Also cache the LinkedIn data for audit
            linkedin_hash = compute_linkedin_hash(linkedin_data)
            db.save_cv_profile_cache(
                f"linkedin_{linkedin_hash}", json.dumps(linkedin_data)
            )

            click.echo("Profile updated successfully!")
        except Exception as e:
            click.echo(f"Failed to save profile: {e}", err=True)
            sys.exit(1)
    else:
        click.echo("Cancelled.", err=True)
        sys.exit(1)


@profile_group.command("tailor-resume")
@click.argument("job_id", type=int)
@click.option("--user", "user_name", default=None, help="User to tailor for")
@click.option(
    "--output",
    "output_pdf",
    default=None,
    type=click.Path(),
    help="Path to save PDF (e.g., ~/tailored_resume.pdf)",
)
def profile_tailor_resume(
    job_id: int, user_name: str | None, output_pdf: str | None
) -> None:
    """Tailor a resume for a specific approved job.

    Generates a resume customized for the target job by highlighting
    relevant skills and experience. Job must be APPROVED or later in
    the application lifecycle.

    Can optionally generate a PDF version of the tailored resume.
    """
    from pathlib import Path

    from job_scout.config import build_effective_config, user_db_path
    from job_scout.cv_parser import parse_cv
    from job_scout.cv_profile import get_or_parse_cv_profile
    from job_scout.database import Database
    from job_scout.llm.factory import get_llm_client
    from job_scout.models import JobStatus
    from job_scout.resume_tailor import (
        generate_resume_pdf,
        tailor_resume_text,
    )

    target = _require_single_user(user_name)
    config = build_effective_config(target)
    db = Database(user_db_path(target))

    # Fetch the job
    job = db.get_job(job_id)
    if not job:
        click.echo(f"Job #{job_id} not found.", err=True)
        sys.exit(1)

    # Check job status - must be approved or later
    if job.status not in [
        JobStatus.APPROVED,
        JobStatus.READY,
        JobStatus.SUBMITTED,
        JobStatus.INTERVIEWING,
        JobStatus.OFFER,
    ]:
        click.echo(
            f"Job #{job_id} has status {job.status.value}. "
            f"Must be APPROVED or later to tailor resume.",
            err=True,
        )
        sys.exit(1)

    # Get CV and profile
    if not config.cv_path:
        click.echo(
            f"No CV path configured. Run 'job-scout init --user {target}' first.",
            err=True,
        )
        sys.exit(1)

    try:
        raw_cv_text = parse_cv(config.cv_path)
    except FileNotFoundError as e:
        click.echo(str(e), err=True)
        sys.exit(1)

    if not raw_cv_text:
        click.echo("Failed to extract text from CV file.", err=True)
        sys.exit(1)

    try:
        client = get_llm_client(config)
    except LLMError as e:
        click.echo(f"LLM configuration error: {e}", err=True)
        sys.exit(1)

    ok, err = client.check_available()
    if not ok:
        click.echo(f"LLM not available: {err}", err=True)
        sys.exit(1)

    cv_profile = get_or_parse_cv_profile(raw_cv_text, client, db)

    if not job.description:
        click.echo(
            f"Job #{job_id} has no description. Cannot tailor resume.",
            err=True,
        )
        sys.exit(1)

    # Check if already tailored
    existing = db.get_tailored_resume(job_id)
    if existing:
        click.echo(
            f"Resume already tailored for job #{job_id}. Use --force to regenerate.",
        )
        if click.confirm("Regenerate?"):
            tailored = tailor_resume_text(
                raw_cv_text, cv_profile, job.description, client=client
            )
        else:
            tailored = existing
    else:
        # Tailor the resume
        click.echo(f"Tailoring resume for: {job.title} @ {job.company}...")
        tailored = tailor_resume_text(
            raw_cv_text, cv_profile, job.description, client=client
        )

    # Save to database
    db.save_tailored_resume(job_id, tailored)
    click.echo("Resume tailored and saved.")

    # Generate PDF if requested
    if output_pdf:
        output_path = Path(output_pdf).expanduser()
        click.echo(f"Generating PDF: {output_path}")
        try:
            generate_resume_pdf(tailored, output_path=output_path)
            click.echo(f"PDF saved to {output_path}")
        except OSError as e:
            click.echo(f"Failed to generate PDF: {e}", err=True)
            sys.exit(1)
    else:
        click.echo(
            "\nTailored Resume Preview:\n"
            + "=" * 50
            + f"\n{tailored[:500]}...\n"
            + "(truncated; use --output to generate full PDF)"
        )


@profile_group.command("get-resume")
@click.argument("job_id", type=int)
@click.option("--user", "user_name", default=None, help="User to retrieve for")
@click.option(
    "--output",
    "output_pdf",
    default=None,
    type=click.Path(),
    help="Path to save PDF",
)
def profile_get_resume(
    job_id: int, user_name: str | None, output_pdf: str | None
) -> None:
    """Display or export a previously tailored resume.

    Retrieves a tailored resume that was previously generated for a job,
    optionally exporting it as a PDF.
    """
    from pathlib import Path

    from job_scout.config import user_db_path
    from job_scout.database import Database
    from job_scout.resume_tailor import generate_resume_pdf

    target = _require_single_user(user_name)
    db = Database(user_db_path(target))

    # Fetch the tailored resume
    tailored = db.get_tailored_resume(job_id)
    if not tailored:
        click.echo(f"No tailored resume found for job #{job_id}.", err=True)
        sys.exit(1)

    if output_pdf:
        output_path = Path(output_pdf).expanduser()
        click.echo(f"Generating PDF: {output_path}")
        try:
            generate_resume_pdf(tailored, output_path=output_path)
            click.echo(f"PDF saved to {output_path}")
        except OSError as e:
            click.echo(f"Failed to generate PDF: {e}", err=True)
            sys.exit(1)
    else:
        click.echo(f"\nTailored Resume for Job #{job_id}:\n" + "=" * 50)
        click.echo(tailored)


@cli.group("approval")
def approval_group() -> None:
    """Manage job application approvals."""


@approval_group.command("queue")
@click.option("--user", "user_name", default=None, help="User whose queue to show")
def approval_queue(user_name: str | None) -> None:
    """Show jobs awaiting approval."""
    user_name = _require_single_user(user_name)
    db = _get_db()
    queue = db.get_approval_queue()

    if not queue:
        click.echo("No jobs awaiting approval.")
        return

    click.echo(f"\n{len(queue)} job(s) awaiting approval:\n")
    for idx, job in enumerate(queue, 1):
        click.echo(f"{idx}. {job.title} @ {job.company}")
        click.echo(f"   Status: {job.status}")
        click.echo(f"   Fit score: {job.fit_score or 'N/A'}")
        click.echo(f"   URL: {job.url}")
        click.echo()


@approval_group.command("approve")
@click.argument("job_id", type=int)
@click.option("--notes", default=None, help="Approval notes")
@click.option("--user", "user_name", default=None, help="User approving")
def approval_approve(job_id: int, notes: str | None, user_name: str | None) -> None:
    """Approve a job for application."""
    from job_scout.models import JobStatus

    user_name = _require_single_user(user_name)
    db = _get_db()

    if not db.update_job_status(job_id, JobStatus.APPROVED):
        click.echo(
            f"Failed to approve job {job_id}. Invalid status transition.",
            err=True,
        )
        return

    db.approve_job(job_id, user_name, notes)
    click.echo(f"Job {job_id} approved by {user_name}")


@cli.group("schedule")
def schedule_group() -> None:
    """Manage the automated daily run schedule."""


@schedule_group.command("install")
@click.option("--hour", default=8, show_default=True, help="Hour to run (0-23)")
@click.option("--minute", default=0, show_default=True, help="Minute to run (0-59)")
@click.option(
    "--days",
    default="1-5",
    show_default=True,
    help="Day-of-week (cron syntax, e.g. '1-5' for Mon-Fri, '*' for daily)",
)
@click.option(
    "--user", "user_name", default=None, help="User to schedule (None for global)"
)
def schedule_install(hour: int, minute: int, days: str, user_name: str | None) -> None:
    """Install a daily cron job for job-scout run."""
    try:
        install_schedule(hour=hour, minute=minute, days=days, user=user_name)
        subject = user_name or "global"
        click.echo(
            f"Schedule installed for {subject}: "
            f"daily at {hour:02d}:{minute:02d} on days {days}"
        )
    except RuntimeError as exc:
        click.echo(f"Failed to install schedule: {exc}", err=True)
        sys.exit(1)


@schedule_group.command("status")
@click.option(
    "--user", "user_name", default=None, help="User to check (None for global)"
)
def schedule_status(user_name: str | None) -> None:
    """Show whether a cron schedule is currently installed."""
    click.echo(check_schedule_status(user=user_name))


@schedule_group.command("remove")
@click.option(
    "--user", "user_name", default=None, help="User to remove (None for global)"
)
def schedule_remove(user_name: str | None) -> None:
    """Remove the daily cron job for job-scout run."""
    try:
        remove_schedule(user=user_name)
        subject = user_name or "global"
        click.echo(f"Schedule removed successfully for {subject}")
    except RuntimeError as exc:
        click.echo(f"Failed to remove schedule: {exc}", err=True)
        sys.exit(1)


@cli.command()
@click.option("--host", default="0.0.0.0", help="Host to bind to (default 0.0.0.0)")
@click.option("--port", default=8000, type=int, help="Port to bind to (default 8000)")
def web(host: str, port: int) -> None:
    """Start the job-scout web dashboard.

    By default, the dashboard runs with NO authentication and is reachable
    from anyone on the network. Set JOB_SCOUT_DASHBOARD_TOKEN environment
    variable or data/secrets.yaml to enable optional token authentication.
    Use firewall rules or a VPN to restrict access.
    """
    from job_scout.web.app import run_server  # noqa: PLC0415

    run_server(host=host, port=port)


def main() -> None:
    """Main entry point for the job-scout CLI."""
    try:
        cli()
    except SystemExit:
        raise
    except Exception:
        try:
            logger.exception("Unhandled exception in job-scout")
        except Exception:
            import traceback

            traceback.print_exc()
        sys.exit(1)
