"""Slack notification provider via incoming webhooks."""

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


def _build_slack_payload(job: JobListing) -> dict[str, object]:
    """Build a Slack message payload for a job notification.

    Args:
        job: The matched job listing.

    Returns:
        Dictionary to send as JSON to Slack webhook.
    """
    return {
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{job.title} @ {job.company}",
                },
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Fit Score:*\n{job.fit_score}/100",
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Salary:*\n{_format_salary_summary(job)}",
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Location:*\n{job.location or 'Not specified'}",
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Travel:*\n{_format_travel_summary(job)}",
                    },
                ],
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Reasoning:* {job.fit_reasoning}",
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "View Job"},
                        "url": job.url,
                    }
                ],
            },
        ]
    }


def _build_slack_digest_payload(jobs: list[JobListing]) -> dict[str, object]:
    """Build a Slack message payload for a digest notification.

    Args:
        jobs: List of matched job listings.

    Returns:
        Dictionary to send as JSON to Slack webhook.
    """
    count = len(jobs)
    top_job = max(jobs, key=lambda j: j.fit_score or 0) if jobs else None

    job_sections = []
    for job in jobs:
        marker = " ⭐ TOP PICK" if job == top_job else ""
        job_sections.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*{job.title}* @ {job.company}\n"
                        f"Score: {job.fit_score}/100{marker}\n"
                        f"<{job.url}|View Job>"
                    ),
                },
            }
        )

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"Daily Job Digest: {count} match{'es' if count != 1 else ''}",
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"Summary of {count} job(s) matched today:",
            },
        },
    ]
    blocks.extend(job_sections)

    return {"blocks": blocks}


class SlackNotifier:
    """Notifier for Slack via incoming webhook."""

    def __init__(self, webhook_url: str) -> None:
        """Initialize the Slack notifier.

        Args:
            webhook_url: Slack incoming webhook URL.
        """
        self._webhook_url = webhook_url

    def send(self, job: JobListing) -> None:
        """Send a Slack message notification for a job.

        Args:
            job: The job listing to notify about.

        Raises:
            NotificationError: If the send fails.
        """
        payload = _build_slack_payload(job)

        try:
            resp = requests.post(
                self._webhook_url,
                data=json.dumps(payload),
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            resp.raise_for_status()
            logger.info(f"Slack notification sent: {job.title} @ {job.company}")
        except requests.RequestException as e:
            raise NotificationError(
                f"Slack notification failed for '{job.title}': {e}"
            ) from e

    def send_digest(self, jobs: list[JobListing]) -> None:
        """Send a Slack digest notification for multiple jobs.

        Args:
            jobs: List of job listings to summarize.

        Raises:
            NotificationError: If the send fails.
        """
        if not jobs:
            return
        payload = _build_slack_digest_payload(jobs)

        try:
            resp = requests.post(
                self._webhook_url,
                data=json.dumps(payload),
                headers={"Content-Type": "application/json"},
                timeout=10,
            )
            resp.raise_for_status()
            logger.info(f"Slack digest notification sent: {len(jobs)} jobs")
        except requests.RequestException as e:
            raise NotificationError(f"Slack digest notification failed: {e}") from e

    def check_available(self) -> tuple[bool, str | None]:
        """Check if Slack webhook is configured.

        Returns:
            (True, None) if configured, (False, error_message) otherwise.
        """
        if not self._webhook_url:
            return False, "slack_webhook_url is not configured"
        return True, None
