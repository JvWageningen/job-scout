"""LLM client that delegates to the Claude Code CLI via subprocess."""

from __future__ import annotations

import shutil
import subprocess

from job_scout.llm.base import CallPurpose, LLMError

CLAUDE_NOT_FOUND_MSG = (
    "Claude Code CLI not found.\n"
    "Install it with:  npm install -g @anthropic-ai/claude-code\n"
    "Or visit: https://docs.anthropic.com/claude-code"
)


class ClaudeCliClient:
    """Invokes the local 'claude' binary via subprocess."""

    def __init__(
        self,
        evaluation_model: str | None = None,
        screening_model: str = "haiku",
        evaluation_timeout: float = 90,
        screening_timeout: float = 60,
    ) -> None:
        """Initialise the client.

        Args:
            evaluation_model: --model flag for evaluation/keywords calls; None omits
                the flag.
            screening_model: --model flag for screening calls.
            evaluation_timeout: Seconds before evaluation subprocess is killed.
            screening_timeout: Seconds before screening subprocess is killed.
        """
        self._evaluation_model = evaluation_model
        self._screening_model = screening_model
        self._evaluation_timeout = evaluation_timeout
        self._screening_timeout = screening_timeout

    def complete(
        self, prompt: str, *, purpose: CallPurpose, timeout: float | None = None
    ) -> str:
        """Run the Claude CLI and return its stdout.

        Args:
            prompt: Prompt text to pass to the CLI.
            purpose: Determines model selection and extra flags.
            timeout: Override the default timeout for this call.

        Returns:
            Stripped stdout from the CLI.

        Raises:
            LLMError: If the binary is missing or exits non-zero.
        """
        ok, err = self.check_available()
        if not ok:
            raise LLMError(err or CLAUDE_NOT_FOUND_MSG)

        is_cheap = purpose in ("screening", "quick_eval")
        effective_timeout = (
            timeout
            if timeout is not None
            else (self._screening_timeout if is_cheap else self._evaluation_timeout)
        )
        model = self._screening_model if is_cheap else self._evaluation_model

        argv = ["claude", "--print"]
        if model:
            argv += ["--model", model]
        if is_cheap:
            argv += ["--tools", ""]
        argv.append(prompt)

        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=effective_timeout,
        )

        if result.returncode != 0:
            snip = result.stderr[:400]
            raise LLMError(f"Claude CLI failed (exit {result.returncode}): {snip}")

        return result.stdout.strip()

    def check_available(self) -> tuple[bool, str | None]:
        """Check whether the 'claude' binary is on PATH.

        Returns:
            (True, None) if found, (False, install instructions) otherwise.
        """
        if shutil.which("claude") is None:
            return False, CLAUDE_NOT_FOUND_MSG
        return True, None
