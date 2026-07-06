"""Retry wrapper for LLM clients with exponential backoff."""

from __future__ import annotations

import time as _time
from collections.abc import Callable

from job_scout.llm.base import CallPurpose, LLMClient, LLMError


class RetryingLLMClient:
    """Wraps an LLMClient to retry on LLMError with exponential backoff.

    Args:
        inner: The underlying LLM client to wrap.
        attempts: Maximum number of attempts before re-raising.
        base_delay: Base delay in seconds (doubles each attempt).
        sleep: Callable used to sleep between retries; injectable for tests.
    """

    def __init__(
        self,
        inner: LLMClient,
        attempts: int = 3,
        base_delay: float = 1.0,
        sleep: Callable[[float], None] = _time.sleep,
    ) -> None:
        self._inner = inner
        self._attempts = max(1, attempts)
        self._base_delay = base_delay
        self._sleep = sleep

    @property
    def inner(self) -> LLMClient:
        """Return the wrapped LLMClient."""
        return self._inner

    def complete(
        self,
        prompt: str,
        *,
        purpose: CallPurpose = "evaluation",
        timeout: float | None = None,
    ) -> str:
        """Call inner.complete with exponential backoff on LLMError.

        Args:
            prompt: The prompt to send to the LLM.
            purpose: Hint for model routing (e.g. "evaluation", "screening").
            timeout: Per-attempt timeout in seconds.

        Returns:
            The LLM response text.

        Raises:
            LLMError: After all attempts are exhausted.
        """
        last_exc: LLMError = LLMError("No attempts made")
        for attempt in range(self._attempts):
            try:
                return self._inner.complete(prompt, purpose=purpose, timeout=timeout)
            except LLMError as exc:
                last_exc = exc
            if attempt < self._attempts - 1:
                self._sleep(self._base_delay * (2**attempt))
        raise last_exc

    def check_available(self) -> tuple[bool, str | None]:
        """Delegate availability check to the inner client.

        Returns:
            (True, None) if available, (False, error_message) otherwise.
        """
        return self._inner.check_available()
