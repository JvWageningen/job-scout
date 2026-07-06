"""Tests for cron schedule management."""

from __future__ import annotations

from subprocess import CompletedProcess
from typing import Any
from unittest.mock import patch

import pytest

from job_scout.scheduler import (
    _CRON_MARKER,
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


def test_install_schedule_adds_marker() -> None:
    """install_schedule writes a cron line containing the managed marker."""
    written: list[str] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
        if "-l" in cmd:
            return _done(stdout="")
        written.append(str(kwargs.get("input", "")))
        return _done()

    with patch("subprocess.run", side_effect=fake_run):
        install_schedule(hour=9, minute=30)

    assert written
    assert _CRON_MARKER in written[0]


def test_install_schedule_embeds_hour_and_minute() -> None:
    """install_schedule encodes hour and minute into the cron expression."""
    written: list[str] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
        if "-l" in cmd:
            return _done(stdout="")
        written.append(str(kwargs.get("input", "")))
        return _done()

    with patch("subprocess.run", side_effect=fake_run):
        install_schedule(hour=14, minute=15)

    assert written
    assert "14" in written[0]
    assert "15" in written[0]


def test_install_schedule_replaces_existing_entry() -> None:
    """install_schedule removes old job-scout entries before adding the new one."""
    old = f"0 8 * * * old-cmd run >> /dev/null 2>&1 {_CRON_MARKER}"
    written: list[str] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
        if "-l" in cmd:
            return _done(stdout=old + "\n")
        written.append(str(kwargs.get("input", "")))
        return _done()

    with patch("subprocess.run", side_effect=fake_run):
        install_schedule(hour=10, minute=0)

    assert written
    # Only one managed entry should remain after replacement
    assert written[0].count(_CRON_MARKER) == 1


def test_install_schedule_preserves_other_cron_entries() -> None:
    """install_schedule keeps unrelated cron entries intact."""
    existing = "0 12 * * * /usr/bin/backup.sh\n"
    written: list[str] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
        if "-l" in cmd:
            return _done(stdout=existing)
        written.append(str(kwargs.get("input", "")))
        return _done()

    with patch("subprocess.run", side_effect=fake_run):
        install_schedule(hour=8, minute=0)

    assert written
    assert "/usr/bin/backup.sh" in written[0]


def test_remove_schedule_clears_managed_entries() -> None:
    """remove_schedule deletes all job-scout cron entries."""
    managed = f"0 8 * * * cmd run {_CRON_MARKER}\n"
    other = "0 12 * * * backup\n"
    written: list[str] = []

    def fake_run(cmd: list[str], **kwargs: Any) -> CompletedProcess[str]:
        if "-l" in cmd:
            return _done(stdout=managed + other)
        written.append(str(kwargs.get("input", "")))
        return _done()

    with patch("subprocess.run", side_effect=fake_run):
        remove_schedule()

    assert written
    assert _CRON_MARKER not in written[0]
    assert "backup" in written[0]


def test_check_schedule_status_no_schedule() -> None:
    """check_schedule_status reports no schedule when crontab is empty."""
    with patch("subprocess.run", return_value=_done(stdout="")):
        result = check_schedule_status()
    assert "No job-scout schedule installed" in result


def test_check_schedule_status_reports_installed_entry() -> None:
    """check_schedule_status returns 'Installed:' prefix with schedule entry."""
    entry = f"0 8 * * * /usr/bin/job-scout run >> /dev/null {_CRON_MARKER}"
    with patch("subprocess.run", return_value=_done(stdout=entry + "\n")):
        result = check_schedule_status()
    assert result.startswith("Installed:")
