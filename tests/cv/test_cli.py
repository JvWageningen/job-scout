"""Tests for the CV builder's click commands on job-scout's root group."""

from __future__ import annotations

import logging
import socket
from pathlib import Path

import pytest
from click.testing import CliRunner

from job_scout.cli import cli
from job_scout.config import user_cv_dir
from job_scout.cv.cli import DEFAULT_PORT, _DropClientDisconnects, _port_is_free
from job_scout.cv.models import CVDocument
from job_scout.cv.storage import ProfileStore


def _init_user(data_dir: Path, name: str) -> Path:
    """Create a user directory the way 'job-scout init --user' would.

    Args:
        data_dir: The isolated data directory.
        name: User name to create.

    Returns:
        That user's CV data root.
    """
    (data_dir / "users" / name).mkdir(parents=True, exist_ok=True)
    return user_cv_dir(name)


@pytest.fixture()
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point job-scout at a temp data directory holding one user, 'tester'.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        The temporary data directory path.
    """
    import job_scout.config as cfg_module

    monkeypatch.setattr(cfg_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(cfg_module, "CONFIG_PATH", tmp_path / "config.yaml")
    _init_user(tmp_path, "tester")
    return tmp_path


# group wiring
# ---------------------------------------------------------------------------


def test_cv_group_hangs_off_the_root_cli() -> None:
    """The editor is reachable as 'job-scout cv', not a second entry point."""
    runner = CliRunner()
    result = runner.invoke(cli, ["cv", "--help"])
    assert result.exit_code == 0
    for command in ("serve", "render", "list"):
        assert command in result.output


def test_cv_has_no_version_command_of_its_own() -> None:
    """job-scout already has a root --version; a second one would be confusing."""
    runner = CliRunner()
    result = runner.invoke(cli, ["cv", "version"])
    assert result.exit_code != 0


# list
# ---------------------------------------------------------------------------


def test_list_on_an_empty_directory(data_dir: Path) -> None:
    """A user with no CVs yet is told where they would be stored."""
    runner = CliRunner()
    result = runner.invoke(cli, ["cv", "list"])
    assert result.exit_code == 0, result.output
    assert "No profiles yet" in result.output


def test_list_shows_saved_profiles(data_dir: Path, cv: CVDocument) -> None:
    """A single-user install can omit --user, as it does everywhere else."""
    ProfileStore(user_cv_dir("tester")).save("mine", cv)
    runner = CliRunner()
    result = runner.invoke(cli, ["cv", "list"])
    assert result.exit_code == 0, result.output
    assert "mine" in result.output


def test_list_keeps_users_apart(data_dir: Path, cv: CVDocument) -> None:
    """--user selects one person's CVs and never shows another's."""
    ProfileStore(user_cv_dir("tester")).save("mine", cv)
    _init_user(data_dir, "other")
    ProfileStore(user_cv_dir("other")).save("theirs", cv)
    runner = CliRunner()
    result = runner.invoke(cli, ["cv", "list", "--user", "other"])
    assert result.exit_code == 0, result.output
    assert "theirs" in result.output
    assert "mine" not in result.output


def test_list_with_several_users_demands_a_choice(data_dir: Path) -> None:
    """Ambiguity is refused rather than guessed, matching the other commands."""
    _init_user(data_dir, "other")
    runner = CliRunner()
    result = runner.invoke(cli, ["cv", "list"])
    assert result.exit_code == 1
    assert "--user" in result.output


def test_list_rejects_an_unknown_user(data_dir: Path) -> None:
    """A typo in --user names the users that do exist."""
    runner = CliRunner()
    result = runner.invoke(cli, ["cv", "list", "--user", "ghost"])
    assert result.exit_code == 1
    assert "tester" in result.output


# render
# ---------------------------------------------------------------------------


def test_render_writes_a_pdf(data_dir: Path, tmp_path: Path, cv: CVDocument) -> None:
    """render writes a real PDF to the requested path, creating parents."""
    ProfileStore(user_cv_dir("tester")).save("mine", cv)
    output = tmp_path / "out" / "cv.pdf"
    runner = CliRunner()
    result = runner.invoke(cli, ["cv", "render", "mine", "-o", str(output)])
    assert result.exit_code == 0, result.output
    assert output.is_file()
    assert output.read_bytes().startswith(b"%PDF")
    assert "1 page(s)" in result.output


def test_render_honours_the_long_output_flag(
    data_dir: Path, tmp_path: Path, cv: CVDocument
) -> None:
    """--output is accepted as well as -o."""
    ProfileStore(user_cv_dir("tester")).save("mine", cv)
    output = tmp_path / "long.pdf"
    runner = CliRunner()
    result = runner.invoke(cli, ["cv", "render", "mine", "--output", str(output)])
    assert result.exit_code == 0, result.output
    assert output.read_bytes().startswith(b"%PDF")


def test_render_unknown_profile_exits_nonzero(data_dir: Path, tmp_path: Path) -> None:
    """A missing slug is reported, not raised as a traceback."""
    runner = CliRunner()
    result = runner.invoke(
        cli, ["cv", "render", "ghost", "-o", str(tmp_path / "ghost.pdf")]
    )
    assert result.exit_code == 1
    assert not (tmp_path / "ghost.pdf").exists()


# serve
# ---------------------------------------------------------------------------


def test_client_disconnects_are_filtered_but_real_errors_are_not() -> None:
    """Only ConnectionResetError is dropped; genuine asyncio errors survive."""
    log_filter = _DropClientDisconnects()

    def record(error: BaseException | None) -> logging.LogRecord:
        """Build a log record carrying the given exception."""
        made = logging.LogRecord("asyncio", logging.ERROR, __file__, 1, "x", None, None)
        made.exc_info = (type(error), error, None) if error else None
        return made

    assert log_filter.filter(record(ConnectionResetError())) is False
    assert log_filter.filter(record(ValueError("real problem"))) is True
    assert log_filter.filter(record(None)) is True


def test_default_port_avoids_the_ephemeral_range() -> None:
    """Windows hands out 49152+ dynamically; binding there invites a clash."""
    assert 1024 < DEFAULT_PORT < 49152


def test_free_port_is_detected() -> None:
    """A listening socket makes the port busy; closing it frees it again."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        taken = probe.getsockname()[1]
        probe.listen(1)
        assert _port_is_free("127.0.0.1", taken) is False

    assert _port_is_free("127.0.0.1", taken) is True


def test_serve_refuses_a_busy_port(data_dir: Path) -> None:
    """A taken port is a one-line message, not a uvicorn traceback."""
    runner = CliRunner()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        probe.listen(1)
        busy = probe.getsockname()[1]
        result = runner.invoke(cli, ["cv", "serve", "--port", str(busy)])

    assert result.exit_code == 1
    assert "already in use" in result.output
    # It must fail before touching the data directory.
    assert not (user_cv_dir("tester") / "profiles").exists()
