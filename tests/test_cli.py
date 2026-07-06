"""Tests for the Click CLI entry point."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from job_scout.cli import cli
from job_scout.models import Config, JobListing, JobStatus


@pytest.fixture()
def cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Patch DATA_DIR and CONFIG_PATH to a temp dir and return it.

    Args:
        tmp_path: Pytest temporary directory.
        monkeypatch: Pytest monkeypatch fixture.

    Returns:
        The temporary data directory path.
    """
    import job_scout.config as cfg_module

    monkeypatch.setattr(cfg_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(cfg_module, "CONFIG_PATH", tmp_path / "config.yaml")
    return tmp_path


def _save_test_config(tmp_path: Path, **overrides: object) -> None:
    """Save a Config to the temp path's config.yaml."""
    import job_scout.config as cfg_module

    config = Config(**overrides)  # type: ignore[arg-type]
    cfg_module.save_config(config)


# ---------------------------------------------------------------------------
# config commands
# ---------------------------------------------------------------------------


def test_config_show_displays_keys(cli_env: Path) -> None:
    """config show prints all config keys to stdout."""
    _save_test_config(cli_env, ntfy_topic="my-alerts")
    runner = CliRunner()
    result = runner.invoke(cli, ["config", "show"])
    assert result.exit_code == 0
    assert "ntfy_topic" in result.output
    assert "my-alerts" in result.output


def test_config_set_updates_value(cli_env: Path) -> None:
    """config set changes an integer config value and persists it."""
    _save_test_config(cli_env)
    runner = CliRunner()
    result = runner.invoke(cli, ["config", "set", "max_travel_car", "45"])
    assert result.exit_code == 0
    assert "max_travel_car" in result.output

    # Verify persistence
    import job_scout.config as cfg_module

    loaded = cfg_module.load_config()
    assert loaded.max_travel_car == 45


def test_config_set_unknown_key_exits_with_error(cli_env: Path) -> None:
    """config set exits with code 1 for an unknown key."""
    _save_test_config(cli_env)
    runner = CliRunner()
    result = runner.invoke(cli, ["config", "set", "totally_unknown_key", "val"])
    assert result.exit_code == 1


def test_config_show_masks_api_keys(cli_env: Path) -> None:
    """config show masks API key values with ***."""
    _save_test_config(cli_env, ors_api_key="secret12345")
    runner = CliRunner()
    result = runner.invoke(cli, ["config", "show"])
    assert result.exit_code == 0
    assert "secret12345" not in result.output
    assert "***" in result.output


# ---------------------------------------------------------------------------
# jobs commands
# ---------------------------------------------------------------------------


def test_jobs_list_empty_database(cli_env: Path) -> None:
    """jobs list reports no matches when the database is empty."""
    (cli_env / "users" / "alice").mkdir(parents=True)
    runner = CliRunner()
    result = runner.invoke(cli, ["jobs", "list"])
    assert result.exit_code == 0
    assert "No matching jobs found" in result.output


def test_jobs_rejected_empty_database(cli_env: Path) -> None:
    """jobs rejected reports no rejections when the database is empty."""
    (cli_env / "users" / "alice").mkdir(parents=True)
    runner = CliRunner()
    result = runner.invoke(cli, ["jobs", "rejected"])
    assert result.exit_code == 0
    assert "No rejected jobs found" in result.output


def test_jobs_list_shows_matched_jobs(cli_env: Path) -> None:
    """jobs list displays matched jobs from the database."""
    from job_scout.database import Database

    user_dir = cli_env / "users" / "alice"
    user_dir.mkdir(parents=True)
    db = Database(user_dir / "jobs.db")
    job = JobListing(
        title="Python Dev",
        company="TechCo",
        url="https://example.com/job/1",
        source="indeed",
        status=JobStatus.MATCHED,
        fit_score=80,
        fit_reasoning="Great fit",
        seen_at=datetime.now(UTC),
    )
    db.save_job(job)

    runner = CliRunner()
    result = runner.invoke(cli, ["jobs", "list"])
    assert result.exit_code == 0
    assert "Python Dev" in result.output
    assert "TechCo" in result.output


# ---------------------------------------------------------------------------
# runs commands
# ---------------------------------------------------------------------------


def test_runs_history_empty_database(cli_env: Path) -> None:
    """runs history reports no runs when the database is empty."""
    (cli_env / "users" / "alice").mkdir(parents=True)
    runner = CliRunner()
    result = runner.invoke(cli, ["runs", "history"])
    assert result.exit_code == 0
    assert "No run history found" in result.output


def test_runs_history_shows_recent_runs(cli_env: Path) -> None:
    """runs history displays recent run history from the database."""
    from job_scout.database import Database
    from job_scout.models import RunStats

    user_dir = cli_env / "users" / "alice"
    user_dir.mkdir(parents=True)
    db = Database(user_dir / "jobs.db")

    # Add a run
    stats = RunStats(
        scraped=100,
        deduplicated=10,
        matched=5,
        rejected=20,
        notified=5,
        errors=[],
    )
    now = datetime.now(UTC)
    db.save_run_stats(stats, now, 30.0)

    runner = CliRunner()
    result = runner.invoke(cli, ["runs", "history"])
    assert result.exit_code == 0
    assert "100" in result.output  # scraped count
    assert "5" in result.output  # matched count


def test_runs_history_respects_limit(cli_env: Path) -> None:
    """runs history respects the --limit parameter."""
    from job_scout.database import Database
    from job_scout.models import RunStats

    user_dir = cli_env / "users" / "alice"
    user_dir.mkdir(parents=True)
    db = Database(user_dir / "jobs.db")

    # Add 3 runs
    for i in range(3):
        stats = RunStats(
            scraped=100 + i,
            matched=5 + i,
            rejected=20 - i,
            notified=5,
            errors=[],
        )
        t = datetime(2026, 1, 1 + i, 10, 0, 0, tzinfo=UTC)
        db.save_run_stats(stats, t, 30.0)

    runner = CliRunner()
    result = runner.invoke(cli, ["runs", "history", "--limit", "2"])
    assert result.exit_code == 0
    # Should contain 2 runs
    assert result.output.count("2026-01-") == 2


# ---------------------------------------------------------------------------
# schedule commands
# ---------------------------------------------------------------------------


def test_schedule_status_no_schedule(cli_env: Path) -> None:
    """schedule status reports no installed schedule when crontab is empty."""
    with patch("subprocess.run") as mock_run:
        from subprocess import CompletedProcess

        mock_run.return_value = CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        runner = CliRunner()
        result = runner.invoke(cli, ["schedule", "status"])
    assert result.exit_code == 0
    assert "No job-scout schedule installed" in result.output


def test_schedule_install_success(cli_env: Path) -> None:
    """schedule install reports success when crontab operations succeed."""
    from subprocess import CompletedProcess

    def fake_run(cmd: list[str], **kwargs: object) -> CompletedProcess[str]:
        return CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        runner = CliRunner()
        result = runner.invoke(  # noqa: E501
            cli, ["schedule", "install", "--hour", "9", "--minute", "30"]
        )
    assert result.exit_code == 0
    assert "09:30" in result.output


# ---------------------------------------------------------------------------
# run command
# ---------------------------------------------------------------------------


def test_run_exits_without_claude(cli_env: Path) -> None:
    """run exits with code 1 when the LLM provider is not available."""
    err_val = (False, "LLM not found")
    with patch("job_scout.cli.check_llm_available", return_value=err_val):
        runner = CliRunner()
        result = runner.invoke(cli, ["run"])
    assert result.exit_code == 1


def test_run_exits_without_profile(cli_env: Path) -> None:
    """run exits with code 1 when no profile description is configured."""
    _save_test_config(cli_env)  # no profile_description
    with patch("job_scout.cli.check_llm_available", return_value=(True, None)):
        runner = CliRunner()
        result = runner.invoke(cli, ["run"])
    assert result.exit_code == 1
    output_lower = result.output.lower()
    assert "profile" in output_lower or "profile" in (result.stderr or "").lower()


def test_run_dry_run_no_new_jobs(cli_env: Path) -> None:
    """run --dry-run reports 'No new jobs found' when scraper returns nothing."""
    _save_test_config(cli_env, profile_description="Software engineer")
    with (
        patch("job_scout.cli.check_llm_available", return_value=(True, None)),
        patch("job_scout.cli.scrape_all_jobs", return_value=[]),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["run", "--dry-run"])
    assert result.exit_code == 0
    assert "No new jobs found" in result.output


def test_run_dry_run_prints_summary(cli_env: Path) -> None:
    """run --dry-run prints a run summary with scraped/evaluated counts."""
    _save_test_config(cli_env, profile_description="Software engineer")

    fake_job = JobListing(
        title="Dev",
        company="Co",
        url="https://example.com/job/1",
        source="indeed",
        seen_at=datetime.now(UTC),
    )

    with (
        patch("job_scout.cli.check_llm_available", return_value=(True, None)),
        patch("job_scout.cli.scrape_all_jobs", return_value=[fake_job]),
        patch(
            "job_scout.cli.screen_job_titles",
            return_value=([fake_job], 0),
        ),
        patch(
            "job_scout.cli._evaluate_job",
            return_value=True,
        ),
        patch(
            "job_scout.cli._apply_travel_filter",
            return_value=True,
        ),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["run", "--dry-run"])

    assert result.exit_code == 0
    assert "Run complete" in result.output


def test_run_dry_run_shows_screened_count(cli_env: Path) -> None:
    """run --dry-run shows the title screened count in the summary."""
    _save_test_config(cli_env, profile_description="CRO specialist")

    jobs = [
        JobListing(
            title=f"Job {i}",
            company="Co",
            url=f"https://example.com/job/{i}",
            source="indeed",
            seen_at=datetime.now(UTC),
        )
        for i in range(3)
    ]
    kept = [jobs[0]]

    with (
        patch("job_scout.cli.check_llm_available", return_value=(True, None)),
        patch("job_scout.cli.scrape_all_jobs", return_value=jobs),
        patch(
            "job_scout.cli.screen_job_titles",
            return_value=(kept, 2),
        ),
        patch("job_scout.cli._evaluate_job", return_value=True),
        patch("job_scout.cli._apply_travel_filter", return_value=True),
    ):
        runner = CliRunner()
        result = runner.invoke(cli, ["run", "--dry-run"])

    assert result.exit_code == 0
    assert "Title screened" in result.output


# ---------------------------------------------------------------------------
# compensation filter
# ---------------------------------------------------------------------------


def test_compensation_filter_passes_when_no_limits() -> None:
    """_passes_compensation_filter passes when no limits are configured."""
    from job_scout.cli import _passes_compensation_filter

    config = Config(profile_description="test")
    job = JobListing(
        title="Dev",
        company="Co",
        url="https://example.com/1",
        source="test",
        salary_min=3000,
        salary_max=4000,
        vacation_days=20,
    )
    assert _passes_compensation_filter(job, config)


def test_compensation_filter_rejects_low_salary() -> None:
    """_passes_compensation_filter rejects when salary_max < min_salary."""
    from job_scout.cli import _passes_compensation_filter

    config = Config(profile_description="test", min_salary=4000)
    job = JobListing(
        title="Dev",
        company="Co",
        url="https://example.com/1",
        source="test",
        salary_max=3500,
    )
    assert not _passes_compensation_filter(job, config)


def test_compensation_filter_rejects_high_salary() -> None:
    """_passes_compensation_filter rejects when salary_min > max_salary."""
    from job_scout.cli import _passes_compensation_filter

    config = Config(profile_description="test", max_salary=5000)
    job = JobListing(
        title="Dev",
        company="Co",
        url="https://example.com/1",
        source="test",
        salary_min=6000,
    )
    assert not _passes_compensation_filter(job, config)


def test_compensation_filter_rejects_low_vacation() -> None:
    """_passes_compensation_filter rejects when vacation < minimum."""
    from job_scout.cli import _passes_compensation_filter

    config = Config(profile_description="test", min_vacation_days=25)
    job = JobListing(
        title="Dev",
        company="Co",
        url="https://example.com/1",
        source="test",
        vacation_days=20,
    )
    assert not _passes_compensation_filter(job, config)


def test_compensation_filter_passes_unknown_salary() -> None:
    """_passes_compensation_filter passes when salary is unknown (fail-open)."""
    from job_scout.cli import _passes_compensation_filter

    config = Config(profile_description="test", min_salary=4000)
    job = JobListing(
        title="Dev",
        company="Co",
        url="https://example.com/1",
        source="test",
    )
    assert _passes_compensation_filter(job, config)


def test_compensation_filter_passes_when_in_range() -> None:
    """_passes_compensation_filter passes when salary overlaps range."""
    from job_scout.cli import _passes_compensation_filter

    config = Config(profile_description="test", min_salary=3000, max_salary=5000)
    job = JobListing(
        title="Dev",
        company="Co",
        url="https://example.com/1",
        source="test",
        salary_min=3500,
        salary_max=4500,
    )
    assert _passes_compensation_filter(job, config)


# ---------------------------------------------------------------------------
# keywords commands
# ---------------------------------------------------------------------------


def test_keywords_refresh_exits_without_profile(cli_env: Path) -> None:
    """keywords refresh exits with code 1 when no profile is configured."""
    _save_test_config(cli_env)
    with patch("job_scout.cli.check_llm_available", return_value=(True, None)):
        runner = CliRunner()
        result = runner.invoke(cli, ["keywords", "refresh"])
    assert result.exit_code == 1


def test_keywords_refresh_exits_without_claude(cli_env: Path) -> None:
    """keywords refresh exits with code 1 when LLM provider is not available."""
    _save_test_config(cli_env, profile_description="Software engineer")
    err_val = (False, "LLM not found")
    with patch("job_scout.cli.check_llm_available", return_value=err_val):
        runner = CliRunner()
        result = runner.invoke(cli, ["keywords", "refresh"])
    assert result.exit_code == 1


def test_profile_cv_summary_exits_without_cv_path(cli_env: Path) -> None:
    """profile cv-summary exits with code 1 when no CV path is configured."""
    from job_scout.config import apply_user_init

    _save_test_config(cli_env)
    apply_user_init("testuser", {})
    runner = CliRunner()
    result = runner.invoke(cli, ["profile", "cv-summary", "--user", "testuser"])
    assert result.exit_code == 1
    assert "No CV path configured" in result.output


def test_profile_cv_summary_exits_when_cv_missing(cli_env: Path) -> None:
    """profile cv-summary exits with code 1 when CV file does not exist."""
    from job_scout.config import apply_user_init

    _save_test_config(cli_env)
    apply_user_init("testuser", {"cv_path": "/nonexistent/cv.pdf"})
    runner = CliRunner()
    result = runner.invoke(cli, ["profile", "cv-summary", "--user", "testuser"])
    assert result.exit_code == 1
    assert "not found" in result.output


def test_profile_cv_summary_with_cv_path(cli_env: Path) -> None:
    """profile cv-summary shows error when CV path is set but parse fails."""
    from job_scout.config import apply_user_init

    cv_path = cli_env / "test_cv.pdf"
    cv_path.write_bytes(b"fake pdf")

    _save_test_config(cli_env)
    apply_user_init("testuser", {"cv_path": str(cv_path)})

    with patch("job_scout.cv_parser.parse_cv", return_value=""):
        runner = CliRunner()
        result = runner.invoke(cli, ["profile", "cv-summary", "--user", "testuser"])
        assert result.exit_code == 1
        assert "Failed to extract text" in result.output


# ---------------------------------------------------------------------------
# notification mode tests
# ---------------------------------------------------------------------------


def test_send_notifications_per_job_mode(cli_env: Path) -> None:
    """Test per-job notification mode sends one notification per job."""
    from unittest.mock import Mock

    from job_scout.cli import _send_notifications
    from job_scout.database import Database

    _save_test_config(cli_env, notification_mode="per_job")
    from job_scout.config import load_config

    config = load_config()
    db = Database(cli_env / "test.db")

    job1 = JobListing(
        title="Engineer",
        company="Corp A",
        url="https://example.com/1",
        source="linkedin",
        fit_score=85,
        fit_reasoning="Good match",
    )
    job2 = JobListing(
        title="Developer",
        company="Corp B",
        url="https://example.com/2",
        source="linkedin",
        fit_score=75,
        fit_reasoning="Okay match",
    )

    with patch("job_scout.cli.get_notifier") as mock_get_notifier:
        mock_notifier = Mock()
        mock_get_notifier.return_value = mock_notifier

        sent = _send_notifications([job1, job2], db, config, dry_run=True)

        assert sent == 0  # dry_run doesn't count
        assert mock_notifier.send.call_count == 0  # dry_run


def test_send_notifications_digest_mode(cli_env: Path) -> None:
    """Test digest notification mode sends one notification for all jobs."""
    from unittest.mock import Mock

    from job_scout.cli import _send_notifications
    from job_scout.database import Database

    _save_test_config(cli_env, notification_mode="digest")
    from job_scout.config import load_config

    config = load_config()
    db = Database(cli_env / "test.db")

    job1 = JobListing(
        title="Engineer",
        company="Corp A",
        url="https://example.com/1",
        source="linkedin",
        fit_score=85,
        fit_reasoning="Good match",
    )
    job2 = JobListing(
        title="Developer",
        company="Corp B",
        url="https://example.com/2",
        source="linkedin",
        fit_score=75,
        fit_reasoning="Okay match",
    )

    with patch("job_scout.cli.get_notifier") as mock_get_notifier:
        mock_notifier = Mock()
        mock_get_notifier.return_value = mock_notifier

        sent = _send_notifications([job1, job2], db, config, dry_run=True)

        assert sent == 0  # dry_run doesn't count
        assert mock_notifier.send_digest.call_count == 0  # dry_run


def test_send_notifications_zero_matches(cli_env: Path) -> None:
    """Test that no notification is sent when there are zero matches."""
    from unittest.mock import Mock

    from job_scout.cli import _send_notifications
    from job_scout.database import Database

    _save_test_config(cli_env, notification_mode="digest")
    from job_scout.config import load_config

    config = load_config()
    db = Database(cli_env / "test.db")

    with patch("job_scout.cli.get_notifier") as mock_get_notifier:
        mock_notifier = Mock()
        mock_get_notifier.return_value = mock_notifier

        sent = _send_notifications([], db, config, dry_run=False)

        assert sent == 0
        assert mock_notifier.send_digest.call_count == 0
        assert mock_notifier.send.call_count == 0
