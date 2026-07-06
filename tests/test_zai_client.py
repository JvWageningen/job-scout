"""Tests for ZaiClient behaviour (openai SDK patched out)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from job_scout.llm.base import LLMError
from job_scout.llm.zai import ZaiClient


def _make_client(
    evaluation_model: str = "glm-5.1",
    screening_model: str = "glm-4.5-air",
) -> tuple[ZaiClient, MagicMock]:
    """Return a ZaiClient with a patched openai.OpenAI instance."""
    mock_openai_instance = MagicMock()
    with patch("openai.OpenAI", return_value=mock_openai_instance):
        client = ZaiClient(
            api_key="sk-test",
            base_url="https://api.z.ai/api/coding/paas/v4",
            evaluation_model=evaluation_model,
            screening_model=screening_model,
        )
    client._client = mock_openai_instance
    return client, mock_openai_instance


def _fake_response(
    content: str, prompt_tokens: int = 10, completion_tokens: int = 20
) -> MagicMock:
    """Build a minimal chat completion response mock."""
    choice = SimpleNamespace(message=SimpleNamespace(content=content))
    usage = SimpleNamespace(
        prompt_tokens=prompt_tokens, completion_tokens=completion_tokens
    )
    response = MagicMock()
    response.choices = [choice]
    response.usage = usage
    return response


# ---------------------------------------------------------------------------
# Model selection by purpose
# ---------------------------------------------------------------------------


def test_evaluation_uses_evaluation_model() -> None:
    """complete() with purpose='evaluation' passes evaluation_model to the API."""
    client, mock = _make_client()
    mock.chat.completions.create.return_value = _fake_response('{"fit_score": 80}')

    client.complete("prompt", purpose="evaluation")

    call_kwargs = mock.chat.completions.create.call_args[1]
    assert call_kwargs["model"] == "glm-5.1"


def test_screening_uses_screening_model() -> None:
    """complete() with purpose='screening' passes screening_model to the API."""
    client, mock = _make_client()
    mock.chat.completions.create.return_value = _fake_response('{"keep": [1]}')

    client.complete("prompt", purpose="screening")

    call_kwargs = mock.chat.completions.create.call_args[1]
    assert call_kwargs["model"] == "glm-4.5-air"


def test_keywords_uses_evaluation_model() -> None:
    """complete() with purpose='keywords' uses evaluation_model by default."""
    client, mock = _make_client()
    mock.chat.completions.create.return_value = _fake_response('{"dutch": []}')

    client.complete("prompt", purpose="keywords")

    call_kwargs = mock.chat.completions.create.call_args[1]
    assert call_kwargs["model"] == "glm-5.1"


# ---------------------------------------------------------------------------
# Timeout forwarding
# ---------------------------------------------------------------------------


def test_evaluation_uses_evaluation_timeout() -> None:
    """complete() uses evaluation_timeout for evaluation calls."""
    client, mock = _make_client()
    client._evaluation_timeout = 99
    mock.chat.completions.create.return_value = _fake_response("ok")

    client.complete("prompt", purpose="evaluation")

    assert mock.chat.completions.create.call_args[1]["timeout"] == 99


def test_screening_uses_screening_timeout() -> None:
    """complete() uses screening_timeout for screening calls."""
    client, mock = _make_client()
    client._screening_timeout = 55
    mock.chat.completions.create.return_value = _fake_response("ok")

    client.complete("prompt", purpose="screening")

    assert mock.chat.completions.create.call_args[1]["timeout"] == 55


def test_explicit_timeout_overrides_default() -> None:
    """An explicit timeout= kwarg overrides the purpose-based default."""
    client, mock = _make_client()
    mock.chat.completions.create.return_value = _fake_response("ok")

    client.complete("prompt", purpose="evaluation", timeout=7.5)

    assert mock.chat.completions.create.call_args[1]["timeout"] == 7.5


# ---------------------------------------------------------------------------
# Content extraction
# ---------------------------------------------------------------------------


def test_returns_stripped_content() -> None:
    """complete() strips leading/trailing whitespace from the response."""
    client, mock = _make_client()
    mock.chat.completions.create.return_value = _fake_response("  result text  ")

    result = client.complete("prompt", purpose="evaluation")

    assert result == "result text"


def test_returns_empty_string_on_none_content() -> None:
    """complete() returns an empty string when content is None."""
    client, mock = _make_client()
    mock.chat.completions.create.return_value = _fake_response(None)  # type: ignore[arg-type]

    result = client.complete("prompt", purpose="evaluation")

    assert result == ""


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_openai_error_raises_llm_error() -> None:
    """complete() wraps openai.OpenAIError as LLMError."""
    import openai

    client, mock = _make_client()
    mock.chat.completions.create.side_effect = openai.APIConnectionError(
        request=MagicMock()
    )

    with pytest.raises(LLMError, match="Z AI API error"):
        client.complete("prompt", purpose="evaluation")


# ---------------------------------------------------------------------------
# check_available
# ---------------------------------------------------------------------------


def test_check_available_returns_true() -> None:
    """check_available() returns (True, None) without any network call."""
    client, mock = _make_client()

    ok, err = client.check_available()

    assert ok is True
    assert err is None
    mock.chat.completions.create.assert_not_called()


# ---------------------------------------------------------------------------
# API key and base_url forwarding
# ---------------------------------------------------------------------------


def test_api_key_and_base_url_forwarded_to_openai() -> None:
    """ZaiClient passes api_key and base_url to openai.OpenAI."""
    with patch("openai.OpenAI") as mock_cls:
        mock_cls.return_value = MagicMock()
        ZaiClient(
            api_key="sk-secret",
            base_url="https://api.z.ai/api/coding/paas/v4",
            evaluation_model="glm-5.1",
            screening_model="glm-4.5-air",
        )

    mock_cls.assert_called_once_with(
        api_key="sk-secret",
        base_url="https://api.z.ai/api/coding/paas/v4",
        max_retries=0,
    )
