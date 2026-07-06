"""Discord notification provider via incoming webhooks."""

from __future__ import annotations

import json

import requests
from loguru import logger

from job_scout.models import JobListing, TravelMode
from job_scout.notify.base import NotificationError


def _format_salary_summary(job: JobListing) -> str:
    """Build a concise salary string.

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
    """Build a concise travel-time string.

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


def _build_discord_embed(job: JobListing) -> dict[str, object]:
    """Build a Discord embed for a job notification.

    Args:
        job: The matched job listing.

    Returns:
        Dictionary representing a Discord embed.
    """
    return {
        "title": f"{job.title} @ {job.company}",
        "description": job.fit_reasoning,
        "url": job.url,
        "color": 3447003,
        "fields": [
            {
                "name": "Fit Score",
                "value": f"{job.fit_score}/100",
                "inline": True,
            },
            {
                "name": "Salary",
                "value": _format_salary_summary(job),
                "inline": True,
            },
            {
                "name": "Location",
                "value": job.location or "Not specified",
                "inline": True,
            },
            {
                "name": "Travel",
                "value": _format_travel_summary(job),
                "inline": True,
            },
            {
                "name": "Source",
                "value": job.source,
                "inline": True,
            },
        ],
    }


class DiscordNotifier:
    """Notifier for Discord via incoming webhook."""

    def __init__(self, webhook_url: str) -> None:
        """Initialize the Discord notifier.

        Args:
            webhook_url: Discord webhook URL.
        """
        self._webhook_url = webhook_url

    def send(self, job: JobListing) -> None:
        """Send a Discord message notification for a job.

        Args:
            job: The job listing to notify about.

        Raises:
            NotificationError: If the send fails.
        """
        embed = _build_discord_embed(job)
        payload = {"embeds": [embed]}

        try:
            resp = requests.post(
                self._webhook_url,
                data=json.dumps(payload),
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            resp.raise_for_status()
            logger.info(f"Discord notification sent: {job.title} @ {job.company}")
        except requests.RequestException as e:
            raise NotificationError(
                f"Discord notification failed for '{job.title}': {e}"
            ) from e

    def check_available(self) -> tuple[bool, str | None]:
        """Check if Discord webhook is configured.

        Returns:
            (True, None) if configured, (False, error_message) otherwise.
        """
        if not self._webhook_url:
            return False, "discord_webhook_url is not configured"
        return True, None
