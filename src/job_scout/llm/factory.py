"""Factory that builds the correct LLMClient from config."""

from __future__ import annotations

from typing import TYPE_CHECKING

from job_scout.llm.base import CallPurpose, LLMClient, LLMError
from job_scout.llm.claude_cli import ClaudeCliClient
from job_scout.llm.kilo_cli import KiloCliClient
from job_scout.llm.retry import RetryingLLMClient

if TYPE_CHECKING:
    from typing import Literal

    from job_scout.models import Config


def _build_raw_client(
    provider: Literal["claude_cli", "zai", "kilo_cli", "local"],
    config: Config,
) -> LLMClient:
    """Build a raw (non-retry-wrapped) LLMClient for a given provider.

    Args:
        provider: Provider name.
        config: Application configuration.

    Returns:
        A ready-to-use LLMClient instance (not wrapped with retry logic).

    Raises:
        LLMError: If the provider is misconfigured.
    """
    if provider == "zai":
        from job_scout.llm.zai import ZaiClient  # noqa: PLC0415

        if not config.zai_api_key:
            raise LLMError(
                "llm_provider is 'zai' but zai_api_key is not set.\n"
                "Run: job-scout config set zai_api_key <your-key>"
            )
        return ZaiClient(
            api_key=config.zai_api_key,
            base_url=config.zai_base_url,
            evaluation_model=config.zai_model,
            screening_model=config.zai_screening_model or config.zai_model,
            quick_eval_model=config.zai_quick_eval_model,
        )
    elif provider == "kilo_cli":
        return KiloCliClient(
            evaluation_model=config.kilo_evaluation_model,
            screening_model=config.kilo_screening_model,
            quick_eval_model=config.kilo_quick_eval_model,
        )
    elif provider == "local":
        from job_scout.llm.local import LocalLLMClient  # noqa: PLC0415

        return LocalLLMClient(
            base_url=config.local_base_url,
            evaluation_model=config.local_model,
            screening_model=config.local_screening_model,
            keywords_model=config.local_keywords_model,
            quick_eval_model=config.local_quick_eval_model,
            api_key=config.local_api_key,
            evaluation_timeout=config.local_evaluation_timeout,
            screening_timeout=config.local_screening_timeout,
        )
    else:
        return ClaudeCliClient(
            evaluation_model=config.claude_evaluation_model,
            screening_model=config.claude_screening_model,
        )


class _PurposeRoutingClient:
    """Routes LLM calls by purpose to different underlying clients.

    When per-purpose provider overrides are configured, dispatches each purpose
    to its configured provider's client. Otherwise, uses the default client for
    all purposes.
    """

    def __init__(
        self,
        default_client: LLMClient,
        overrides: dict[CallPurpose, LLMClient],
    ) -> None:
        """Initialise the routing client.

        Args:
            default_client: Client used for purposes without an override.
            overrides: Mapping of purpose to client for specific purposes.
        """
        self._default = default_client
        self._overrides = overrides

    def complete(
        self,
        prompt: str,
        *,
        purpose: CallPurpose,
        timeout: float | None = None,
    ) -> str:
        """Dispatch complete() to the appropriate client by purpose.

        Args:
            prompt: The prompt to send.
            purpose: The call purpose (determines which client to use).
            timeout: Optional timeout override.

        Returns:
            The LLM response text.

        Raises:
            LLMError: If the call fails.
        """
        client = self._overrides.get(purpose, self._default)
        return client.complete(prompt, purpose=purpose, timeout=timeout)

    def check_available(self) -> tuple[bool, str | None]:
        """Check availability of the default client and all override clients.

        Returns:
            (True, None) if all are available, (False, error_message) otherwise.
            The error message identifies which provider/purpose failed.
        """
        ok, err = self._default.check_available()
        if not ok:
            return False, f"Default provider: {err}"

        for purpose, client in self._overrides.items():
            ok, err = client.check_available()
            if not ok:
                return False, f"Provider for {purpose}: {err}"

        return True, None


def get_llm_client(config: Config) -> LLMClient:
    """Return the LLMClient configured by *config*, wrapped with retry logic.

    Per-purpose provider overrides are automatically routed: if
    quick_eval_provider, screening_provider, evaluation_provider, or
    keywords_provider differ from the default llm_provider, separate clients
    are instantiated and routed to by purpose.

    When no overrides are configured, behavior is identical to before: a single
    client is instantiated and used for all purposes.

    Args:
        config: Application configuration.

    Returns:
        A ready-to-use LLMClient instance with exponential-backoff retries.

    Raises:
        LLMError: If any selected provider is misconfigured.
    """
    default_provider = config.llm_provider
    default_client = _build_raw_client(default_provider, config)

    # Determine which purposes have overrides that differ from the default
    overrides: dict[CallPurpose, LLMClient] = {}
    for purpose in ["quick_eval", "screening", "evaluation", "keywords"]:
        override_attr = f"{purpose}_provider"
        override_provider = getattr(config, override_attr, None)
        if override_provider and override_provider != default_provider:
            overrides[purpose] = _build_raw_client(override_provider, config)  # type: ignore

    # If there are no overrides, use the default client directly
    inner: LLMClient
    if not overrides:
        inner = default_client
    else:
        inner = _PurposeRoutingClient(default_client, overrides)

    return RetryingLLMClient(
        inner, config.llm_max_attempts, config.llm_retry_base_delay
    )


def build_raw_client_for_test(
    provider: Literal["claude_cli", "zai", "kilo_cli", "local"],
    **kwargs: object,
) -> LLMClient:
    """Build a raw client for a specific provider with explicit parameters.

    This is a helper for testing a candidate provider configuration that hasn't
    been saved yet (e.g., the web dashboard's /api/llm/test-connection endpoint).

    Args:
        provider: Provider name.
        **kwargs: Provider-specific configuration (e.g., api_key, base_url, model).

    Returns:
        A raw (non-retry-wrapped) LLMClient instance.

    Raises:
        LLMError: If the provider is misconfigured.
    """
    from job_scout.models import Config  # noqa: PLC0415

    # Build a minimal config object with the provided parameters
    config_dict: dict[str, object] = {}
    if provider == "zai":
        config_dict.update(
            {
                "zai_api_key": kwargs.get("api_key", ""),
                "zai_base_url": kwargs.get(
                    "base_url", "https://api.z.ai/api/coding/paas/v4"
                ),
                "zai_model": kwargs.get("model", "glm-5.1"),
                "zai_screening_model": kwargs.get("screening_model"),
                "zai_quick_eval_model": kwargs.get("quick_eval_model"),
            }
        )
    elif provider == "local":
        config_dict.update(
            {
                "local_base_url": kwargs.get("base_url", "http://localhost:11434/v1"),
                "local_api_key": kwargs.get("api_key"),
                "local_model": kwargs.get("model", "llama3.1"),
                "local_screening_model": kwargs.get("screening_model"),
                "local_quick_eval_model": kwargs.get("quick_eval_model"),
                "local_keywords_model": kwargs.get("keywords_model"),
                "local_evaluation_timeout": kwargs.get("evaluation_timeout", 120),
                "local_screening_timeout": kwargs.get("screening_timeout", 90),
            }
        )
    elif provider == "kilo_cli":
        config_dict.update(
            {
                "kilo_evaluation_model": kwargs.get("model", "zai/glm-5.1"),
                "kilo_screening_model": kwargs.get(
                    "screening_model", "zai/glm-4.5-air"
                ),
                "kilo_quick_eval_model": kwargs.get(
                    "quick_eval_model", "zai/glm-4.5-air"
                ),
            }
        )
    else:  # claude_cli
        config_dict.update(
            {
                "claude_evaluation_model": kwargs.get("model"),
                "claude_screening_model": kwargs.get("screening_model", "haiku"),
            }
        )

    config = Config(**config_dict)
    return _build_raw_client(provider, config)
