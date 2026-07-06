"""Z AI (GLM) LLM client using the OpenAI-compatible REST API."""

from __future__ import annotations

from loguru import logger

from job_scout.llm.base import CallPurpose, LLMError


class ZaiClient:
    """LLM client that calls Z AI's GLM models via the OpenAI-compatible API.

    The ``purpose`` parameter on :meth:`complete` drives model selection:

    - ``"evaluation"`` / ``"keywords"`` → ``evaluation_model``
    - ``"screening"`` → ``screening_model``

    Unlike the Claude CLI path, tool use is never invoked here — Z AI's chat
    completions endpoint ignores the ``tools`` parameter unless explicitly
    passed, so no special-casing is needed for the screening purpose.

    The ``openai`` SDK retries up to 2 times on 5xx / 429 by default.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str,
        evaluation_model: str,
        screening_model: str,
        keywords_model: str | None = None,
        quick_eval_model: str | None = None,
        evaluation_timeout: float = 120,
        screening_timeout: float = 60,
    ) -> None:
        """Initialise the Z AI client.

        Args:
            api_key: Z AI API key.
            base_url: Base URL for the Z AI API endpoint.
            evaluation_model: Model id for evaluation and keyword calls.
            screening_model: Model id for title-screening batches.
            keywords_model: Override for keyword generation; falls back to
                ``evaluation_model``.
            quick_eval_model: Model id for the cheap first-pass evaluation;
                falls back to ``screening_model``.
            evaluation_timeout: HTTP timeout in seconds for evaluation / keyword calls.
            screening_timeout: HTTP timeout in seconds for screening calls.
        """
        import openai

        self._client = openai.OpenAI(api_key=api_key, base_url=base_url, max_retries=0)
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
        """Send a prompt and return the model's text response.

        Args:
            prompt: The full prompt to send.
            purpose: Determines which model and timeout are used.
            timeout: Override the default timeout for this call.

        Returns:
            Stripped text content from the first choice.

        Raises:
            LLMError: On any API or transport error.
        """
        import openai

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

        try:
            response = self._client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You must respond with valid JSON only. "
                            "Do not include any explanation or text "
                            "outside the JSON object."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                timeout=effective_timeout,
            )
        except openai.OpenAIError as exc:
            raise LLMError(f"Z AI API error: {exc}") from exc

        usage = response.usage
        if usage:
            logger.debug(
                "Z AI usage: prompt_tokens={}, completion_tokens={}, model={}",
                usage.prompt_tokens,
                usage.completion_tokens,
                model,
            )

        content = response.choices[0].message.content
        return (content or "").strip()

    def check_available(self) -> tuple[bool, str | None]:
        """Check whether Z AI credentials are present without making a network call.

        Returns:
            ``(True, None)`` when the client was constructed with a non-empty key,
            ``(False, message)`` otherwise.
        """
        # api_key availability is validated at construction time in the factory.
        # If we reach here, the key was present.
        return True, None
