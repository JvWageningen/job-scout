"""Tests for RetryingLLMClient."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from job_scout.llm.base import LLMClient, LLMError
from job_scout.llm.retry import RetryingLLMClient


def _make_inner(side_effects: list) -> LLMClient:
    inner = MagicMock(spec=LLMClient)
    inner.complete.side_effect = side_effects
    inner.check_available.return_value = (True, None)
    return inner


def test_passthrough_on_success() -> None:
    """Returns result immediately when inner succeeds on first attempt."""
    inner = _make_inner(["result"])
    client = RetryingLLMClient(inner, attempts=3, base_delay=1.0, sleep=lambda _: None)
    assert client.complete("prompt", purpose="evaluation") == "result"
    assert inner.complete.call_count == 1


def test_retries_and_succeeds_on_second() -> None:
    """Retries after first LLMError and returns result on second attempt."""
    inner = _make_inner([LLMError("fail"), "ok"])
    delays: list[float] = []
    client = RetryingLLMClient(inner, attempts=3, base_delay=2.0, sleep=delays.append)
    assert client.complete("prompt", purpose="screening") == "ok"
    assert inner.complete.call_count == 2
    assert delays == [2.0]


def test_raises_after_all_attempts_exhausted() -> None:
    """Re-raises LLMError when every attempt fails."""
    inner = _make_inner([LLMError("a"), LLMError("b"), LLMError("c")])
    client = RetryingLLMClient(inner, attempts=3, base_delay=1.0, sleep=lambda _: None)
    with pytest.raises(LLMError):
        client.complete("prompt", purpose="evaluation")
    assert inner.complete.call_count == 3


def test_exponential_backoff_delays() -> None:
    """Sleep delays double each retry: base, base*2, ..."""
    inner = _make_inner([LLMError("x"), LLMError("y"), LLMError("z"), "done"])
    delays: list[float] = []
    client = RetryingLLMClient(inner, attempts=4, base_delay=1.0, sleep=delays.append)
    client.complete("prompt", purpose="quick_eval")
    assert delays == [1.0, 2.0, 4.0]


def test_no_sleep_on_last_attempt() -> None:
    """No sleep is called after the final attempt."""
    inner = _make_inner([LLMError("x"), LLMError("y")])
    delays: list[float] = []
    client = RetryingLLMClient(inner, attempts=2, base_delay=1.0, sleep=delays.append)
    with pytest.raises(LLMError):
        client.complete("p", purpose="evaluation")
    assert delays == [1.0]


def test_check_available_passthrough() -> None:
    """check_available delegates to inner without retry logic."""
    inner = _make_inner([])
    inner.check_available.return_value = (False, "unavailable")
    client = RetryingLLMClient(inner, attempts=3, base_delay=1.0, sleep=lambda _: None)
    ok, err = client.check_available()
    assert ok is False
    assert err == "unavailable"
    inner.check_available.assert_called_once()


def test_inner_property() -> None:
    """inner property returns the wrapped client."""
    inner = _make_inner([])
    client = RetryingLLMClient(inner, attempts=2, base_delay=0.5, sleep=lambda _: None)
    assert client.inner is inner


def test_attempts_clamped_to_one() -> None:
    """Passing attempts=0 is treated as 1 attempt."""
    inner = _make_inner(["x"])
    client = RetryingLLMClient(inner, attempts=0, base_delay=1.0, sleep=lambda _: None)
    assert client.complete("p", purpose="evaluation") == "x"
    assert inner.complete.call_count == 1
