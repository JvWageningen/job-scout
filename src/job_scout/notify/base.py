"""Protocol and shared types for notification providers."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from job_scout.models import JobListing


class NotificationError(RuntimeError):
    """Raised when a notification send fails."""


@runtime_checkable
class Notifier(Protocol):
    """Common interface for notification channel providers."""

    def send(self, job: JobListing) -> None:
        """Send a notification for a job listing.

        Args:
            job: The job listing to notify about.

        Raises:
            NotificationError: If the send fails for any reason.
        """
        ...

    def check_available(self) -> tuple[bool, str | None]:
        """Check whether this notifier is ready to use.

        Returns:
            (True, None) if available, (False, error_message) otherwise.
            Does not make network calls; only checks configuration.
        """
        ...
