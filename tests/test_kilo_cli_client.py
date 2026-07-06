"""Tests for KiloCliClient."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from job_scout.llm.base import LLMError
from job_scout.llm.kilo_cli import KILO_NOT_FOUND_MSG, KiloCliClient


def _make_ndjson(*text_parts: str, output_tokens: int = 10) -> str:
    """Build a minimal Kilo NDJSON response."""
    lines = []
    for text in text_parts:
        lines.append(json.dumps({"type": "text", "part": {"text": text}}))
    lines.append(
        json.dumps(
            {
                "type": "step_finish",
                "part": {
                    "tokens": {"output": output_tokens, "total": output_tokens + 100}
                },
                "cost": "0.001",
            }
        )
    )
    return "\n".join(lines)


def _make_client(**kwargs: object) -> KiloCliClient:
    return KiloCliClient(
        evaluation_model=kwargs.get("evaluation_model", "zai/glm-5.1"),  # type: ignore[arg-type]
        screening_model=kwargs.get("screening_model", "zai/glm-4.5-air"),  # type: ignore[arg-type]
    )


@patch("shutil.which", return_value="/usr/local/bin/kilo")
@patch("subprocess.run")
def test_evaluation_uses_evaluation_model(
    mock_run: MagicMock, _which: MagicMock
) -> None:
    mock_run.return_value = MagicMock(
        returncode=0, stdout=_make_ndjson('{"fit_score": 80}'), stderr=""
    )
    client = _make_client()
    result = client.complete("prompt", purpose="evaluation")
    assert result == '{"fit_score": 80}'
    cmd = mock_run.call_args[0][0]
    assert "--model" in cmd
    assert cmd[cmd.index("--model") + 1] == "zai/glm-5.1"


@patch("shutil.which", return_value="/usr/local/bin/kilo")
@patch("subprocess.run")
def test_screening_uses_screening_model(mock_run: MagicMock, _which: MagicMock) -> None:
    mock_run.return_value = MagicMock(
        returncode=0, stdout=_make_ndjson('{"keep": [1, 2]}'), stderr=""
    )
    client = _make_client()
    client.complete("prompt", purpose="screening")
    cmd = mock_run.call_args[0][0]
    assert cmd[cmd.index("--model") + 1] == "zai/glm-4.5-air"


@patch("shutil.which", return_value="/usr/local/bin/kilo")
@patch("subprocess.run")
def test_keywords_uses_evaluation_model(mock_run: MagicMock, _which: MagicMock) -> None:
    mock_run.return_value = MagicMock(
        returncode=0, stdout=_make_ndjson('{"dutch": []}'), stderr=""
    )
    client = _make_client()
    client.complete("prompt", purpose="keywords")
    cmd = mock_run.call_args[0][0]
    assert cmd[cmd.index("--model") + 1] == "zai/glm-5.1"


@patch("shutil.which", return_value="/usr/local/bin/kilo")
@patch("subprocess.run")
def test_explicit_timeout_overrides_default(
    mock_run: MagicMock, _which: MagicMock
) -> None:
    mock_run.return_value = MagicMock(
        returncode=0, stdout=_make_ndjson("{}"), stderr=""
    )
    client = KiloCliClient(evaluation_timeout=120, screening_timeout=90)
    client.complete("prompt", purpose="evaluation", timeout=30)
    assert mock_run.call_args[1]["timeout"] == 30


@patch("shutil.which", return_value="/usr/local/bin/kilo")
@patch("subprocess.run")
def test_auto_and_format_json_flags_present(
    mock_run: MagicMock, _which: MagicMock
) -> None:
    mock_run.return_value = MagicMock(
        returncode=0, stdout=_make_ndjson("{}"), stderr=""
    )
    client = _make_client()
    client.complete("my prompt", purpose="evaluation")
    cmd = mock_run.call_args[0][0]
    assert "--auto" in cmd
    assert "--format" in cmd
    assert cmd[cmd.index("--format") + 1] == "json"
    assert "my prompt" in cmd


@patch("shutil.which", return_value="/usr/local/bin/kilo")
@patch("subprocess.run")
def test_multiple_text_events_concatenated(
    mock_run: MagicMock, _which: MagicMock
) -> None:
    mock_run.return_value = MagicMock(
        returncode=0,
        stdout=_make_ndjson('{"fit_score":', " 75}"),
        stderr="",
    )
    client = _make_client()
    result = client.complete("prompt", purpose="evaluation")
    assert result == '{"fit_score": 75}'


@patch("shutil.which", return_value="/usr/local/bin/kilo")
@patch("subprocess.run")
def test_no_text_events_raises_llm_error(
    mock_run: MagicMock, _which: MagicMock
) -> None:
    ndjson = json.dumps({"type": "step_finish", "part": {"tokens": {"output": 0}}})
    mock_run.return_value = MagicMock(returncode=0, stdout=ndjson, stderr="")
    client = _make_client()
    with pytest.raises(LLMError, match="no text content"):
        client.complete("prompt", purpose="evaluation")


@patch("shutil.which", return_value="/usr/local/bin/kilo")
@patch(
    "subprocess.run",
    side_effect=subprocess.TimeoutExpired(cmd="kilo", timeout=60),
)
def test_timeout_raises_llm_error(_mock_run: MagicMock, _which: MagicMock) -> None:
    client = _make_client()
    with pytest.raises(LLMError, match="timed out"):
        client.complete("prompt", purpose="evaluation")


@patch("shutil.which", return_value=None)
def test_check_available_false_when_missing(_which: MagicMock) -> None:
    client = _make_client()
    ok, msg = client.check_available()
    assert not ok
    assert msg == KILO_NOT_FOUND_MSG


@patch("shutil.which", return_value="/usr/local/bin/kilo")
def test_check_available_true_when_present(_which: MagicMock) -> None:
    client = _make_client()
    ok, msg = client.check_available()
    assert ok
    assert msg is None


@patch("shutil.which", return_value=None)
def test_complete_raises_when_kilo_missing(_which: MagicMock) -> None:
    client = _make_client()
    with pytest.raises(LLMError):
        client.complete("prompt", purpose="evaluation")
