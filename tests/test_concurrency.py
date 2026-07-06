"""Empirical proof that quick-eval and full-eval run LLM calls concurrently.

These tests use an LLM client that sleeps per call so that wall-clock time
gives direct evidence of parallelism, rather than trusting that a
ThreadPoolExecutor is wired up correctly by inspection alone.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

from job_scout.cli import _run_quick_eval
from job_scout.database import Database
from job_scout.llm.base import CallPurpose
from job_scout.models import Config, JobListing, RunStats

_JOB_COUNT = 10
_CALL_DELAY = 0.2


class _SlowFakeLLMClient:
    """LLM client that sleeps briefly per call to make concurrency measurable.

    Args:
        delay: Seconds to sleep on every call.
        response: Canned JSON response text to return.
    """

    def __init__(self, delay: float, response: str) -> None:
        self._delay = delay
        self._response = response

    def complete(
        self, prompt: str, *, purpose: CallPurpose, timeout: float | None = None
    ) -> str:
        """Sleep for the configured delay, then return the canned response."""
        time.sleep(self._delay)
        return self._response

    def check_available(self) -> tuple[bool, str | None]:
        """Always report as available."""
        return True, None


def _make_job(i: int) -> JobListing:
    """Build a minimal unique JobListing for concurrency timing tests."""
    return JobListing(
        title=f"Job {i}",
        company=f"Company {i}",
        url=f"https://example.com/job/{i}",
        source="test",
        seen_at=datetime.now(UTC),
    )


def test_quick_eval_runs_llm_calls_concurrently(tmp_path, base_config: Config) -> None:
    """max_parallel_evaluations > 1 must cut wall-clock time, not just add code."""
    jobs = [_make_job(i) for i in range(_JOB_COUNT)]
    config = base_config.model_copy(
        update={"max_parallel_evaluations": 5, "quick_eval_threshold": 999}
    )
    client = _SlowFakeLLMClient(delay=_CALL_DELAY, response='{"fit_score": 50}')
    db = Database(tmp_path / "quick_eval.db")
    stats = RunStats()

    start = time.monotonic()
    _run_quick_eval(jobs, config, "cv text", db, True, False, client, stats)
    elapsed = time.monotonic() - start

    sequential_time = _JOB_COUNT * _CALL_DELAY
    assert elapsed < sequential_time / 2, (
        f"quick-eval took {elapsed:.2f}s for {_JOB_COUNT} jobs at "
        f"{_CALL_DELAY}s/call -- expected well under the "
        f"{sequential_time:.2f}s sequential baseline if truly parallel"
    )
