"""Push notification delivery via ntfy.sh."""

from __future__ import annotations

import requests
from loguru import logger

from job_scout.models import Config, JobListing, TravelMode


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
    return title, "\n".join(lines)


def send_notification(job: JobListing, config: Config) -> bool:
    """Send a push notification for a matched job via ntfy.sh.

    Args:
        job: The matched job listing.
        config: Configuration with ntfy topic and server URL.

    Returns:
        True if the notification was delivered successfully.
    """
    title, body = _build_notification_payload(job)
    url = f"{config.ntfy_server.rstrip('/')}/{config.ntfy_topic}"

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
        logger.info(f"Notification sent: {job.title} @ {job.company}")
        return True
    except requests.RequestException as e:
        logger.warning(f"ntfy notification failed for '{job.title}': {e}")
        return False
