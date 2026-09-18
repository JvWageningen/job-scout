"""Tests for ClaudeCliClient: every way the CLI can fail is an LLM error.

No test starts the real binary: shutil.which and subprocess.run are patched.
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from job_scout.llm.base import LLMError, LLMUnavailableError
from job_scout.llm.claude_cli import CLAUDE_NOT_FOUND_MSG, ClaudeCliClient


@patch("shutil.which", return_value="/usr/local/bin/claude")
@patch("subprocess.run")
def test_the_answer_is_what_the_cli_printed(
    mock_run: MagicMock, _which: MagicMock
) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout=" {} \n", stderr="")

    assert ClaudeCliClient().complete("prompt", purpose="evaluation") == "{}"


@patch("shutil.which", return_value="/usr/local/bin/claude")
@patch(
    "subprocess.run",
    side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=90),
)
def test_a_timeout_is_an_llm_error(_run: MagicMock, _which: MagicMock) -> None:
    """Callers remember a failed lookup on LLMError; a raw timeout skipped that."""
    with pytest.raises(LLMError, match="timed out after 90"):
        ClaudeCliClient().complete("prompt", purpose="evaluation")


@patch("shutil.which", return_value="/usr/local/bin/claude")
@patch("subprocess.run", side_effect=FileNotFoundError("claude"))
def test_a_binary_that_vanished_lets_the_fallback_take_over(
    _run: MagicMock, _which: MagicMock
) -> None:
    """Only LLMUnavailableError makes the fallback provider answer instead."""
    with pytest.raises(LLMUnavailableError) as raised:
        ClaudeCliClient().complete("prompt", purpose="evaluation")

    assert str(raised.value) == CLAUDE_NOT_FOUND_MSG


@patch("shutil.which", return_value="/usr/local/bin/claude")
@patch("subprocess.run", side_effect=PermissionError("denied"))
def test_a_binary_that_cannot_start_is_unavailable(
    _run: MagicMock, _which: MagicMock
) -> None:
    with pytest.raises(LLMUnavailableError, match="could not be started"):
        ClaudeCliClient().complete("prompt", purpose="evaluation")


@patch("shutil.which", return_value="/usr/local/bin/claude")
@patch("subprocess.run")
def test_a_failed_run_is_an_llm_error(mock_run: MagicMock, _which: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=2, stdout="", stderr="boom")

    with pytest.raises(LLMError, match="exit 2"):
        ClaudeCliClient().complete("prompt", purpose="evaluation")
