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
    # The client builds one openai client per (endpoint, read timeout); short
    # -circuit that cache so every endpoint resolves to the mock.
    client._client_for = lambda base_url, read_timeout: mock_openai_instance  # type: ignore[method-assign]
    return client, mock_openai_instance


def _models_response(*model_ids: str) -> MagicMock:
    """Build a realistic GET /v1/models response mock."""
    response = MagicMock()
    response.ok = True
    response.status_code = 200
    response.json.return_value = {
        "object": "list",
        "data": [{"id": mid, "object": "model"} for mid in model_ids],
    }
    return response


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
    """complete() converts a server-side openai.OpenAIError to LLMError."""
    import openai

    client, mock = _make_client()
    mock.chat.completions.create.side_effect = openai.BadRequestError(
        "unknown model", response=MagicMock(status_code=400), body=None
    )

    with pytest.raises(LLMError, match="Local LLM API error"):
        client.complete("prompt", purpose="evaluation")


def test_connection_error_raises_llm_error() -> None:
    """A transport failure on the only endpoint still surfaces as LLMError."""
    import openai

    client, mock = _make_client()
    mock.chat.completions.create.side_effect = openai.APIConnectionError(
        request=MagicMock()
    )

    with pytest.raises(LLMError, match="No local LLM endpoint reachable"):
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

    with patch("requests.get", return_value=_models_response("llama3.1")):
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


# ---------------------------------------------------------------------------
# Multi-endpoint failover
#
# Regression: a single unreachable base_url used to take down every pipeline
# stage. The model host is commonly reachable by more than one route, so the
# client now walks a priority list.
# ---------------------------------------------------------------------------


def _multi_client() -> LocalLLMClient:
    """Return a client with a primary and one fallback endpoint."""
    with patch("openai.OpenAI"):
        return LocalLLMClient(
            base_url="http://primary:8080/v1",
            evaluation_model="llama3.1",
            fallback_base_urls=["http://fallback:8080/v1"],
        )


def test_complete_falls_back_when_primary_unreachable() -> None:
    """A connection error on the primary transparently retries the fallback."""
    import openai

    client = _multi_client()
    dead = MagicMock()
    dead.chat.completions.create.side_effect = openai.APIConnectionError(
        request=MagicMock()
    )
    alive = MagicMock()
    alive.chat.completions.create.return_value = _fake_response('{"fit_score": 80}')

    client._client_for = lambda base_url, read_timeout: (  # type: ignore[method-assign]
        dead if "primary" in base_url else alive
    )

    assert client.complete("prompt", purpose="evaluation") == '{"fit_score": 80}'
    # The healthy endpoint becomes the active one, so later calls start there.
    assert client.base_url == "http://fallback:8080/v1"


def test_complete_raises_when_every_endpoint_is_down() -> None:
    """With no endpoint reachable the error names each one tried."""
    import openai

    client = _multi_client()
    dead = MagicMock()
    dead.chat.completions.create.side_effect = openai.APIConnectionError(
        request=MagicMock()
    )
    client._client_for = lambda base_url, read_timeout: dead  # type: ignore[method-assign]

    with pytest.raises(LLMError) as excinfo:
        client.complete("prompt", purpose="evaluation")
    assert "primary" in str(excinfo.value)
    assert "fallback" in str(excinfo.value)


def test_api_error_does_not_trigger_failover() -> None:
    """A server that answered with an error is not retried elsewhere."""
    import openai

    client = _multi_client()
    responder = MagicMock()
    responder.chat.completions.create.side_effect = openai.BadRequestError(
        "unknown model", response=MagicMock(status_code=400), body=None
    )
    client._client_for = lambda base_url, read_timeout: responder  # type: ignore[method-assign]

    with pytest.raises(LLMError):
        client.complete("prompt", purpose="evaluation")
    # Only the primary was attempted.
    assert responder.chat.completions.create.call_count == 1


def test_check_available_prefers_first_healthy_endpoint() -> None:
    """check_available promotes the first endpoint that answers."""
    import requests

    client = _multi_client()

    def fake_get(url: str, **_: object) -> MagicMock:
        if "primary" in url:
            raise requests.ConnectionError("no route")
        return _models_response("llama3.1")

    with patch("requests.get", side_effect=fake_get):
        ok, err = client.check_available()

    assert ok is True
    assert err is None
    assert client.base_url == "http://fallback:8080/v1"


def test_check_available_rejects_non_ok_status() -> None:
    """A 404 on /v1/models is a failure, not a healthy server."""
    with patch("openai.OpenAI"):
        client = LocalLLMClient(
            base_url="http://localhost:11434/v1", evaluation_model="llama3.1"
        )

    not_found = MagicMock()
    not_found.ok = False
    not_found.status_code = 404

    with patch("requests.get", return_value=not_found):
        ok, err = client.check_available()

    assert ok is False
    assert "404" in err


def test_check_available_rejects_unexpected_payload() -> None:
    """An HTTP 200 from an unrelated web server is not a healthy endpoint."""
    with patch("openai.OpenAI"):
        client = LocalLLMClient(
            base_url="http://localhost:11434/v1", evaluation_model="llama3.1"
        )

    stray = MagicMock()
    stray.ok = True
    stray.status_code = 200
    stray.json.return_value = {"hello": "world"}

    with patch("requests.get", return_value=stray):
        ok, err = client.check_available()

    assert ok is False
    assert "unexpected response shape" in err
