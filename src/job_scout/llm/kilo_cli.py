"""LLM client that delegates to the Kilo Code CLI via subprocess."""

from __future__ import annotations

import json
import shutil
import subprocess

from loguru import logger

from job_scout.llm.base import CallPurpose, LLMError

KILO_NOT_FOUND_MSG = (
    "Kilo Code CLI not found.\n"
    "Install it with:  npm install -g kilo-code\n"
    "Or visit: https://kilocode.ai"
)


class KiloCliClient:
    """LLM client that calls Z AI (or other providers) via the Kilo Code CLI.

    Uses ``kilo run --auto --format json --model <provider>/<model>`` and
    parses the resulting NDJSON stream to extract assistant text.  Model names
    must include the provider prefix, e.g. ``"zai/glm-5.1"``.

    No sessions are created — each call is a fresh one-shot prompt.  The
    ``--auto`` flag lets Kilo auto-approve any incidental tool calls so the
    subprocess never blocks waiting for input.
    """

    def __init__(
        self,
        evaluation_model: str = "zai/glm-5.1",
        screening_model: str = "zai/glm-4.5-air",
        keywords_model: str | None = None,
        quick_eval_model: str | None = None,
        evaluation_timeout: float = 120,
        screening_timeout: float = 90,
    ) -> None:
        """Initialise the Kilo CLI client.

        Args:
            evaluation_model: Model for evaluation and keyword calls (provider/model).
            screening_model: Model for title-screening batches (provider/model).
            keywords_model: Override for keyword generation; falls back to
                ``evaluation_model``.
            quick_eval_model: Model for the cheap first-pass evaluation; falls back
                to ``screening_model``.
            evaluation_timeout: Subprocess timeout in seconds for evaluation calls.
            screening_timeout: Subprocess timeout in seconds for screening calls.
        """
        self._evaluation_model = evaluation_model
        self._screening_model = screening_model
        self._keywords_model = keywords_model or evaluation_model
        self._quick_eval_model = quick_eval_model or screening_model
        self._evaluation_timeout = evaluation_timeout
        self._screening_timeout = screening_timeout

    def complete(
        self,
        prompt: str,
        *,
        purpose: CallPurpose,
        timeout: float | None = None,
    ) -> str:
        """Run a one-shot Kilo prompt and return the assistant text.

        Args:
            prompt: Full prompt text to send.
            purpose: Determines model and timeout selection.
            timeout: Override the default timeout for this call.

        Returns:
            Concatenated text from all NDJSON ``text`` events, stripped.

        Raises:
            LLMError: If the CLI is missing, times out, or returns no content.
        """
        ok, err = self.check_available()
        if not ok:
            raise LLMError(err or KILO_NOT_FOUND_MSG)

        if purpose == "screening":
            model = self._screening_model
            default_timeout = self._screening_timeout
        elif purpose == "quick_eval":
            model = self._quick_eval_model
            default_timeout = self._screening_timeout
        elif purpose == "keywords":
            model = self._keywords_model
            default_timeout = self._evaluation_timeout
        else:
            model = self._evaluation_model
            default_timeout = self._evaluation_timeout

        effective_timeout = timeout if timeout is not None else default_timeout

        cmd = ["kilo", "run", "--auto", "--format", "json", "--model", model, prompt]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=effective_timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise LLMError(f"Kilo CLI timed out after {effective_timeout}s") from exc
        except FileNotFoundError as exc:
            raise LLMError(KILO_NOT_FOUND_MSG) from exc

        if result.returncode != 0 and not result.stdout.strip():
            snip = result.stderr[:400]
            raise LLMError(f"Kilo CLI failed (exit {result.returncode}): {snip}")

        return self._parse_ndjson(result.stdout, model)

    def _parse_ndjson(self, raw: str, model: str) -> str:
        """Extract assistant text from Kilo NDJSON output.

        Args:
            raw: Raw stdout from ``kilo run --format json``.
            model: Model name used (for debug logging).

        Returns:
            Concatenated text content from all text events.

        Raises:
            LLMError: If no text content was found.
        """
        text_parts: list[str] = []
        output_tokens = 0

        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            event_type = event.get("type", "")
            part = event.get("part", {})

            if event_type == "text":
                text = part.get("text", "")
                if text:
                    text_parts.append(text)
            elif event_type == "step_finish":
                tokens = part.get("tokens", {})
                output_tokens += tokens.get("output", 0)

        if output_tokens:
            logger.debug("Kilo CLI output_tokens={}, model={}", output_tokens, model)

        if not text_parts:
            raise LLMError("Kilo CLI returned no text content")

        return "".join(text_parts).strip()

    def check_available(self) -> tuple[bool, str | None]:
        """Check whether the 'kilo' binary is on PATH.

        Returns:
            (True, None) if found, (False, install instructions) otherwise.
        """
        if shutil.which("kilo") is None:
            return False, KILO_NOT_FOUND_MSG
        return True, None
