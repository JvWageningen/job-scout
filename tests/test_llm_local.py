"""Tests for LocalLLMClient behaviour (openai SDK patched out)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from job_scout.llm.base import LLMError
from job_scout.llm.local import LocalLLMClient


def _make_client(
    evaluation_model: str = "llama3.1",
    screening_model: str | None = None,
) -> tuple[LocalLLMClient, MagicMock]:
    """Return a LocalLLMClient with a patched openai.OpenAI instance."""
    mock_openai_instance = MagicMock()
    with patch("openai.OpenAI", return_value=mock_openai_instance):
        client = LocalLLMClient(
            base_url="http://localhost:11434/v1",
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
    assert call_kwargs["model"] == "llama3.1"


def test_screening_uses_screening_model() -> None:
    """complete() with purpose='screening' passes screening_model to the API."""
    client, mock = _make_client(screening_model="llama2.1")
    mock.chat.completions.create.return_value = _fake_response('{"keep": [1]}')

    client.complete("prompt", purpose="screening")

    call_kwargs = mock.chat.completions.create.call_args[1]
    assert call_kwargs["model"] == "llama2.1"


def test_screening_falls_back_to_evaluation_model() -> None:
    """complete() falls back to evaluation_model when screening_model is unset."""
    client, mock = _make_client(screening_model=None)
    mock.chat.completions.create.return_value = _fake_response('{"keep": [1]}')

    client.complete("prompt", purpose="screening")

    call_kwargs = mock.chat.completions.create.call_args[1]
    assert call_kwargs["model"] == "llama3.1"


def test_keywords_uses_evaluation_model() -> None:
    """complete() with purpose='keywords' uses evaluation_model by default."""
    client, mock = _make_client()
    mock.chat.completions.create.return_value = _fake_response('{"dutch": []}')

    client.complete("prompt", purpose="keywords")

    call_kwargs = mock.chat.completions.create.call_args[1]
    assert call_kwargs["model"] == "llama3.1"


def test_quick_eval_uses_screening_model() -> None:
    """complete() with purpose='quick_eval' uses screening_model when set."""
    client, mock = _make_client(screening_model="llama2.1")
    mock.chat.completions.create.return_value = _fake_response('{"fit_score": 40}')

    client.complete("prompt", purpose="quick_eval")

    call_kwargs = mock.chat.completions.create.call_args[1]
    assert call_kwargs["model"] == "llama2.1"


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
    client._screening_timeout = 88
    mock.chat.completions.create.return_value = _fake_response("ok")

    client.complete("prompt", purpose="screening")

    assert mock.chat.completions.create.call_args[1]["timeout"] == 88


def test_timeout_override() -> None:
    """complete() respects the timeout parameter override."""
    client, mock = _make_client()
    client._evaluation_timeout = 99
    mock.chat.completions.create.return_value = _fake_response("ok")

    client.complete("prompt", purpose="evaluation", timeout=77)

    assert mock.chat.completions.create.call_args[1]["timeout"] == 77


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_openai_error_raises_llm_error() -> None:
    """complete() converts openai.OpenAIError to LLMError."""
    import openai

    client, mock = _make_client()
    mock.chat.completions.create.side_effect = openai.APIConnectionError(
        request=MagicMock()
    )

    with pytest.raises(LLMError, match="Local LLM API error"):
        client.complete("prompt", purpose="evaluation")


# ---------------------------------------------------------------------------
# Response handling
# ---------------------------------------------------------------------------


def test_response_content_stripped() -> None:
    """complete() returns stripped response content."""
    client, mock = _make_client()
    mock.chat.completions.create.return_value = _fake_response('  {"data": 1}  ')

    result = client.complete("prompt", purpose="evaluation")

    assert result == '{"data": 1}'


def test_empty_response_returns_empty_string() -> None:
    """complete() handles empty response content."""
    client, mock = _make_client()
    mock.chat.completions.create.return_value = _fake_response(None)

    result = client.complete("prompt", purpose="evaluation")

    assert result == ""


# ---------------------------------------------------------------------------
# check_available()
# ---------------------------------------------------------------------------


def test_check_available_success() -> None:
    """check_available() returns (True, None) when server is reachable."""
    with patch("openai.OpenAI"):
        client = LocalLLMClient(
            base_url="http://localhost:11434/v1",
            evaluation_model="llama3.1",
        )

    with patch("requests.get"):
        ok, err = client.check_available()
    assert ok is True
    assert err is None


def test_check_available_network_error() -> None:
    """check_available() returns (False, error) on network failure."""
    import requests

    with patch("openai.OpenAI"):
        client = LocalLLMClient(
            base_url="http://localhost:11434/v1",
            evaluation_model="llama3.1",
        )

    with patch("requests.get", side_effect=requests.ConnectionError("No server")):
        ok, err = client.check_available()
        assert ok is False
        assert "Cannot reach local LLM server" in err
