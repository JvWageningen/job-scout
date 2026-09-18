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


class LLMUnavailableError(LLMError):
    """Raised when a provider could not be reached at all.

    Deliberately narrower than :class:`LLMError`: it means the endpoint or binary
    was not there, not that the model answered badly. Only this triggers the
    configured fallback provider, so a genuine model or prompt failure surfaces
    instead of being silently retried somewhere else at a different price.
    """


class LLMTimeoutError(LLMUnavailableError):
    """Raised when a provider was reached but did not answer in time.

    Kept apart from other unavailability because the right response differs:
    a refused connection is worth another try a second later, but a call that
    already waited minutes for a long piece of writing would most likely wait
    that long again, with the applicant watching a spinner.

    Attributes:
        waited: Seconds the call was allowed to take before it was given up.
    """

    def __init__(self, message: str, *, waited: float) -> None:
        """Record how long the call waited.

        Args:
            message: What timed out, for the log.
            waited: Seconds the call was allowed to take.
        """
        super().__init__(message)
        self.waited = waited


# Calls that write a long piece of prose for the applicant. A reasoning model
# spends thousands of tokens on these before it answers: on 2026-09-18 Z.AI
# took 407 seconds for one set of interview questions (12,711 completion
# tokens), three times the 120-second default that suits scoring a vacancy.
WRITING_PURPOSES: frozenset[str] = frozenset(
    {"cover_letter", "behavioral_questions", "resume_tailoring", "screening_answers"}
)


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
