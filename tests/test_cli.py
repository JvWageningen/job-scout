"""Tests for the Click CLI entry point."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

import job_scout
from job_scout.cli import _evaluate_survivors, _filter_by_commute, cli
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
def test_version_option_reports_package_version() -> None:
    """--version prints the installed package version, which bug reports ask for."""
    runner = CliRunner()
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert "job-scout" in result.output
    assert job_scout.__version__ in result.output


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


def test_dedupe_matched_for_notification_collapses_cross_source() -> None:
    """Same title+company from different sources collapses to the top score."""
    from job_scout.cli import _dedupe_matched_for_notification

    linkedin = JobListing(
        title="CRO Specialist",
        company="Foxelli Group",
        url="https://linkedin.com/jobs/1",
        source="linkedin",
        fit_score=83,
    )
    indeed = JobListing(
        title="CRO Specialist",
        company="Foxelli Group",
        url="https://indeed.com/jobs/2",
        source="indeed",
        fit_score=85,
    )
    other = JobListing(
        title="CRO Manager",
        company="Sunny Cars",
        url="https://indeed.com/jobs/3",
        source="indeed",
        fit_score=88,
    )

    result = _dedupe_matched_for_notification([linkedin, indeed, other])

    assert len(result) == 2
    # The higher-scoring Foxelli row (Indeed, 85) is kept over the LinkedIn (83).
    foxelli = [j for j in result if j.company == "Foxelli Group"]
    assert len(foxelli) == 1
    assert foxelli[0].fit_score == 85


def test_send_notifications_dedupes_cross_source(cli_env: Path) -> None:
    """_send_notifications sends one notification per unique job, not per source."""
    from unittest.mock import Mock

    from job_scout.cli import _send_notifications
    from job_scout.config import load_config
    from job_scout.database import Database

    _save_test_config(cli_env)
    config = load_config()
    db = Database(cli_env / "test.db")

    dup_a = JobListing(
        title="CRO Specialist",
        company="Foxelli Group",
        url="https://linkedin.com/jobs/1",
        source="linkedin",
        fit_score=83,
    )
    dup_b = JobListing(
        title="CRO Specialist",
        company="Foxelli Group",
        url="https://indeed.com/jobs/2",
        source="indeed",
        fit_score=85,
    )

    with patch("job_scout.cli.get_notifier") as mock_get_notifier:
        mock_notifier = Mock()
        mock_get_notifier.return_value = mock_notifier

        _send_notifications([dup_a, dup_b], db, config, dry_run=False)

        # Two rows for the same job (different sources) → notified exactly once.
        assert mock_notifier.send.call_count == 1


def test_prune_filled_matches_expires_and_excludes_filled() -> None:
    """Filled matched jobs are marked expired and dropped from the notify set."""
    from unittest.mock import MagicMock

    from job_scout.cli import _prune_filled_matches
    from job_scout.pruner import PruneCheck, PruneOutcome

    open_job = JobListing(
        title="CRO Specialist",
        company="Praxis",
        url="https://x/1",
        source="linkedin",
        seen_at=datetime.now(UTC),
    )
    open_job.id = 1
    filled_job = JobListing(
        title="Head of E-commerce",
        company="Acme",
        url="https://x/2",
        source="linkedin",
        seen_at=datetime.now(UTC),
    )
    filled_job.id = 2

    def fake_check(job: JobListing, **_: object) -> PruneCheck:
        if job.id == 2:
            return PruneCheck(outcome=PruneOutcome.FILLED, reason="closed", signal="x")
        return PruneCheck(outcome=PruneOutcome.OPEN, reason="open")

    db = MagicMock()
    config = Config(verify_matches_open=True)
    with patch("job_scout.pruner.check_vacancy_open", side_effect=fake_check):
        still_open = _prune_filled_matches(
            [open_job, filled_job], db, config, dry_run=False
        )

    assert [j.id for j in still_open] == [1]
    db.mark_expired.assert_called_once()
    assert db.mark_expired.call_args[0][0] == 2


def test_prune_filled_matches_disabled_returns_all() -> None:
    """When verification is off, all matched jobs pass through untouched."""
    from unittest.mock import MagicMock

    from job_scout.cli import _prune_filled_matches

    job = JobListing(
        title="CRO Specialist",
        company="Praxis",
        url="https://x/1",
        source="linkedin",
        seen_at=datetime.now(UTC),
    )
    config = Config(verify_matches_open=False)
    result = _prune_filled_matches([job], MagicMock(), config, dry_run=False)
    assert result == [job]


class TestCommutePreFilter:
    """Tests for dropping unreachable jobs before the LLM sees them."""

    def _job(self, title: str, location: str) -> JobListing:
        """Build a listing at a given location."""
        return JobListing(
            title=title,
            company="ACME",
            location=location,
            url=f"https://example.invalid/{title}",
            description="",
            source="test",
        )

    def _config(self) -> Config:
        """Return a config with a tight commute."""
        return Config(
            name="test",
            home_address="Somewhere 1, Town",
            max_distance_km=30,
            max_travel_car=45,
            allow_unknown_location=False,
        )

    def test_unreachable_jobs_never_reach_the_evaluator(self, tmp_path) -> None:  # noqa: ANN001
        """The whole point: pay for geography before paying for the LLM."""
        from job_scout.database import Database
        from job_scout.models import RunStats

        near = self._job("Near", "Town")
        far = self._job("Far", "Faraway")
        db = Database(tmp_path / "jobs.db")
        stats = RunStats()

        def fake_travel(job, config, database=None):  # noqa: ANN001, ANN202
            job.distance_km = 5.0 if job.title == "Near" else 400.0
            job.location_unknown = False
            job.travel_times = []
            return job

        with patch("job_scout.cli._calculate_travel_for_job", side_effect=fake_travel):
            kept = _filter_by_commute([near, far], self._config(), db, False, stats)

        assert [j.title for j in kept] == ["Near"]
        assert stats.commute_filtered == 1
        assert stats.rejected == 1

    def test_rejected_jobs_are_saved_so_they_are_not_rechecked(self, tmp_path) -> None:  # noqa: ANN001
        """An out-of-range job is remembered, not re-scraped every run."""
        from job_scout.database import Database
        from job_scout.models import RunStats

        far = self._job("Far", "Faraway")
        db = Database(tmp_path / "jobs.db")

        def fake_travel(job, config, database=None):  # noqa: ANN001, ANN202
            job.distance_km = 400.0
            job.location_unknown = False
            job.travel_times = []
            return job

        with patch("job_scout.cli._calculate_travel_for_job", side_effect=fake_travel):
            _filter_by_commute([far], self._config(), db, False, RunStats())

        assert db.is_duplicate(far) is True

    def test_dry_run_saves_nothing(self, tmp_path) -> None:  # noqa: ANN001
        """A dry run must not write the rejects."""
        from job_scout.database import Database
        from job_scout.models import RunStats

        far = self._job("Far", "Faraway")
        db = Database(tmp_path / "jobs.db")

        def fake_travel(job, config, database=None):  # noqa: ANN001, ANN202
            job.distance_km = 400.0
            job.location_unknown = False
            job.travel_times = []
            return job

        with patch("job_scout.cli._calculate_travel_for_job", side_effect=fake_travel):
            _filter_by_commute([far], self._config(), db, True, RunStats())

        assert db.is_duplicate(far) is False

    def test_a_failed_lookup_is_retried_next_run(self, tmp_path) -> None:  # noqa: ANN001
        """A rate-limited geocode must not quietly retire a job for good."""
        from job_scout.database import Database
        from job_scout.models import RunStats

        job = self._job("Unresolved", "Somewhere the geocoder choked on")
        db = Database(tmp_path / "jobs.db")

        def fake_travel(listing, config, database=None):  # noqa: ANN001, ANN202
            listing.location_unknown = True
            listing.distance_km = None
            listing.travel_times = []
            return listing

        with patch("job_scout.cli._calculate_travel_for_job", side_effect=fake_travel):
            kept = _filter_by_commute([job], self._config(), db, False, RunStats())

        assert kept == []
        assert db.is_duplicate(job) is False

    def test_a_listing_with_no_location_is_settled(self, tmp_path) -> None:  # noqa: ANN001
        """Re-asking cannot invent a location, so that answer is recorded."""
        from job_scout.database import Database
        from job_scout.models import RunStats

        job = self._job("Nowhere", "")
        db = Database(tmp_path / "jobs.db")

        def fake_travel(listing, config, database=None):  # noqa: ANN001, ANN202
            listing.location_unknown = True
            return listing

        with patch("job_scout.cli._calculate_travel_for_job", side_effect=fake_travel):
            _filter_by_commute([job], self._config(), db, False, RunStats())

        assert db.is_duplicate(job) is True

    def test_no_jobs_is_not_an_error(self, tmp_path) -> None:  # noqa: ANN001
        """An empty candidate list short-circuits."""
        from job_scout.database import Database
        from job_scout.models import RunStats

        db = Database(tmp_path / "jobs.db")
        assert _filter_by_commute([], self._config(), db, False, RunStats()) == []


class TestStoppingMidEvaluation:
    """Tests that a stopped run keeps the work it already did."""

    def test_finished_evaluations_are_saved_when_a_run_is_stopped(
        self, tmp_path
    ) -> None:  # noqa: ANN001
        """Otherwise stopping would throw away hours of scoring."""
        from job_scout import progress
        from job_scout.database import Database
        from job_scout.models import RunStats

        jobs = [
            JobListing(
                title=f"Job {i}",
                company="ACME",
                location="Town",
                url=f"https://example.invalid/{i}",
                description="",
                source="test",
            )
            for i in range(4)
        ]
        db = Database(tmp_path / "jobs.db")
        stats = RunStats()
        to_save: list[JobListing] = []
        passed: list[JobListing] = []
        config = Config(name="test", home_address="Somewhere 1, Town")

        calls = {"n": 0}

        def fake_eval(args):  # noqa: ANN001, ANN202
            job = args[0]
            calls["n"] += 1
            if calls["n"] == 3:
                progress.request_stop("stopper")
            return job, False, None

        progress.begin_run("stopper")
        try:
            with (
                patch("job_scout.cli._eval_job_full_parallel", side_effect=fake_eval),
                pytest.raises(progress.RunStoppedError),
            ):
                _evaluate_survivors(
                    jobs, config, "", db, MagicMock(), stats, to_save, passed, 1
                )
        finally:
            progress.end_run("stopper")

        # The run stopped early, but what it had already judged is in hand.
        assert to_save, "a stopped run must not discard finished evaluations"
        assert len(to_save) < len(jobs)

    def test_stopping_does_not_wait_for_the_whole_queue(self, tmp_path) -> None:  # noqa: ANN001
        """A stop must skip the jobs not started, not wait them out.

        Every job is submitted to the pool up front, so without cancelling the
        pending futures a stop takes as long as finishing the stage -- which
        makes the button look broken.
        """
        from job_scout import progress
        from job_scout.database import Database
        from job_scout.models import RunStats

        jobs = [
            JobListing(
                title=f"Job {i}",
                company="ACME",
                location="Town",
                url=f"https://example.invalid/{i}",
                description="",
                source="test",
            )
            for i in range(40)
        ]
        db = Database(tmp_path / "jobs.db")
        started = {"n": 0}

        def fake_eval(args):  # noqa: ANN001, ANN202
            started["n"] += 1
            # Real evaluations take minutes; without some cost here every
            # future finishes before the loop runs and there is nothing left
            # to cancel, which would make the test pass on any implementation.
            time.sleep(0.02)
            progress.request_stop("stopper")
            return args[0], False, None

        progress.begin_run("stopper")
        try:
            with (
                patch("job_scout.cli._eval_job_full_parallel", side_effect=fake_eval),
                pytest.raises(progress.RunStoppedError),
            ):
                _evaluate_survivors(
                    jobs,
                    Config(name="test"),
                    "",
                    db,
                    MagicMock(),
                    RunStats(),
                    [],
                    [],
                    2,
                )
        finally:
            progress.end_run("stopper")

        assert started["n"] < len(jobs), (
            "the stop waited for jobs it should have cancelled"
        )
