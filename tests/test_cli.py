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
