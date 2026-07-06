"""Tests for the LLM provider factory."""

from __future__ import annotations

import pytest

from job_scout.llm.base import LLMError
from job_scout.llm.claude_cli import ClaudeCliClient
from job_scout.llm.factory import get_llm_client
from job_scout.llm.retry import RetryingLLMClient
from job_scout.llm.zai import ZaiClient
from job_scout.models import Config


def test_factory_returns_retrying_client() -> None:
    """get_llm_client wraps every provider in RetryingLLMClient."""
    config = Config()
    client = get_llm_client(config)
    assert isinstance(client, RetryingLLMClient)


def test_factory_returns_claude_cli_by_default() -> None:
    """get_llm_client wraps ClaudeCliClient when llm_provider is claude_cli."""
    config = Config()
    assert config.llm_provider == "claude_cli"
    client = get_llm_client(config)
    assert isinstance(client, RetryingLLMClient)
    assert isinstance(client.inner, ClaudeCliClient)


def test_factory_returns_zai_when_configured() -> None:
    """get_llm_client wraps ZaiClient when llm_provider is zai."""
    config = Config(llm_provider="zai", zai_api_key="sk-test")
    client = get_llm_client(config)
    assert isinstance(client, RetryingLLMClient)
    assert isinstance(client.inner, ZaiClient)


def test_factory_raises_when_zai_missing_key() -> None:
    """get_llm_client raises LLMError when llm_provider='zai' with no zai_api_key."""
    config = Config(llm_provider="zai", zai_api_key=None)
    with pytest.raises(LLMError, match="zai_api_key"):
        get_llm_client(config)


def test_factory_passes_evaluation_model_to_zai() -> None:
    """get_llm_client passes zai_model as the evaluation_model to ZaiClient."""
    config = Config(llm_provider="zai", zai_api_key="sk-test", zai_model="glm-5.1")
    client = get_llm_client(config)
    assert isinstance(client, RetryingLLMClient)
    assert isinstance(client.inner, ZaiClient)
    assert client.inner._evaluation_model == "glm-5.1"


def test_factory_screening_model_falls_back_to_zai_model() -> None:
    """screening_model falls back to zai_model when zai_screening_model is unset."""
    config = Config(
        llm_provider="zai",
        zai_api_key="sk-test",
        zai_model="glm-5.1",
        zai_screening_model=None,
    )
    client = get_llm_client(config)
    assert isinstance(client, RetryingLLMClient)
    assert isinstance(client.inner, ZaiClient)
    assert client.inner._screening_model == "glm-5.1"


def test_factory_passes_screening_model_to_zai() -> None:
    """get_llm_client passes zai_screening_model when set."""
    config = Config(
        llm_provider="zai",
        zai_api_key="sk-test",
        zai_screening_model="glm-4.5-air",
    )
    client = get_llm_client(config)
    assert isinstance(client, RetryingLLMClient)
    assert isinstance(client.inner, ZaiClient)
    assert client.inner._screening_model == "glm-4.5-air"


def test_zai_check_available_without_network() -> None:
    """ZaiClient.check_available() returns True without making a network call."""
    config = Config(llm_provider="zai", zai_api_key="sk-test")
    client = get_llm_client(config)
    assert isinstance(client, RetryingLLMClient)
    assert isinstance(client.inner, ZaiClient)

    ok, err = client.check_available()
    assert ok is True
    assert err is None


def test_factory_returns_kilo_cli_when_configured() -> None:
    """get_llm_client wraps KiloCliClient when llm_provider is kilo_cli."""
    from job_scout.llm.kilo_cli import KiloCliClient

    config = Config(llm_provider="kilo_cli")
    client = get_llm_client(config)
    assert isinstance(client, RetryingLLMClient)
    assert isinstance(client.inner, KiloCliClient)


def test_factory_passes_kilo_models() -> None:
    """get_llm_client forwards kilo model fields to KiloCliClient."""
    from job_scout.llm.kilo_cli import KiloCliClient

    config = Config(
        llm_provider="kilo_cli",
        kilo_evaluation_model="zai/glm-5.1",
        kilo_screening_model="zai/glm-4.5-air",
    )
    client = get_llm_client(config)
    assert isinstance(client, RetryingLLMClient)
    assert isinstance(client.inner, KiloCliClient)
    assert client.inner._evaluation_model == "zai/glm-5.1"
    assert client.inner._screening_model == "zai/glm-4.5-air"


# ---------------------------------------------------------------------------
# Local LLM provider
# ---------------------------------------------------------------------------


def test_factory_returns_local_when_configured() -> None:
    """get_llm_client wraps LocalLLMClient when llm_provider is local."""
    from job_scout.llm.local import LocalLLMClient

    config = Config(llm_provider="local")
    client = get_llm_client(config)
    assert isinstance(client, RetryingLLMClient)
    assert isinstance(client.inner, LocalLLMClient)


def test_factory_passes_local_models() -> None:
    """get_llm_client forwards local model fields to LocalLLMClient."""
    from job_scout.llm.local import LocalLLMClient

    config = Config(
        llm_provider="local",
        local_model="llama3.1",
        local_screening_model="llama2.1",
        local_base_url="http://localhost:11434/v1",
    )
    client = get_llm_client(config)
    assert isinstance(client, RetryingLLMClient)
    assert isinstance(client.inner, LocalLLMClient)
    assert client.inner._evaluation_model == "llama3.1"
    assert client.inner._screening_model == "llama2.1"


# ---------------------------------------------------------------------------
# Per-purpose provider overrides (routing)
# ---------------------------------------------------------------------------


def test_no_overrides_returns_single_client() -> None:
    """When no per-purpose overrides are set, behavior is unchanged."""
    config = Config(llm_provider="claude_cli")
    client = get_llm_client(config)
    assert isinstance(client, RetryingLLMClient)
    assert isinstance(client.inner, ClaudeCliClient)
    assert not hasattr(client.inner, "_overrides")


def test_override_quick_eval_provider() -> None:
    """quick_eval_provider override routes quick_eval to a different client."""
    from job_scout.llm.local import LocalLLMClient

    config = Config(
        llm_provider="claude_cli",
        quick_eval_provider="local",
    )
    client = get_llm_client(config)
    assert isinstance(client, RetryingLLMClient)

    # The inner should be a routing client
    from job_scout.llm.factory import _PurposeRoutingClient

    assert isinstance(client.inner, _PurposeRoutingClient)
    # Check that quick_eval maps to LocalLLMClient
    assert isinstance(client.inner._overrides.get("quick_eval"), LocalLLMClient)
    # Check that default is ClaudeCliClient
    assert isinstance(client.inner._default, ClaudeCliClient)


def test_override_screening_provider() -> None:
    """screening_provider override routes screening to a different client."""
    from job_scout.llm.zai import ZaiClient

    config = Config(
        llm_provider="claude_cli",
        screening_provider="zai",
        zai_api_key="sk-test",
    )
    client = get_llm_client(config)
    assert isinstance(client, RetryingLLMClient)

    from job_scout.llm.factory import _PurposeRoutingClient

    assert isinstance(client.inner, _PurposeRoutingClient)
    assert isinstance(client.inner._overrides.get("screening"), ZaiClient)
    assert isinstance(client.inner._default, ClaudeCliClient)


def test_override_evaluation_provider() -> None:
    """evaluation_provider override routes evaluation to a different client."""
    from job_scout.llm.zai import ZaiClient

    config = Config(
        llm_provider="claude_cli",
        evaluation_provider="zai",
        zai_api_key="sk-test",
    )
    client = get_llm_client(config)
    assert isinstance(client, RetryingLLMClient)

    from job_scout.llm.factory import _PurposeRoutingClient

    assert isinstance(client.inner, _PurposeRoutingClient)
    assert isinstance(client.inner._overrides.get("evaluation"), ZaiClient)


def test_override_keywords_provider() -> None:
    """keywords_provider override routes keywords to a different client."""
    from job_scout.llm.local import LocalLLMClient

    config = Config(
        llm_provider="zai",
        zai_api_key="sk-test",
        keywords_provider="local",
    )
    client = get_llm_client(config)
    assert isinstance(client, RetryingLLMClient)

    from job_scout.llm.factory import _PurposeRoutingClient

    assert isinstance(client.inner, _PurposeRoutingClient)
    assert isinstance(client.inner._overrides.get("keywords"), LocalLLMClient)
    assert isinstance(client.inner._default, ZaiClient)


def test_multiple_overrides() -> None:
    """Multiple per-purpose overrides are all configured."""
    from job_scout.llm.local import LocalLLMClient
    from job_scout.llm.zai import ZaiClient

    config = Config(
        llm_provider="claude_cli",
        quick_eval_provider="local",
        screening_provider="zai",
        zai_api_key="sk-test",
    )
    client = get_llm_client(config)
    assert isinstance(client, RetryingLLMClient)

    from job_scout.llm.factory import _PurposeRoutingClient

    assert isinstance(client.inner, _PurposeRoutingClient)
    assert isinstance(client.inner._overrides.get("quick_eval"), LocalLLMClient)
    assert isinstance(client.inner._overrides.get("screening"), ZaiClient)
    assert isinstance(client.inner._default, ClaudeCliClient)


def test_override_same_as_default_not_duplicated() -> None:
    """If override equals default provider, no routing client is created."""
    config = Config(
        llm_provider="zai",
        zai_api_key="sk-test",
        quick_eval_provider="zai",  # Same as default
    )
    client = get_llm_client(config)
    assert isinstance(client, RetryingLLMClient)
    # Should be a plain ZaiClient, not a routing client
    assert isinstance(client.inner, ZaiClient)


def test_backward_compatibility_no_overrides() -> None:
    """With no overrides, get_llm_client behavior is identical to before."""
    config = Config(llm_provider="zai", zai_api_key="sk-test")
    client = get_llm_client(config)
    assert isinstance(client, RetryingLLMClient)
    assert isinstance(client.inner, ZaiClient)
    # The inner client should be directly accessible for model checks
    assert client.inner._evaluation_model == "glm-5.1"


def test_cv_parsing_provider_override() -> None:
    """cv_parsing_provider overrides the default provider for cv_parsing purpose."""
    from job_scout.llm.factory import _PurposeRoutingClient
    from job_scout.llm.local import LocalLLMClient
    from job_scout.llm.zai import ZaiClient

    config = Config(
        llm_provider="zai",
        zai_api_key="sk-test",
        cv_parsing_provider="local",
    )
    client = get_llm_client(config)
    assert isinstance(client, RetryingLLMClient)
    assert isinstance(client.inner, _PurposeRoutingClient)
    assert isinstance(client.inner._overrides.get("cv_parsing"), LocalLLMClient)
    assert isinstance(client.inner._default, ZaiClient)


def test_cv_parsing_same_as_default_not_duplicated() -> None:
    """If cv_parsing_provider equals default, no override is created."""
    config = Config(
        llm_provider="claude_cli",
        cv_parsing_provider="claude_cli",  # Same as default
    )
    client = get_llm_client(config)
    assert isinstance(client, RetryingLLMClient)
    from job_scout.llm.factory import _PurposeRoutingClient

    # Should be a plain client, not routing
    assert not isinstance(client.inner, _PurposeRoutingClient)
