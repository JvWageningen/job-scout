"""Click CLI entry point for job-scout."""

from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import cast

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
from job_scout.llm.base import LLMClient
from job_scout.models import Config, JobListing, JobStatus, RunStats
from job_scout.notifier import send_notification
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
) -> JobListing:
    """Calculate travel times for a single job (for parallel execution).

    Args:
        job: Job to calculate travel times for.
        config: Application configuration.

    Returns:
        Job with travel_times, location_unknown, and distance_km populated.
    """
    if job.location:
        travel_times, location_unknown, distance = calculate_travel_times(
            job.location, config
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
            return _calculate_travel_for_job(job, config)

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
    """Send ntfy notifications for matched jobs and retry pending ones.

    Args:
        matched: Newly matched jobs to notify about.
        db: Database for updating notification state.
        config: Application configuration with ntfy settings.
        dry_run: Skip actual sending if True.

    Returns:
        Number of notifications successfully sent.
    """
    sent = 0
    for job in matched:
        if dry_run:
            click.echo(
                f"[DRY RUN] Would notify: {job.title} @ {job.company} ({job.fit_score})"
            )
            continue
        if send_notification(job, config):
            if job.id:
                db.mark_notified(job.id)
            sent += 1
        elif job.id:
            db.mark_notification_pending(job.id)
    if not dry_run:
        for pending in db.get_pending_notifications():
            if send_notification(pending, config) and pending.id:
                db.mark_notified(pending.id)
                sent += 1
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


@cli.group("schedule")
def schedule_group() -> None:
    """Manage the automated daily run schedule."""


@schedule_group.command("install")
@click.option("--hour", default=8, show_default=True, help="Hour to run (0-23)")
@click.option("--minute", default=0, show_default=True, help="Minute to run (0-59)")
def schedule_install(hour: int, minute: int) -> None:
    """Install a daily cron job for job-scout run."""
    try:
        install_schedule(hour, minute)
        click.echo(f"Schedule installed: daily at {hour:02d}:{minute:02d}")
    except RuntimeError as exc:
        click.echo(f"Failed to install schedule: {exc}", err=True)
        sys.exit(1)


@schedule_group.command("status")
def schedule_status() -> None:
    """Show whether a cron schedule is currently installed."""
    click.echo(check_schedule_status())


@schedule_group.command("remove")
def schedule_remove() -> None:
    """Remove the daily cron job for job-scout run."""
    try:
        remove_schedule()
        click.echo("Schedule removed successfully")
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
