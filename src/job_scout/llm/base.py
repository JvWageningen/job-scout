"""Protocol and shared types for LLM providers."""

from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

CallPurpose = Literal[
    "evaluation",
    "quick_eval",
    "screening",
    "keywords",
    "cv_parsing",
    "resume_tailoring",
    "cover_letter",
    "screening_questions",
    "screening_answers",
    "behavioral_questions",
]


class LLMError(RuntimeError):
    """Raised when an LLM call fails."""


@runtime_checkable
class LLMClient(Protocol):
    """Common interface for LLM provider clients."""

    def complete(
        self, prompt: str, *, purpose: CallPurpose, timeout: float | None = None
    ) -> str:
        """Send a prompt and return the response text.

        Args:
            prompt: The full prompt to send.
            purpose: Hint for the client to select the right model/settings.
            timeout: Maximum seconds to wait; None uses the client default.

        Returns:
            Raw text response (may contain markdown-fenced JSON).

        Raises:
            LLMError: If the call fails for any reason.
        """
        ...

    def check_available(self) -> tuple[bool, str | None]:
        """Check whether this client is ready to use.

        Returns:
            (True, None) if available, (False, error_message) otherwise.
            Does not make network calls.
        """
        ...
