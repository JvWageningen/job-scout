"""Tests for notification providers and factory."""

from __future__ import annotations

import json
from unittest.mock import Mock, patch

import pytest

from job_scout.models import JobListing
from job_scout.notify.base import NotificationError
from job_scout.notify.discord import DiscordNotifier
from job_scout.notify.email import EmailNotifier
from job_scout.notify.factory import build_raw_notifier_for_test, get_notifier
from job_scout.notify.ntfy import NtfyNotifier
from job_scout.notify.slack import SlackNotifier


@pytest.fixture
def sample_job() -> JobListing:
    """Create a sample job for testing."""
    return JobListing(
        title="Software Engineer",
        company="Tech Corp",
        url="https://example.com/job/123",
        source="linkedin",
        fit_score=85,
        fit_reasoning="Great fit for your skills",
        salary_min=3000,
        salary_max=5000,
        salary_period="month",
        location="Amsterdam",
        vacation_days=25,
    )


class TestNtfyNotifier:
    """Tests for NtfyNotifier."""

    def test_send_success(self, sample_job: JobListing) -> None:
        """Test successful ntfy notification."""
        notifier = NtfyNotifier(topic="test-topic", server="https://ntfy.sh")

        with patch("job_scout.notify.ntfy.requests.post") as mock_post:
            mock_response = Mock()
            mock_response.raise_for_status = Mock()
            mock_post.return_value = mock_response

            notifier.send(sample_job)

            mock_post.assert_called_once()
            call_args = mock_post.call_args
            assert "ntfy.sh/test-topic" in call_args[0][0]

    def test_send_failure(self, sample_job: JobListing) -> None:
        """Test ntfy notification failure."""
        notifier = NtfyNotifier(topic="test-topic", server="https://ntfy.sh")

        with patch("job_scout.notify.ntfy.requests.post") as mock_post:
            import requests

            mock_response = Mock()
            mock_response.raise_for_status = Mock(
                side_effect=requests.RequestException("Network error")
            )
            mock_post.return_value = mock_response

            with pytest.raises(NotificationError):
                notifier.send(sample_job)

    def test_check_available_valid(self) -> None:
        """Test availability check with valid config."""
        notifier = NtfyNotifier(topic="test-topic", server="https://ntfy.sh")
        available, error = notifier.check_available()
        assert available is True
        assert error is None

    def test_check_available_missing_topic(self) -> None:
        """Test availability check with missing topic."""
        notifier = NtfyNotifier(topic="", server="https://ntfy.sh")
        available, error = notifier.check_available()
        assert available is False
        assert error is not None

    def test_send_digest_success(self, sample_job: JobListing) -> None:
        """Test successful ntfy digest notification."""
        notifier = NtfyNotifier(topic="test-topic", server="https://ntfy.sh")
        jobs = [sample_job, sample_job]

        with patch("job_scout.notify.ntfy.requests.post") as mock_post:
            mock_response = Mock()
            mock_response.raise_for_status = Mock()
            mock_post.return_value = mock_response

            notifier.send_digest(jobs)

            mock_post.assert_called_once()
            call_args = mock_post.call_args
            assert "ntfy.sh/test-topic" in call_args[0][0]

    def test_send_digest_empty_list(self) -> None:
        """Test ntfy digest notification with empty list."""
        notifier = NtfyNotifier(topic="test-topic", server="https://ntfy.sh")

        with patch("job_scout.notify.ntfy.requests.post") as mock_post:
            notifier.send_digest([])

            mock_post.assert_not_called()


class TestEmailNotifier:
    """Tests for EmailNotifier."""

    def test_send_success(self, sample_job: JobListing) -> None:
        """Test successful email notification."""
        notifier = EmailNotifier(
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_from="sender@example.com",
            smtp_to="recipient@example.com",
        )

        with patch("job_scout.notify.email.smtplib.SMTP") as mock_smtp:
            mock_conn = Mock()
            mock_smtp.return_value.__enter__.return_value = mock_conn

            notifier.send(sample_job)

            mock_conn.send_message.assert_called_once()

    def test_send_with_auth(self, sample_job: JobListing) -> None:
        """Test email notification with authentication."""
        notifier = EmailNotifier(
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_from="sender@example.com",
            smtp_to="recipient@example.com",
            smtp_username="user",
            smtp_password="pass",
        )

        with patch("job_scout.notify.email.smtplib.SMTP") as mock_smtp:
            mock_conn = Mock()
            mock_smtp.return_value.__enter__.return_value = mock_conn

            notifier.send(sample_job)

            mock_conn.starttls.assert_called_once()
            mock_conn.login.assert_called_once_with("user", "pass")

    def test_check_available_valid(self) -> None:
        """Test availability check with valid config."""
        notifier = EmailNotifier(
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_from="sender@example.com",
            smtp_to="recipient@example.com",
        )
        available, error = notifier.check_available()
        assert available is True
        assert error is None

    def test_check_available_missing_to(self) -> None:
        """Test availability check with missing recipient."""
        notifier = EmailNotifier(
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_from="sender@example.com",
            smtp_to="",
        )
        available, error = notifier.check_available()
        assert available is False
        assert error is not None

    def test_send_digest_success(self, sample_job: JobListing) -> None:
        """Test successful email digest notification."""
        notifier = EmailNotifier(
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_from="sender@example.com",
            smtp_to="recipient@example.com",
        )
        jobs = [sample_job, sample_job]

        with patch("job_scout.notify.email.smtplib.SMTP") as mock_smtp:
            mock_conn = Mock()
            mock_smtp.return_value.__enter__.return_value = mock_conn

            notifier.send_digest(jobs)

            mock_conn.send_message.assert_called_once()

    def test_send_digest_empty_list(self) -> None:
        """Test email digest notification with empty list."""
        notifier = EmailNotifier(
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_from="sender@example.com",
            smtp_to="recipient@example.com",
        )

        with patch("job_scout.notify.email.smtplib.SMTP") as mock_smtp:
            notifier.send_digest([])

            mock_smtp.assert_not_called()


class TestSlackNotifier:
    """Tests for SlackNotifier."""

    def test_send_success(self, sample_job: JobListing) -> None:
        """Test successful Slack notification."""
        notifier = SlackNotifier(webhook_url="https://hooks.slack.com/services/test")

        with patch("job_scout.notify.slack.requests.post") as mock_post:
            mock_response = Mock()
            mock_response.raise_for_status = Mock()
            mock_post.return_value = mock_response

            notifier.send(sample_job)

            mock_post.assert_called_once()
            call_args = mock_post.call_args
            payload = json.loads(call_args[1]["data"])
            assert "blocks" in payload

    def test_check_available_valid(self) -> None:
        """Test availability check with valid config."""
        notifier = SlackNotifier(webhook_url="https://hooks.slack.com/services/test")
        available, error = notifier.check_available()
        assert available is True
        assert error is None

    def test_check_available_missing_url(self) -> None:
        """Test availability check with missing webhook URL."""
        notifier = SlackNotifier(webhook_url="")
        available, error = notifier.check_available()
        assert available is False
        assert error is not None

    def test_send_digest_success(self, sample_job: JobListing) -> None:
        """Test successful Slack digest notification."""
        notifier = SlackNotifier(webhook_url="https://hooks.slack.com/services/test")
        jobs = [sample_job, sample_job]

        with patch("job_scout.notify.slack.requests.post") as mock_post:
            mock_response = Mock()
            mock_response.raise_for_status = Mock()
            mock_post.return_value = mock_response

            notifier.send_digest(jobs)

            mock_post.assert_called_once()
            call_args = mock_post.call_args
            payload = json.loads(call_args[1]["data"])
            assert "blocks" in payload

    def test_send_digest_empty_list(self) -> None:
        """Test Slack digest notification with empty list."""
        notifier = SlackNotifier(webhook_url="https://hooks.slack.com/services/test")

        with patch("job_scout.notify.slack.requests.post") as mock_post:
            notifier.send_digest([])

            mock_post.assert_not_called()


class TestDiscordNotifier:
    """Tests for DiscordNotifier."""

    def test_send_success(self, sample_job: JobListing) -> None:
        """Test successful Discord notification."""
        notifier = DiscordNotifier(
            webhook_url="https://discordapp.com/api/webhooks/test"
        )

        with patch("job_scout.notify.discord.requests.post") as mock_post:
            mock_response = Mock()
            mock_response.raise_for_status = Mock()
            mock_post.return_value = mock_response

            notifier.send(sample_job)

            mock_post.assert_called_once()
            call_args = mock_post.call_args
            payload = json.loads(call_args[1]["data"])
            assert "embeds" in payload

    def test_check_available_valid(self) -> None:
        """Test availability check with valid config."""
        notifier = DiscordNotifier(
            webhook_url="https://discordapp.com/api/webhooks/test"
        )
        available, error = notifier.check_available()
        assert available is True
        assert error is None

    def test_check_available_missing_url(self) -> None:
        """Test availability check with missing webhook URL."""
        notifier = DiscordNotifier(webhook_url="")
        available, error = notifier.check_available()
        assert available is False
        assert error is not None

    def test_send_digest_success(self, sample_job: JobListing) -> None:
        """Test successful Discord digest notification."""
        notifier = DiscordNotifier(
            webhook_url="https://discordapp.com/api/webhooks/test"
        )
        jobs = [sample_job, sample_job]

        with patch("job_scout.notify.discord.requests.post") as mock_post:
            mock_response = Mock()
            mock_response.raise_for_status = Mock()
            mock_post.return_value = mock_response

            notifier.send_digest(jobs)

            mock_post.assert_called_once()
            call_args = mock_post.call_args
            payload = json.loads(call_args[1]["data"])
            assert "embeds" in payload

    def test_send_digest_empty_list(self) -> None:
        """Test Discord digest notification with empty list."""
        notifier = DiscordNotifier(
            webhook_url="https://discordapp.com/api/webhooks/test"
        )

        with patch("job_scout.notify.discord.requests.post") as mock_post:
            notifier.send_digest([])

            mock_post.assert_not_called()


class TestFactory:
    """Tests for notification factory."""

    def test_get_notifier_ntfy(self, base_config):
        """Test getting ntfy notifier from factory."""
        base_config.notification_channel = "ntfy"
        notifier = get_notifier(base_config)
        assert isinstance(notifier, NtfyNotifier)

    def test_get_notifier_email(self, base_config):
        """Test getting email notifier from factory."""
        base_config.notification_channel = "email"
        base_config.smtp_host = "smtp.example.com"
        base_config.smtp_from = "sender@example.com"
        base_config.smtp_to = "recipient@example.com"
        notifier = get_notifier(base_config)
        assert isinstance(notifier, EmailNotifier)

    def test_get_notifier_slack(self, base_config):
        """Test getting Slack notifier from factory."""
        base_config.notification_channel = "slack"
        base_config.slack_webhook_url = "https://hooks.slack.com/services/test"
        notifier = get_notifier(base_config)
        assert isinstance(notifier, SlackNotifier)

    def test_get_notifier_discord(self, base_config):
        """Test getting Discord notifier from factory."""
        base_config.notification_channel = "discord"
        base_config.discord_webhook_url = "https://discordapp.com/api/webhooks/test"
        notifier = get_notifier(base_config)
        assert isinstance(notifier, DiscordNotifier)

    def test_get_notifier_invalid_channel(self, base_config):
        """Test getting notifier with invalid channel."""
        base_config.notification_channel = "invalid"
        with pytest.raises(NotificationError):
            get_notifier(base_config)

    def test_build_raw_notifier_for_test_ntfy(self) -> None:
        """Test building raw ntfy notifier."""
        notifier = build_raw_notifier_for_test(
            "ntfy",
            ntfy_topic="test",
            ntfy_server="https://ntfy.sh",
        )
        assert isinstance(notifier, NtfyNotifier)

    def test_build_raw_notifier_for_test_slack(self) -> None:
        """Test building raw Slack notifier."""
        notifier = build_raw_notifier_for_test(
            "slack",
            slack_webhook_url="https://hooks.slack.com/services/test",
        )
        assert isinstance(notifier, SlackNotifier)

    def test_build_raw_notifier_for_test_invalid(self) -> None:
        """Test building notifier with invalid channel."""
        with pytest.raises(NotificationError):
            build_raw_notifier_for_test("invalid")
