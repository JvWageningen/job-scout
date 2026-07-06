"""Tests for cron schedule management."""

from __future__ import annotations

from subprocess import CompletedProcess
from typing import Any
from unittest.mock import patch

import pytest

from job_scout.scheduler import (
    _CRON_MARKER,
    _get_marker_for_user,
    _read_crontab,
    _write_crontab,
    check_schedule_status,
    install_schedule,
    remove_schedule,
)


def _done(  # noqa: E501
    returncode: int = 0, stdout: str = "", stderr: str = ""
) -> CompletedProcess[str]:
    """Build a fake CompletedProcess."""
    return CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr
    )


# Tests for marker generation
def test_marker_for_global_schedule() -> None:
    """_get_marker_for_user(None) returns the plain marker for global schedule."""
    marker = _get_marker_for_user(None)
    assert marker == _CRON_MARKER


def test_marker_for_user_schedule() -> None:
    """_get_marker_for_user(user) returns user-specific marker."""
    marker = _get_marker_for_user("alice")
    assert marker == f"{_CRON_MARKER}:alice"


# Tests for basic crontab operations
def test_read_crontab_returns_stdout() -> None:
    """_read_crontab returns subprocess stdout on success."""
    crontab_output = _done(stdout="0 8 * * * cmd\n")
    with patch("subprocess.run", return_value=crontab_output) as mock_run:
        result = _read_crontab()
    assert result == "0 8 * * * cmd\n"
    mock_run.assert_called_once_with(["crontab", "-l"], capture_output=True, text=True)


def test_read_crontab_empty_when_no_crontab() -> None:
    """_read_crontab returns empty string when crontab -l fails (no crontab)."""
    with patch("subprocess.run", return_value=_done(returncode=1)):
        result = _read_crontab()
    assert result == ""


def test_write_crontab_passes_input() -> None:
    """_write_crontab passes content via stdin to crontab -."""
    with patch("subprocess.run", return_value=_done()) as mock_run:
        _write_crontab("content\n")
    mock_run.assert_called_once_with(
        ["crontab", "-"], input="content\n", capture_output=True, text=True
    )


def test_write_crontab_raises_on_failure() -> None:
    """_write_crontab raises RuntimeError when crontab returns non-zero exit code."""
    failed = _done(returncode=1, stderr="error msg")
    with (
        patch("subprocess.run", return_value=failed),
        pytest.raises(RuntimeError, match="crontab write failed"),
    ):
        _write_crontab("bad\n")


# Tests for global schedule (backward compatibility)
def test_install_global_schedule_adds_marker() -> None:
    """install_schedule with no user adds global marker."""
    written: list[str] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
        if "-l" in cmd:
            return _done(stdout="")
        written.append(str(kwargs.get("input", "")))
        return _done()

    with patch("subprocess.run", side_effect=fake_run):
        install_schedule(hour=9, minute=30, user=None)

    assert written
    assert _CRON_MARKER in written[0]
    assert "--all" in written[0]


def test_install_global_schedule_includes_days() -> None:
    """install_schedule encodes days in cron expression."""
    written: list[str] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
        if "-l" in cmd:
            return _done(stdout="")
        written.append(str(kwargs.get("input", "")))
        return _done()

    with patch("subprocess.run", side_effect=fake_run):
        install_schedule(hour=8, minute=0, days="1-5", user=None)

    assert written
    assert "1-5" in written[0]


# Tests for per-user schedule
def test_install_user_schedule_uses_user_marker() -> None:
    """install_schedule with user adds user-specific marker."""
    written: list[str] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
        if "-l" in cmd:
            return _done(stdout="")
        written.append(str(kwargs.get("input", "")))
        return _done()

    with patch("subprocess.run", side_effect=fake_run):
        install_schedule(hour=9, minute=30, user="alice")

    assert written
    assert f"{_CRON_MARKER}:alice" in written[0]
    assert "--user alice" in written[0]


def test_install_user_schedule_respects_days() -> None:
    """install_schedule for user respects day specification."""
    written: list[str] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
        if "-l" in cmd:
            return _done(stdout="")
        written.append(str(kwargs.get("input", "")))
        return _done()

    with patch("subprocess.run", side_effect=fake_run):
        install_schedule(hour=10, minute=15, days="0,6", user="bob")

    assert written
    assert "0,6" in written[0]


def test_install_schedule_replaces_existing_global_entry() -> None:
    """install_schedule removes old global entries before adding new one."""
    old = f"0 8 * * * old-cmd run >> /dev/null 2>&1 {_CRON_MARKER}"
    written: list[str] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
        if "-l" in cmd:
            return _done(stdout=old + "\n")
        written.append(str(kwargs.get("input", "")))
        return _done()

    with patch("subprocess.run", side_effect=fake_run):
        install_schedule(hour=10, minute=0, user=None)

    assert written
    # Only one managed entry should remain after replacement
    assert written[0].count(_CRON_MARKER) == 1


def test_install_schedule_replaces_existing_user_entry() -> None:
    """install_schedule removes old user entries before adding new one."""
    old = f"0 8 * * * cmd run {_CRON_MARKER}:alice\n"
    written: list[str] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
        if "-l" in cmd:
            return _done(stdout=old + "\n")
        written.append(str(kwargs.get("input", "")))
        return _done()

    with patch("subprocess.run", side_effect=fake_run):
        install_schedule(hour=10, minute=0, user="alice")

    assert written
    assert written[0].count(f"{_CRON_MARKER}:alice") == 1


def test_install_schedule_preserves_other_entries() -> None:
    """install_schedule keeps unrelated cron entries intact."""
    existing = "0 12 * * * /usr/bin/backup.sh\n"
    written: list[str] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
        if "-l" in cmd:
            return _done(stdout=existing)
        written.append(str(kwargs.get("input", "")))
        return _done()

    with patch("subprocess.run", side_effect=fake_run):
        install_schedule(hour=8, minute=0, user="alice")

    assert written
    assert "/usr/bin/backup.sh" in written[0]


def test_install_multiple_user_schedules() -> None:
    """Multiple user schedules can coexist in crontab."""
    written: list[str] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
        if "-l" in cmd:
            return _done(stdout="")
        written.append(str(kwargs.get("input", "")))
        return _done()

    with patch("subprocess.run", side_effect=fake_run):
        install_schedule(hour=8, minute=0, user="alice")
        install_schedule(hour=9, minute=0, user="bob")

    assert len(written) == 2
    assert f"{_CRON_MARKER}:alice" in written[0]
    assert f"{_CRON_MARKER}:alice" not in written[1]
    assert f"{_CRON_MARKER}:bob" in written[1]


# Tests for schedule removal
def test_remove_global_schedule() -> None:
    """remove_schedule with no user removes global entries."""
    managed = f"0 8 * * * cmd run {_CRON_MARKER}\n"
    other = "0 12 * * * backup\n"
    written: list[str] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
        if "-l" in cmd:
            return _done(stdout=managed + other)
        written.append(str(kwargs.get("input", "")))
        return _done()

    with patch("subprocess.run", side_effect=fake_run):
        remove_schedule(user=None)

    assert written
    assert _CRON_MARKER not in written[0]
    assert "backup" in written[0]


def test_remove_user_schedule() -> None:
    """remove_schedule with user removes only that user's entry."""
    alice_entry = f"0 8 * * * cmd {_CRON_MARKER}:alice\n"
    bob_entry = f"0 9 * * * cmd {_CRON_MARKER}:bob\n"
    other = "0 12 * * * backup\n"
    written: list[str] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
        if "-l" in cmd:
            return _done(stdout=alice_entry + bob_entry + other)
        written.append(str(kwargs.get("input", "")))
        return _done()

    with patch("subprocess.run", side_effect=fake_run):
        remove_schedule(user="alice")

    assert written
    assert f"{_CRON_MARKER}:alice" not in written[0]
    assert f"{_CRON_MARKER}:bob" in written[0]
    assert "backup" in written[0]


# Tests for schedule status
def test_check_global_schedule_status_no_schedule() -> None:
    """check_schedule_status reports no schedule when crontab is empty."""
    with patch("subprocess.run", return_value=_done(stdout="")):
        result = check_schedule_status(user=None)
    assert "No job-scout schedule installed for global" in result


def test_check_user_schedule_status_no_schedule() -> None:
    """check_schedule_status reports no user schedule."""
    with patch("subprocess.run", return_value=_done(stdout="")):
        result = check_schedule_status(user="alice")
    assert "No job-scout schedule installed for user 'alice'" in result


def test_check_schedule_status_reports_installed_entry() -> None:
    """check_schedule_status returns entry for installed schedule."""
    entry = f"0 8 * * * /usr/bin/job-scout run >> /dev/null {_CRON_MARKER}"
    with patch("subprocess.run", return_value=_done(stdout=entry + "\n")):
        result = check_schedule_status(user=None)
    assert result.startswith("Installed:")


def test_check_user_schedule_status_reports_installed_entry() -> None:
    """check_schedule_status reports installed user schedule."""
    alice_entry = f"0 8 * * * /usr/bin/job-scout run {_CRON_MARKER}:alice\n"
    bob_entry = f"0 9 * * * /usr/bin/job-scout run {_CRON_MARKER}:bob\n"
    with patch("subprocess.run", return_value=_done(stdout=alice_entry + bob_entry)):
        result = check_schedule_status(user="alice")
    assert result.startswith("Installed:")
    assert f"{_CRON_MARKER}:alice" not in result
