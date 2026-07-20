"""Ntfy.sh notification provider."""

from __future__ import annotations

import requests
from loguru import logger

from job_scout.models import JobListing, TravelMode
from job_scout.notify.base import NotificationError


def _format_salary_summary(job: JobListing) -> str:
    """Build a concise salary string for the notification body.

    Args:
        job: Job listing with salary fields populated.

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


def _format_travel_summary(job: JobListing) -> str:
    """Build a concise travel-time string for the notification body.

    Args:
        job: Job listing with travel_times populated.

    Returns:
        Human-readable travel summary string.
    """
    if job.location_unknown:
        return "Location unknown — travel time not calculated"
    if not job.travel_times:
        return "Travel time not available"

    preferred_order = [TravelMode.PUBLIC_TRANSPORT, TravelMode.BIKE, TravelMode.CAR]
    labels = {
        TravelMode.PUBLIC_TRANSPORT: "PT",
        TravelMode.BIKE: "Bike",
        TravelMode.CAR: "Car",
    }
    parts = [
        f"{labels[tt.mode]}: {int(tt.minutes)}min"
        for mode in preferred_order
        for tt in job.travel_times
        if tt.mode == mode and tt.available and tt.minutes is not None
    ]
    return " | ".join(parts) if parts else "Travel time not available"


def _build_notification_payload(job: JobListing) -> tuple[str, str]:
    """Build title and body for an ntfy notification.

    Args:
        job: The matched job listing.

    Returns:
        Tuple of (title, body) strings.
    """
    title = f"{job.title} @ {job.company}"
    lines = [
        f"Score: {job.fit_score}/100 — {job.fit_reasoning}",
        f"Salary: {_format_salary_summary(job)}",
    ]
    if job.vacation_days is not None:
        lines.append(f"Vacation: {job.vacation_days} days/year")
    lines.extend(
        [
            f"Location: {job.location or 'Not specified'}",
            f"Travel: {_format_travel_summary(job)}",
            f"Source: {job.source}",
        ]
    )
    if job.official_url:
        status = (
            "still open"
            if job.official_available
            else "possibly filled"
            if job.official_available is False
            else "unverified"
        )
        lines.append(f"Employer page ({status}): {job.official_url}")
    return title, "\n".join(lines)


def _build_digest_payload(jobs: list[JobListing]) -> tuple[str, str]:
    """Build title and body for a digest notification.

    Args:
        jobs: List of matched job listings.

    Returns:
        Tuple of (title, body) strings.
    """
    count = len(jobs)
    title = f"Daily Job Digest: {count} match{'es' if count != 1 else ''} found"

    lines = [
        f"Summary of {count} job(s) matched today:",
        "",
    ]

    top_job = max(jobs, key=lambda j: j.fit_score or 0) if jobs else None
    for job in jobs:
        marker = " ⭐ TOP PICK" if job == top_job else ""
        lines.append(f"• {job.title} @ {job.company} ({job.fit_score}/100){marker}")

    lines.extend(
        [
            "",
            "View all matches in the dashboard or your notifications app.",
        ]
    )
    return title, "\n".join(lines)


class NtfyNotifier:
    """Notifier for ntfy.sh push notifications."""

    def __init__(self, topic: str, server: str) -> None:
        """Initialize the ntfy notifier.

        Args:
            topic: ntfy topic name.
            server: ntfy server URL (e.g., https://ntfy.sh).
        """
        self._topic = topic
        self._server = server

    def send(self, job: JobListing) -> None:
        """Send a push notification for a job via ntfy.sh.

        Args:
            job: The job listing to notify about.

        Raises:
            NotificationError: If the send fails.
        """
        title, body = _build_notification_payload(job)
        url = f"{self._server.rstrip('/')}/{self._topic}"

        try:
            resp = requests.post(
                url,
                data=body.encode("utf-8"),
                headers={
                    "Title": title.encode("utf-8").decode("latin-1"),
                    "Click": job.url,
                    "Priority": "default",
                    "Tags": "briefcase",
                },
                timeout=10,
            )
            resp.raise_for_status()
            logger.info(f"Notification sent via ntfy: {job.title} @ {job.company}")
        except requests.RequestException as e:
            raise NotificationError(
                f"ntfy notification failed for '{job.title}': {e}"
            ) from e

    def send_digest(self, jobs: list[JobListing]) -> None:
        """Send a digest notification for multiple jobs via ntfy.sh.

        Args:
            jobs: List of job listings to summarize.

        Raises:
            NotificationError: If the send fails.
        """
        if not jobs:
            return
        title, body = _build_digest_payload(jobs)
        url = f"{self._server.rstrip('/')}/{self._topic}"

        try:
            resp = requests.post(
                url,
                data=body.encode("utf-8"),
                headers={
                    "Title": title.encode("utf-8").decode("latin-1"),
                    "Priority": "default",
                    "Tags": "briefcase",
                },
                timeout=10,
            )
            resp.raise_for_status()
            logger.info(f"Digest notification sent via ntfy: {len(jobs)} jobs")
        except requests.RequestException as e:
            raise NotificationError(f"ntfy digest notification failed: {e}") from e

    def check_available(self) -> tuple[bool, str | None]:
        """Check if ntfy is configured.

        Returns:
            (True, None) if configured, (False, error_message) otherwise.
        """
        if not self._topic:
            return False, "ntfy_topic is not configured"
        if not self._server:
            return False, "ntfy_server is not configured"
        return True, None
