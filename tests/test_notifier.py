"""Tests for push notification formatting."""

from __future__ import annotations

from datetime import UTC, datetime

from job_scout.models import JobListing, TravelMode, TravelTime
from job_scout.notifier import (
    _build_notification_payload,
    _format_salary_summary,
    _format_travel_summary,
)


def _make_job(**kwargs: object) -> JobListing:
    """Create a minimal JobListing for notifier tests."""
    defaults: dict[str, object] = {
        "title": "Dev",
        "company": "Co",
        "url": "https://example.com/job",
        "source": "indeed",
        "fit_score": 80,
        "fit_reasoning": "Good match",
        "seen_at": datetime.now(UTC),
    }
    defaults.update(kwargs)
    return JobListing(**defaults)  # type: ignore[arg-type]


def test_format_travel_unknown_location() -> None:
    """Unknown location produces a descriptive 'not calculated' message."""
    job = _make_job(location_unknown=True)
    result = _format_travel_summary(job)
    assert "unknown" in result.lower()


def test_format_travel_no_times() -> None:
    """Missing travel times produce 'not available' message."""
    job = _make_job(travel_times=[])
    result = _format_travel_summary(job)
    assert "not available" in result.lower()


def test_format_travel_preferred_order() -> None:
    """Public transport appears before car in the travel summary."""
    job = _make_job(
        travel_times=[
            TravelTime(mode=TravelMode.CAR, minutes=20.0),
            TravelTime(mode=TravelMode.PUBLIC_TRANSPORT, minutes=45.0),
        ]
    )
    result = _format_travel_summary(job)
    assert "PT" in result
    assert "Car" in result
    assert result.index("PT") < result.index("Car")


def test_format_travel_skips_unavailable() -> None:
    """Unavailable travel modes are omitted from the summary."""
    job = _make_job(
        travel_times=[
            TravelTime(mode=TravelMode.CAR, available=False, error="No key"),
            TravelTime(mode=TravelMode.BIKE, minutes=30.0),
        ]
    )
    result = _format_travel_summary(job)
    assert "Bike" in result
    assert "Car" not in result


def test_notification_payload_structure() -> None:
    """build_notification_payload returns non-empty title and body."""
    job = _make_job(location="Amsterdam")
    title, body = _build_notification_payload(job)
    assert "Dev" in title
    assert "Co" in title
    assert "80/100" in body
    assert "Amsterdam" in body


def test_notification_title_contains_company() -> None:
    """Notification title includes both job title and company."""
    job = _make_job(title="Backend Engineer", company="StartupXYZ")
    title, _ = _build_notification_payload(job)
    assert "Backend Engineer" in title
    assert "StartupXYZ" in title


def test_notification_body_contains_salary() -> None:
    """Notification body includes salary information."""
    job = _make_job(salary_min=3500, salary_max=4500, salary_period="monthly")
    _, body = _build_notification_payload(job)
    assert "3500" in body
    assert "4500" in body


def test_notification_body_contains_vacation() -> None:
    """Notification body includes vacation days when available."""
    job = _make_job(vacation_days=25)
    _, body = _build_notification_payload(job)
    assert "25" in body
    assert "days/year" in body


def test_format_salary_no_data() -> None:
    """_format_salary_summary returns 'Not specified' when no salary data."""
    job = _make_job()
    assert _format_salary_summary(job) == "Not specified"


def test_format_salary_range() -> None:
    """_format_salary_summary formats a salary range."""
    job = _make_job(salary_min=3000, salary_max=4000, salary_period="monthly")
    result = _format_salary_summary(job)
    assert "3000" in result
    assert "4000" in result
    assert "monthly" in result


def test_format_salary_single_value() -> None:
    """_format_salary_summary handles min == max."""
    job = _make_job(salary_min=4000, salary_max=4000, salary_period="monthly")
    result = _format_salary_summary(job)
    assert "4000" in result


def test_notification_body_contains_source() -> None:
    """Notification body includes the job source (e.g. 'indeed')."""
    job = _make_job(source="nationalevacaturebank")
    _, body = _build_notification_payload(job)
    assert "nationalevacaturebank" in body


def test_format_travel_bike_mode_label() -> None:
    """Bike travel time uses 'Bike' label in the summary."""
    job = _make_job(travel_times=[TravelTime(mode=TravelMode.BIKE, minutes=35.0)])
    result = _format_travel_summary(job)
    assert "Bike" in result
    assert "35min" in result


# ---------------------------------------------------------------------------
# send_notification (mocked requests)
# ---------------------------------------------------------------------------


def test_send_notification_success() -> None:
    """send_notification returns True when ntfy.sh responds with 200."""
    from unittest.mock import MagicMock, patch

    from job_scout.models import Config
    from job_scout.notifier import send_notification

    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    config = Config(ntfy_topic="test-topic")
    job = _make_job()

    with patch("requests.post", return_value=mock_resp) as mock_post:
        result = send_notification(job, config)

    assert result is True
    mock_post.assert_called_once()


def test_send_notification_failure_returns_false() -> None:
    """send_notification returns False when the request raises an exception."""
    from unittest.mock import patch

    import requests as req_lib

    from job_scout.models import Config
    from job_scout.notifier import send_notification

    config = Config(ntfy_topic="test-topic")
    job = _make_job()

    with patch("requests.post", side_effect=req_lib.ConnectionError("refused")):
        result = send_notification(job, config)

    assert result is False


def test_send_notification_uses_correct_topic() -> None:
    """send_notification POSTs to the configured ntfy topic URL."""
    from unittest.mock import MagicMock, patch

    from job_scout.models import Config
    from job_scout.notifier import send_notification

    mock_resp = MagicMock()
    mock_resp.raise_for_status.return_value = None
    config = Config(ntfy_topic="my-job-alerts", ntfy_server="https://ntfy.sh")
    job = _make_job()

    with patch("requests.post", return_value=mock_resp) as mock_post:
        send_notification(job, config)

    args, _ = mock_post.call_args
    assert args[0] == "https://ntfy.sh/my-job-alerts"
