"""Shared test helpers."""

from __future__ import annotations

from collections import deque

from job_scout.llm.base import CallPurpose, LLMError


class FakeLLMClient:
    """Test double for LLMClient that returns canned responses.

    Args:
        responses: Sequence of strings returned in order.
        repeat_last: When True, the final response is repeated indefinitely.
            When False and responses are exhausted, raises LLMError.
    """

    def __init__(
        self,
        responses: list[str],
        *,
        repeat_last: bool = True,
    ) -> None:
        self._queue: deque[str] = deque(responses)
        self._repeat_last = repeat_last
        self._last: str = responses[-1] if responses else ""
        self.calls: list[tuple[str, CallPurpose]] = []

    def complete(
        self, prompt: str, *, purpose: CallPurpose, timeout: float | None = None
    ) -> str:
        """Return the next canned response and record the call."""
        self.calls.append((prompt, purpose))
        if self._queue:
            self._last = self._queue.popleft()
            return self._last
        if self._repeat_last:
            return self._last
        raise LLMError("FakeLLMClient: no more responses")

    def check_available(self) -> tuple[bool, str | None]:
        """Always report as available."""
        return True, None
