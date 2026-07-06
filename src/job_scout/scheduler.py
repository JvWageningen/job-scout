"""Cron schedule management for automated daily runs."""

from __future__ import annotations

import shutil
import subprocess
import sys

from loguru import logger

_CRON_MARKER = "# job-scout-managed"


def _find_job_scout_cmd() -> str:
    """Return the absolute path to the job-scout binary, or a fallback.

    Returns:
        Shell command string to invoke job-scout.
    """
    path = shutil.which("job-scout")
    if path:
        return path
    return f"{sys.executable} -m job_scout"


def _get_marker_for_user(user: str | None = None) -> str:
    """Return the cron marker string for a given user.

    For backward compatibility, the global schedule (no user) uses the plain marker.
    Per-user schedules embed the username in the marker.

    Args:
        user: User name, or None for global schedule.

    Returns:
        Cron marker string.
    """
    if user is None:
        return _CRON_MARKER
    return f"{_CRON_MARKER}:{user}"


def install_schedule(
    hour: int = 8, minute: int = 0, days: str = "1-5", user: str | None = None
) -> None:
    """Install a daily cron job for a user or globally.

    For a given user, installs a per-user schedule running 'job-scout run --user
    <name>'.
    If user is None (backward compatible), installs a global schedule running
    'job-scout run --all'.

    Args:
        hour: Hour of day to run (0-23).
        minute: Minute of the hour to run (0-59).
        days: Day-of-week specification (cron syntax, e.g., "1-5" for Mon-Fri).
        user: User name for per-user schedule, or None for global.

    Raises:
        RuntimeError: If crontab cannot be updated.
    """
    cmd = _find_job_scout_cmd()
    from job_scout.config import get_data_dir, user_logs_dir

    marker = _get_marker_for_user(user)
    if user is None:
        log_path = get_data_dir() / "logs" / "cron.log"
        run_arg = "run --all"
    else:
        log_path = user_logs_dir(user) / "cron.log"
        run_arg = f"run --user {user}"

    cron_line = (
        f"{minute} {hour} * * {days} {cmd} {run_arg} >> {log_path} 2>&1 {marker}"
    )

    existing = _read_crontab()
    lines = [ln for ln in existing.splitlines() if marker not in ln]
    lines.append(cron_line)
    _write_crontab("\n".join(lines) + "\n")
    subject = user or "global"
    logger.info(
        f"Cron schedule installed for {subject}: "
        f"daily at {hour:02d}:{minute:02d} on days {days}"
    )


def remove_schedule(user: str | None = None) -> None:
    """Remove job-scout cron entries for a user or globally.

    Args:
        user: User name for per-user removal, or None to remove global schedule.
    """
    marker = _get_marker_for_user(user)
    existing = _read_crontab()
    lines = [ln for ln in existing.splitlines() if marker not in ln]
    _write_crontab("\n".join(lines) + "\n")
    logger.info(f"Cron schedule removed for {user or 'global'}")


def check_schedule_status(user: str | None = None) -> str:
    """Return a description of the current cron schedule status.

    Args:
        user: User name to check, or None to check global schedule.

    Returns:
        Human-readable status string.
    """
    marker = _get_marker_for_user(user)
    existing = _read_crontab()
    entries = [ln for ln in existing.splitlines() if marker in ln]
    if not entries:
        subject = f"user '{user}'" if user else "global"
        return f"No job-scout schedule installed for {subject}."
    return "Installed: " + entries[0].replace(marker, "").strip()


def _read_crontab() -> str:
    """Read the current user's crontab.

    Returns:
        Crontab text, or empty string if none exists.
    """
    result = subprocess.run(
        ["crontab", "-l"],
        capture_output=True,
        text=True,
    )
    return result.stdout if result.returncode == 0 else ""


def _write_crontab(content: str) -> None:
    """Write a new crontab for the current user.

    Args:
        content: Full crontab text to install.

    Raises:
        RuntimeError: If crontab write fails.
    """
    proc = subprocess.run(
        ["crontab", "-"],
        input=content,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"crontab write failed: {proc.stderr.strip()}")
