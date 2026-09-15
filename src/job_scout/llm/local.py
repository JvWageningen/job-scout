"""Local LLM client using OpenAI-compatible servers like Ollama or LM Studio."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from loguru import logger

from job_scout.llm.base import CallPurpose, LLMError

# Purposes where the model's reasoning earns its cost: the answer is a
# judgement, not a sieve. Kept in sync with Config.local_reasoning_purposes.
DEFAULT_REASONING: frozenset[str] = frozenset(
    {"evaluation", "cv_parsing", "resume_tailoring", "cover_letter"}
)

if TYPE_CHECKING:
    import openai

# A GET on the base URL itself 404s on llama-swap, llama.cpp-server, vLLM and
# Ollama alike -- only the sub-routes exist -- so health is probed against the
# model list, whose success also proves the server speaks the OpenAI API.
_PROBE_PATH = "/models"


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

    Several base URLs may be supplied. They are tried in order and the first one
    that answers becomes the active endpoint; a transport failure mid-run demotes
    it and the next candidate is tried, so losing one route to the model host
    (a dropped VPN link, say) no longer takes the whole pipeline down.

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
        fallback_base_urls: list[str] | None = None,
        connect_timeout: float = 15.0,
        probe_timeout: float = 10.0,
        reasoning_purposes: Sequence[str] | None = None,
        max_tokens_reasoning: int = 8000,
        max_tokens_direct: int = 1200,
    ) -> None:
        """Initialise the local LLM client.

        Args:
            base_url: Primary base URL for the OpenAI-compatible API endpoint.
            evaluation_model: Model id for evaluation and keyword calls.
            screening_model: Model id for title-screening batches; falls back to
                ``evaluation_model``.
            keywords_model: Override for keyword generation; falls back to
                ``evaluation_model``.
            quick_eval_model: Model id for the cheap first-pass evaluation;
                falls back to ``screening_model`` or ``evaluation_model``.
            api_key: Optional API key; if not provided, uses "not-needed".
            evaluation_timeout: HTTP read timeout in seconds for evaluation /
                keyword calls.
            screening_timeout: HTTP read timeout in seconds for screening calls.
            fallback_base_urls: Further endpoints to try, in order, when the
                primary is unreachable.
            connect_timeout: Seconds allowed to establish a TCP connection.
            probe_timeout: Seconds allowed for a health probe.
            reasoning_purposes: Call purposes that keep the model's reasoning
                enabled. Every other purpose asks the model to answer directly.
            max_tokens_reasoning: Output ceiling for a reasoning call.
            max_tokens_direct: Output ceiling for a direct call.
        """
        self._base_urls = _dedupe_urls([base_url, *(fallback_base_urls or [])])
        self._api_key = api_key or "not-needed"
        self._evaluation_model = evaluation_model
        self._screening_model = screening_model or evaluation_model
        self._keywords_model = keywords_model or evaluation_model
        self._quick_eval_model = quick_eval_model or self._screening_model
        self._evaluation_timeout = evaluation_timeout
        self._screening_timeout = screening_timeout
        self._connect_timeout = connect_timeout
        self._probe_timeout = probe_timeout
        self._reasoning_purposes = frozenset(
            reasoning_purposes if reasoning_purposes is not None else DEFAULT_REASONING
        )
        self._max_tokens_reasoning = max_tokens_reasoning
        self._max_tokens_direct = max_tokens_direct
        self._active_index = 0
        self._clients: dict[str, Any] = {}
        # Set once a server rejects the thinking switch, so we stop sending it
        # instead of failing every later call on a model whose chat template
        # does not know the option.
        self._thinking_switch_unsupported = False

    @property
    def base_url(self) -> str:
        """Return the endpoint currently being used.

        Returns:
            The active base URL.
        """
        return self._base_urls[self._active_index]

    def _client_for(self, base_url: str, read_timeout: float) -> openai.OpenAI:
        """Return a cached client for one endpoint.

        Args:
            base_url: Endpoint to build a client for.
            read_timeout: Read timeout in seconds.

        Returns:
            A configured OpenAI client.
        """
        import httpx  # noqa: PLC0415
        import openai  # noqa: PLC0415

        key = f"{base_url}|{read_timeout}"
        client = self._clients.get(key)
        if client is None:
            client = openai.OpenAI(
                api_key=self._api_key,
                base_url=base_url,
                max_retries=0,
                timeout=httpx.Timeout(read_timeout, connect=self._connect_timeout),
            )
            self._clients[key] = client
        return client

    def complete(
        self,
        prompt: str,
        *,
        purpose: CallPurpose,
        timeout: float | None = None,
    ) -> str:
        """Send a prompt and return the model's text response.

        Endpoints are tried in priority order. A connection-level failure moves
        on to the next candidate; an error from a server that did answer (a bad
        model id, a refusal) is raised immediately, since retrying it elsewhere
        would only repeat the same mistake.

        Args:
            prompt: The full prompt to send.
            purpose: Determines which model and timeout are used.
            timeout: Override the default read timeout for this call.

        Returns:
            Stripped text content from the first choice.

        Raises:
            LLMError: On any API or transport error.
        """
        import openai  # noqa: PLC0415

        model, default_timeout = self._route(purpose)
        read_timeout = timeout if timeout is not None else default_timeout

        failures: list[str] = []
        for offset in range(len(self._base_urls)):
            index = (self._active_index + offset) % len(self._base_urls)
            base_url = self._base_urls[index]
            try:
                response = self._call_with_thinking_fallback(
                    base_url, read_timeout, model, prompt, purpose
                )
            except openai.APIConnectionError as exc:
                # Covers APITimeoutError, which subclasses it.
                failures.append(f"{base_url}: {type(exc).__name__}: {exc}")
                logger.warning(
                    "Local LLM endpoint {} unreachable ({}); trying next",
                    base_url,
                    type(exc).__name__,
                )
                continue
            except openai.OpenAIError as exc:
                raise LLMError(f"Local LLM API error at {base_url}: {exc}") from exc

            if index != self._active_index:
                logger.info("Local LLM endpoint switched to {}", base_url)
                self._active_index = index
            return _content_of(response, model)

        raise LLMError("No local LLM endpoint reachable - " + "; ".join(failures))

    def _call_with_thinking_fallback(
        self,
        base_url: str,
        read_timeout: float,
        model: str,
        prompt: str,
        purpose: CallPurpose,
    ) -> Any:
        """Issue one call, retrying once without the thinking switch.

        Not every chat template understands ``enable_thinking``. A server that
        rejects it fails the request outright, which would otherwise take down
        every screening and quick-scoring call on that model. The first refusal
        disables the switch for the rest of the run.

        Args:
            base_url: Endpoint to call.
            read_timeout: Read timeout in seconds.
            model: Model id to request.
            prompt: The user prompt.
            purpose: The call purpose.

        Returns:
            The raw chat completion response.
        """
        import openai  # noqa: PLC0415

        sent_switch = (
            purpose not in self._reasoning_purposes
            and not self._thinking_switch_unsupported
        )
        try:
            return self._clients_complete(
                base_url, read_timeout, model, prompt, purpose
            )
        except openai.BadRequestError:
            if not sent_switch:
                # Nothing to back off from, so this is a real rejection.
                raise
            logger.warning(
                "Model {} rejected the thinking switch; "
                "continuing with reasoning left on",
                model,
            )
            self._thinking_switch_unsupported = True
            return self._clients_complete(
                base_url, read_timeout, model, prompt, purpose
            )

    def _clients_complete(
        self,
        base_url: str,
        read_timeout: float,
        model: str,
        prompt: str,
        purpose: CallPurpose,
    ) -> Any:
        """Issue one chat completion against a specific endpoint.

        Args:
            base_url: Endpoint to call.
            read_timeout: Read timeout in seconds.
            model: Model id to request.
            prompt: The user prompt.
            purpose: Decides whether reasoning stays on and which token cap applies.

        Returns:
            The raw chat completion response.
        """
        reasoning = purpose in self._reasoning_purposes
        extra: dict[str, Any] = {
            "max_tokens": (
                self._max_tokens_reasoning if reasoning else self._max_tokens_direct
            )
        }
        if not reasoning and not self._thinking_switch_unsupported:
            # How Qwen-family chat templates are told to skip the thinking
            # block. Harmless on templates that ignore it; the one that objects
            # is handled by the caller, which retries without it.
            extra["chat_template_kwargs"] = {"enable_thinking": False}

        return self._client_for(base_url, read_timeout).chat.completions.create(
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
            timeout=read_timeout,
            extra_body=extra,
        )

    def _route(self, purpose: CallPurpose) -> tuple[str, float]:
        """Select the model and default timeout for a call purpose.

        Args:
            purpose: The call purpose.

        Returns:
            Tuple of (model id, default read timeout).
        """
        if purpose == "screening":
            return self._screening_model, self._screening_timeout
        if purpose == "quick_eval":
            return self._quick_eval_model, self._screening_timeout
        if purpose == "keywords":
            return self._keywords_model, self._evaluation_timeout
        return self._evaluation_model, self._evaluation_timeout

    def check_available(self) -> tuple[bool, str | None]:
        """Check whether any configured local LLM endpoint is reachable.

        Probes ``{base_url}/models`` on each endpoint in priority order and
        makes the first healthy one active. The status code is checked: a 404
        or an unrelated web server answering on the port is a failure, not a
        success.

        Returns:
            (True, None) if an endpoint is reachable, (False, error_message)
            otherwise. The message lists every endpoint tried and why it failed.
        """
        failures: list[str] = []
        for index, base_url in enumerate(self._base_urls):
            ok, error = probe_endpoint(
                base_url, self._api_key, timeout=self._probe_timeout
            )
            if ok:
                if index != self._active_index:
                    logger.info("Local LLM endpoint switched to {}", base_url)
                    self._active_index = index
                return True, None
            failures.append(f"{base_url}: {error}")

        return False, "Cannot reach local LLM server - " + "; ".join(failures)


def _content_of(response: Any, model: str) -> str:
    """Extract and log the text content of a completion response.

    Args:
        response: Raw chat completion response.
        model: Model id that produced it, for logging.

    Returns:
        The stripped message content.

    Raises:
        LLMError: If the model was cut off by the token ceiling before it
            finished, so the answer is incomplete rather than wrong.
    """
    usage = getattr(response, "usage", None)
    if usage:
        logger.debug(
            "Local LLM usage: prompt_tokens={}, completion_tokens={}, model={}",
            usage.prompt_tokens,
            usage.completion_tokens,
            model,
        )
    choice = response.choices[0]
    if getattr(choice, "finish_reason", None) == "length":
        # A cut-off answer is not an answer. Left to itself it arrives as
        # unparseable JSON, and the caller reads that as the model having
        # judged the job and scored it zero -- a verdict that then gets cached,
        # rejecting the job for good because it was too long-winded.
        raise LLMError(
            f"{model} hit the token ceiling before finishing its answer "
            f"(completion_tokens={getattr(usage, 'completion_tokens', '?')})"
        )
    content = choice.message.content
    return (content or "").strip()


def _dedupe_urls(urls: list[str]) -> list[str]:
    """Normalise and de-duplicate endpoint URLs, preserving order.

    Args:
        urls: Candidate base URLs.

    Returns:
        Non-empty, de-duplicated URLs without trailing slashes.
    """
    seen: set[str] = set()
    out: list[str] = []
    for url in urls:
        cleaned = (url or "").strip().rstrip("/")
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            out.append(cleaned)
    return out or ["http://localhost:11434/v1"]


def probe_endpoint(
    base_url: str,
    api_key: str | None = None,
    *,
    timeout: float = 10.0,
) -> tuple[bool, str | None]:
    """Check one OpenAI-compatible endpoint by listing its models.

    Args:
        base_url: Base URL to probe (without a trailing slash).
        api_key: Optional API key.
        timeout: Read timeout in seconds; the connect timeout is half of it.

    Returns:
        (True, None) when the endpoint answers with a model list, otherwise
        (False, a human-readable reason).
    """
    import requests  # noqa: PLC0415

    url = base_url.rstrip("/") + _PROBE_PATH
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        response = requests.get(
            url, timeout=(max(1.0, timeout / 2), timeout), headers=headers
        )
    except requests.Timeout:
        return False, f"timed out after {timeout:g}s"
    except requests.RequestException as exc:
        return False, f"{type(exc).__name__}: {exc}"

    if not response.ok:
        return False, f"HTTP {response.status_code} from {url}"

    try:
        payload = response.json()
    except ValueError:
        return False, f"non-JSON response from {url}"

    if not isinstance(payload, dict) or "data" not in payload:
        return False, f"unexpected response shape from {url}"

    return True, None


def list_models(
    base_url: str,
    api_key: str | None = None,
    *,
    timeout: float = 10.0,
) -> list[str]:
    """List the model ids an endpoint advertises.

    Args:
        base_url: Base URL to query.
        api_key: Optional API key.
        timeout: Read timeout in seconds.

    Returns:
        Model ids, or an empty list when the response has none.

    Raises:
        requests.RequestException: If the endpoint cannot be reached.
        ValueError: If the response is not usable JSON.
    """
    import requests  # noqa: PLC0415

    url = base_url.rstrip("/") + _PROBE_PATH
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    response = requests.get(
        url, timeout=(max(1.0, timeout / 2), timeout), headers=headers
    )
    response.raise_for_status()
    payload = response.json()
    entries = payload.get("data", []) if isinstance(payload, dict) else []
    return [
        str(entry["id"])
        for entry in entries
        if isinstance(entry, dict) and "id" in entry
    ]
