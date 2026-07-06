"""Local LLM client using OpenAI-compatible servers like Ollama or LM Studio."""

from __future__ import annotations

from loguru import logger

from job_scout.llm.base import CallPurpose, LLMError


class LocalLLMClient:
    """LLM client that calls an OpenAI-compatible server on localhost or the LAN.

    Supports any server implementing the OpenAI API specification, such as:
    - Ollama (default: http://localhost:11434/v1)
    - LM Studio
    - llama-swap (auto-swapping GGUF model proxy)
    - vLLM
    - llama.cpp server
    - text-generation-webui
    - LocalAI

    The ``purpose`` parameter on :meth:`complete` drives model selection:

    - ``"evaluation"`` / ``"keywords"`` → ``evaluation_model``
    - ``"screening"`` → ``screening_model``
    - ``"quick_eval"`` → ``quick_eval_model``

    Unlike Z AI, response_format with JSON mode is NOT used since many local
    servers don't support it. Instead, the existing prompt's "respond ONLY with JSON"
    instructions are relied upon, plus the _extract_json fence-stripping in
    evaluator.py/title_screener.py.
    """

    def __init__(
        self,
        base_url: str,
        evaluation_model: str,
        screening_model: str | None = None,
        keywords_model: str | None = None,
        quick_eval_model: str | None = None,
        api_key: str | None = None,
        evaluation_timeout: float = 120,
        screening_timeout: float = 90,
    ) -> None:
        """Initialise the local LLM client.

        Args:
            base_url: Base URL for the OpenAI-compatible API endpoint.
            evaluation_model: Model id for evaluation and keyword calls.
            screening_model: Model id for title-screening batches; falls back to
                ``evaluation_model``.
            keywords_model: Override for keyword generation; falls back to
                ``evaluation_model``.
            quick_eval_model: Model id for the cheap first-pass evaluation;
                falls back to ``screening_model`` or ``evaluation_model``.
            api_key: Optional API key; if not provided, uses "not-needed".
            evaluation_timeout: HTTP timeout in seconds for evaluation / keyword calls.
            screening_timeout: HTTP timeout in seconds for screening calls.
        """
        import openai

        self._base_url = base_url
        self._api_key = api_key or "not-needed"
        self._evaluation_model = evaluation_model
        self._screening_model = screening_model or evaluation_model
        self._keywords_model = keywords_model or evaluation_model
        self._quick_eval_model = quick_eval_model or self._screening_model
        self._evaluation_timeout = evaluation_timeout
        self._screening_timeout = screening_timeout
        self._client = openai.OpenAI(
            api_key=self._api_key, base_url=base_url, max_retries=0
        )

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
                timeout=effective_timeout,
            )
        except openai.OpenAIError as exc:
            raise LLMError(f"Local LLM API error: {exc}") from exc

        usage = response.usage
        if usage:
            logger.debug(
                "Local LLM usage: prompt_tokens={}, completion_tokens={}, model={}",
                usage.prompt_tokens,
                usage.completion_tokens,
                model,
            )

        content = response.choices[0].message.content
        return (content or "").strip()

    def check_available(self) -> tuple[bool, str | None]:
        """Check whether the local LLM server is reachable.

        Attempts a simple HTTP GET to the base URL to verify connectivity
        without making a full API call.

        Returns:
            (True, None) if the server is reachable, (False, error_message) otherwise.
        """
        import requests

        try:
            requests.get(self._base_url, timeout=5)
            return True, None
        except requests.RequestException as exc:
            return (
                False,
                f"Cannot reach local LLM server at {self._base_url}: {exc}",
            )
