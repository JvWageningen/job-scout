"""Tests for the vacancy auto-prune detection."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from job_scout.models import Config, JobListing, JobStatus
from job_scout.pruner import (
    PruneCheck,
    PruneOutcome,
    check_vacancy_open,
    prune_jobs,
)
from tests.helpers import FakeLLMClient


def _job(url: str, *, source: str = "linkedin") -> JobListing:
    return JobListing(
        title="CRO Specialist",
        company="Acme",
        location="Amsterdam",
        url=url,
        source=source,
        seen_at=datetime.now(UTC),
    )


def _resp(url: str, status: int = 200, text: str = "") -> MagicMock:
    resp = MagicMock()
    resp.url = url
    resp.status_code = status
    resp.text = text
    return resp


def test_should_prune_property() -> None:
    """should_prune is True only for FILLED/GONE outcomes."""
    assert PruneCheck(outcome=PruneOutcome.FILLED, reason="x").should_prune
    assert PruneCheck(outcome=PruneOutcome.GONE, reason="x").should_prune
    assert not PruneCheck(outcome=PruneOutcome.OPEN, reason="x").should_prune
    assert not PruneCheck(outcome=PruneOutcome.UNKNOWN, reason="x").should_prune


def test_linkedin_expired_redirect_is_gone() -> None:
    """A LinkedIn job redirected to expired_jd_redirect is GONE."""
    job = _job("https://www.linkedin.com/jobs/view/123")
    redirected = _resp("https://nl.linkedin.com/jobs/x?trk=expired_jd_redirect")
    with patch("job_scout.pruner.requests.get", return_value=redirected):
        check = check_vacancy_open(job)
    assert check.outcome == PruneOutcome.GONE
    assert check.should_prune


def test_linkedin_still_open_stays_open() -> None:
    """A LinkedIn job still at /jobs/view/ with no closed banner is OPEN."""
    job = _job("https://www.linkedin.com/jobs/view/123")
    same = _resp("https://www.linkedin.com/jobs/view/123", text="<p>Apply now</p>")
    with patch("job_scout.pruner.requests.get", return_value=same):
        check = check_vacancy_open(job)
    assert check.outcome == PruneOutcome.OPEN
    assert not check.should_prune


def test_linkedin_closed_banner_without_redirect_is_filled() -> None:
    """A LinkedIn job at /jobs/view/ showing a closed banner is FILLED.

    Regression: closed postings often stay at /jobs/view/ (no expired redirect)
    but display "No longer accepting applications".
    """
    job = _job("https://www.linkedin.com/jobs/view/123")
    closed = _resp(
        "https://www.linkedin.com/jobs/view/123",
        text="<div>No longer accepting applications</div>",
    )
    with patch("job_scout.pruner.requests.get", return_value=closed):
        check = check_vacancy_open(job)
    assert check.outcome == PruneOutcome.FILLED
    assert check.should_prune


def test_generic_404_is_gone() -> None:
    """A non-LinkedIn page returning 404 is GONE."""
    job = _job("https://company.example/job/1", source="custom")
    with patch("job_scout.pruner.requests.get", return_value=_resp("u", 404)):
        check = check_vacancy_open(job)
    assert check.outcome == PruneOutcome.GONE


def test_generic_403_is_unknown_not_pruned() -> None:
    """A blocked page (403) is UNKNOWN and must not be pruned."""
    job = _job("https://nl.indeed.com/viewjob?jk=1", source="indeed")
    with patch("job_scout.pruner.requests.get", return_value=_resp("u", 403)):
        check = check_vacancy_open(job)
    assert check.outcome == PruneOutcome.UNKNOWN
    assert not check.should_prune


@pytest.mark.parametrize(
    "phrase",
    [
        "This job is no longer accepting applications.",
        "Deze vacature is vervuld.",
        "Helaas, deze vacature is verlopen op indeed.",
        "De positie is niet meer beschikbaar.",
    ],
)
def test_fill_phrase_detected(phrase: str) -> None:
    """Explicit Dutch/English fill phrases mark a page FILLED."""
    job = _job("https://company.example/job/1", source="custom")
    body = f"<html><body><h1>Job</h1><p>{phrase}</p></body></html>"
    with patch("job_scout.pruner.requests.get", return_value=_resp("u", 200, body)):
        check = check_vacancy_open(job)
    assert check.outcome == PruneOutcome.FILLED
    assert check.should_prune


def test_open_page_without_signal_stays_open() -> None:
    """A reachable page with no fill signal and no LLM is OPEN."""
    job = _job("https://company.example/job/1", source="custom")
    body = "<html><body>Apply now for this great role!</body></html>"
    with patch("job_scout.pruner.requests.get", return_value=_resp("u", 200, body)):
        check = check_vacancy_open(job)
    assert check.outcome == PruneOutcome.OPEN


def test_llm_judges_ambiguous_page_filled() -> None:
    """With no keyword hit, the LLM verdict decides FILLED/OPEN."""
    job = _job("https://company.example/careers", source="custom")
    body = "<html><body>Some ambiguous careers page.</body></html>"
    client = FakeLLMClient(['{"status": "filled"}'])
    with patch("job_scout.pruner.requests.get", return_value=_resp("u", 200, body)):
        check = check_vacancy_open(job, client=client)
    assert check.outcome == PruneOutcome.FILLED


def test_prune_jobs_marks_filled_and_respects_dry_run() -> None:
    """prune_jobs marks GONE/FILLED jobs expired, and skips writes on dry-run."""
    gone_job = _job("https://www.linkedin.com/jobs/view/1")
    gone_job.id = 11
    open_job = _job("https://www.linkedin.com/jobs/view/2")
    open_job.id = 22
    config = Config()

    def fake_check(job: JobListing, **_: object) -> PruneCheck:
        if job.id == 11:
            return PruneCheck(outcome=PruneOutcome.GONE, reason="gone")
        return PruneCheck(outcome=PruneOutcome.OPEN, reason="open")

    db = MagicMock()
    with patch("job_scout.pruner.check_vacancy_open", side_effect=fake_check):
        stats = prune_jobs([gone_job, open_job], db, config, dry_run=False)
    assert stats.checked == 2
    assert stats.pruned == 1
    assert stats.still_open == 1
    db.mark_expired.assert_called_once_with(11, reason="gone: gone")

    db2 = MagicMock()
    with patch("job_scout.pruner.check_vacancy_open", side_effect=fake_check):
        stats2 = prune_jobs([gone_job, open_job], db2, config, dry_run=True)
    assert stats2.pruned == 1
    db2.mark_expired.assert_not_called()


def test_expired_is_valid_status_and_terminal() -> None:
    """EXPIRED is reachable from active states and is terminal."""
    from job_scout.models import ApplicationTracker

    assert ApplicationTracker.can_transition(JobStatus.MATCHED, JobStatus.EXPIRED)
    assert ApplicationTracker.can_transition(JobStatus.APPROVED, JobStatus.EXPIRED)
    assert ApplicationTracker.get_valid_transitions(JobStatus.EXPIRED) == []
