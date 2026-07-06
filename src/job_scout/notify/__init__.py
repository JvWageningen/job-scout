"""Pluggable notification channel providers."""

from job_scout.notify.base import NotificationError, Notifier
from job_scout.notify.factory import get_notifier

__all__ = [
    "NotificationError",
    "Notifier",
    "get_notifier",
]
