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


def install_schedule(hour: int = 8, minute: int = 0) -> None:
    """Install a daily cron job that runs job-scout at the given time.

    Replaces any existing job-scout cron entries.

    Args:
        hour: Hour of day to run (0-23).
        minute: Minute of the hour to run (0-59).

    Raises:
        RuntimeError: If crontab cannot be updated.
    """
    cmd = _find_job_scout_cmd()
    from job_scout.config import get_data_dir

    log_path = get_data_dir() / "logs" / "cron.log"
    cron_line = f"{minute} {hour} * * * {cmd} run >> {log_path} 2>&1 {_CRON_MARKER}"

    existing = _read_crontab()
    lines = [ln for ln in existing.splitlines() if _CRON_MARKER not in ln]
    lines.append(cron_line)
    _write_crontab("\n".join(lines) + "\n")
    logger.info(f"Cron schedule installed: daily at {hour:02d}:{minute:02d}")


def remove_schedule() -> None:
    """Remove any job-scout cron entries."""
    existing = _read_crontab()
    lines = [ln for ln in existing.splitlines() if _CRON_MARKER not in ln]
    _write_crontab("\n".join(lines) + "\n")
    logger.info("Cron schedule removed")


def check_schedule_status() -> str:
    """Return a description of the current cron schedule status.

    Returns:
        Human-readable status string.
    """
    existing = _read_crontab()
    entries = [ln for ln in existing.splitlines() if _CRON_MARKER in ln]
    if not entries:
        return "No job-scout schedule installed."
    return "Installed: " + entries[0].replace(_CRON_MARKER, "").strip()


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
