"""Factory that builds the correct Notifier from config."""

from __future__ import annotations

from typing import TYPE_CHECKING

from job_scout.notify.base import NotificationError, Notifier
from job_scout.notify.discord import DiscordNotifier
from job_scout.notify.email import EmailNotifier
from job_scout.notify.ntfy import NtfyNotifier
from job_scout.notify.slack import SlackNotifier

if TYPE_CHECKING:
    from job_scout.models import Config


def get_notifier(config: Config) -> Notifier:
    """Return the Notifier configured by *config*.

    Args:
        config: Application configuration.

    Returns:
        A ready-to-use Notifier instance.

    Raises:
        NotificationError: If the selected channel is misconfigured.
    """
    channel = getattr(config, "notification_channel", "ntfy")

    if channel == "ntfy":
        return NtfyNotifier(
            topic=config.ntfy_topic,
            server=config.ntfy_server,
        )
    elif channel == "email":
        smtp_username = getattr(config, "smtp_username", None)
        smtp_password = getattr(config, "smtp_password", None)
        return EmailNotifier(
            smtp_host=getattr(config, "smtp_host", ""),
            smtp_port=getattr(config, "smtp_port", 587),
            smtp_from=getattr(config, "smtp_from", ""),
            smtp_to=getattr(config, "smtp_to", ""),
            smtp_username=smtp_username,
            smtp_password=smtp_password,
        )
    elif channel == "slack":
        return SlackNotifier(
            webhook_url=getattr(config, "slack_webhook_url", ""),
        )
    elif channel == "discord":
        return DiscordNotifier(
            webhook_url=getattr(config, "discord_webhook_url", ""),
        )
    else:
        raise NotificationError(
            f"Unknown notification channel: {channel!r}. "
            f"Valid options: ntfy, email, slack, discord"
        )


def build_raw_notifier_for_test(
    channel: str,
    **kwargs: object,
) -> Notifier:
    """Build a notifier for a specific channel with explicit parameters.

    This is a helper for testing a candidate channel configuration that hasn't
    been saved yet (e.g., the web dashboard's test notification endpoint).

    Args:
        channel: Channel name (ntfy, email, slack, discord).
        **kwargs: Channel-specific configuration.

    Returns:
        A raw Notifier instance.

    Raises:
        NotificationError: If the channel is misconfigured.
    """
    if channel == "ntfy":
        return NtfyNotifier(
            topic=str(kwargs.get("ntfy_topic", "")),
            server=str(kwargs.get("ntfy_server", "https://ntfy.sh")),
        )
    elif channel == "email":
        smtp_port_val = kwargs.get("smtp_port", 587)
        smtp_port = int(smtp_port_val) if isinstance(smtp_port_val, (int, str)) else 587
        return EmailNotifier(
            smtp_host=str(kwargs.get("smtp_host", "")),
            smtp_port=smtp_port,
            smtp_from=str(kwargs.get("smtp_from", "")),
            smtp_to=str(kwargs.get("smtp_to", "")),
            smtp_username=str(kwargs.get("smtp_username", "")) or None,
            smtp_password=str(kwargs.get("smtp_password", "")) or None,
        )
    elif channel == "slack":
        return SlackNotifier(
            webhook_url=str(kwargs.get("slack_webhook_url", "")),
        )
    elif channel == "discord":
        return DiscordNotifier(
            webhook_url=str(kwargs.get("discord_webhook_url", "")),
        )
    else:
        raise NotificationError(
            f"Unknown notification channel: {channel!r}. "
            f"Valid options: ntfy, email, slack, discord"
        )
