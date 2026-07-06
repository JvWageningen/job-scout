"""Email notification provider via SMTP."""

from __future__ import annotations

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

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


def _build_html_body(job: JobListing) -> str:
    """Build an HTML email body for a job notification.

    Args:
        job: The matched job listing.

    Returns:
        HTML string.
    """
    return f"""<html><body style="font-family: Arial, sans-serif;">
<h2>{job.title} @ {job.company}</h2>
<p><strong>Fit Score:</strong> {job.fit_score}/100</p>
<p><strong>Reasoning:</strong> {job.fit_reasoning}</p>
<p><strong>Salary:</strong> {_format_salary_summary(job)}</p>
(
            f'<p><strong>Vacation:</strong> {job.vacation_days} days/year</p>'
            if job.vacation_days
            else ''
        )
<p><strong>Location:</strong> {job.location or "Not specified"}</p>
<p><strong>Travel:</strong> {_format_travel_summary(job)}</p>
<p><strong>Source:</strong> {job.source}</p>
<p><a href="{job.url}">View Job Listing</a></p>
</body></html>"""


def _build_digest_html_body(jobs: list[JobListing]) -> str:
    """Build an HTML email body for a digest notification.

    Args:
        jobs: List of matched job listings.

    Returns:
        HTML string.
    """
    count = len(jobs)
    top_job = max(jobs, key=lambda j: j.fit_score or 0) if jobs else None

    job_rows = []
    for job in jobs:
        marker = " ⭐ TOP PICK" if job == top_job else ""
        row = f"""<tr style="border-bottom: 1px solid #eee;">
        <td style="padding: 10px;">{job.title}</td>
        <td style="padding: 10px;">{job.company}</td>
        <td style="padding: 10px;">{job.fit_score}/100{marker}</td>
        <td style="padding: 10px;"><a href="{job.url}">View</a></td>
    </tr>"""
        job_rows.append(row)

    return f"""<html><body style="font-family: Arial, sans-serif;">
<h2>Daily Job Digest: {count} match{"es" if count != 1 else ""} found</h2>
<p>Summary of {count} job(s) matched today:</p>
<table style="width: 100%; border-collapse: collapse;">
    <thead>
        <tr style="background-color: #f5f5f5;">
            <th style="padding: 10px; text-align: left;">Title</th>
            <th style="padding: 10px; text-align: left;">Company</th>
            <th style="padding: 10px; text-align: left;">Fit Score</th>
            <th style="padding: 10px; text-align: left;">Action</th>
        </tr>
    </thead>
    <tbody>
        {"".join(job_rows)}
    </tbody>
</table>
<p style="margin-top: 20px;">Check the dashboard for more details.</p>
</body></html>"""


class EmailNotifier:
    """Notifier for email via SMTP."""

    def __init__(
        self,
        smtp_host: str,
        smtp_port: int,
        smtp_from: str,
        smtp_to: str,
        smtp_username: str | None = None,
        smtp_password: str | None = None,
    ) -> None:
        """Initialize the email notifier.

        Args:
            smtp_host: SMTP server hostname.
            smtp_port: SMTP server port.
            smtp_from: Sender email address.
            smtp_to: Recipient email address.
            smtp_username: SMTP username (optional, for authentication).
            smtp_password: SMTP password (optional, for authentication).
        """
        self._smtp_host = smtp_host
        self._smtp_port = smtp_port
        self._smtp_from = smtp_from
        self._smtp_to = smtp_to
        self._smtp_username = smtp_username
        self._smtp_password = smtp_password

    def send(self, job: JobListing) -> None:
        """Send an email notification for a job.

        Args:
            job: The job listing to notify about.

        Raises:
            NotificationError: If the send fails.
        """
        subject = f"New Job Match: {job.title} @ {job.company}"
        html_body = _build_html_body(job)

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = self._smtp_from
            msg["To"] = self._smtp_to

            msg.attach(MIMEText(html_body, "html"))

            with smtplib.SMTP(self._smtp_host, self._smtp_port, timeout=10) as server:
                if self._smtp_username and self._smtp_password:
                    server.starttls()
                    server.login(self._smtp_username, self._smtp_password)
                server.send_message(msg)

            logger.info(
                f"Email notification sent to {self._smtp_to}: "
                f"{job.title} @ {job.company}"
            )
        except (smtplib.SMTPException, OSError) as e:
            raise NotificationError(
                f"Email notification failed for '{job.title}': {e}"
            ) from e

    def send_digest(self, jobs: list[JobListing]) -> None:
        """Send a digest email notification for multiple jobs.

        Args:
            jobs: List of job listings to summarize.

        Raises:
            NotificationError: If the send fails.
        """
        if not jobs:
            return
        count = len(jobs)
        subject = f"Daily Job Digest: {count} match{'es' if count != 1 else ''}"
        html_body = _build_digest_html_body(jobs)

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = self._smtp_from
            msg["To"] = self._smtp_to

            msg.attach(MIMEText(html_body, "html"))

            with smtplib.SMTP(self._smtp_host, self._smtp_port, timeout=10) as server:
                if self._smtp_username and self._smtp_password:
                    server.starttls()
                    server.login(self._smtp_username, self._smtp_password)
                server.send_message(msg)

            logger.info(f"Digest email sent to {self._smtp_to}: {count} jobs")
        except (smtplib.SMTPException, OSError) as e:
            raise NotificationError(f"Email digest notification failed: {e}") from e

    def check_available(self) -> tuple[bool, str | None]:
        """Check if email is configured.

        Returns:
            (True, None) if configured, (False, error_message) otherwise.
        """
        if not self._smtp_host:
            return False, "smtp_host is not configured"
        if not self._smtp_from:
            return False, "smtp_from is not configured"
        if not self._smtp_to:
            return False, "smtp_to is not configured"
        return True, None
