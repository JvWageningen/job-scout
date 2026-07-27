"""Tests for live pipeline progress reporting."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from job_scout import progress


def _reset() -> None:
    """Clear any progress left over from another test."""
    progress.end_run("tester")
    progress.end_run(None)


class TestRunLifecycle:
    """Progress exists only while a run is active."""

    def test_no_progress_before_a_run_starts(self) -> None:
        """An untracked user reports nothing."""
        _reset()
        assert progress.get("tester") is None

    def test_begin_run_creates_progress(self) -> None:
        """Starting a run makes progress available."""
        _reset()
        progress.begin_run("tester")
        assert progress.get("tester") is not None
        _reset()

    def test_end_run_clears_progress(self) -> None:
        """A finished run stops reporting progress."""
        _reset()
        progress.begin_run("tester")
        progress.end_run("tester")
        assert progress.get("tester") is None

    def test_reporting_without_a_run_is_a_no_op(self) -> None:
        """Stage calls outside a run must not raise."""
        _reset()
        progress.set_stage("scraping", 10)
        progress.advance("something")
        assert progress.get("tester") is None


class TestStageReporting:
    """Stage and counts are surfaced for the dashboard."""

    def test_stage_is_reported_with_a_readable_label(self) -> None:
        """Raw stage keys are mapped to human wording."""
        _reset()
        progress.begin_run("tester")
        progress.set_stage("quick_eval", 100)
        state = progress.get("tester")
        assert state is not None
        assert state["stage"] == "quick_eval"
        assert state["stage_label"] == "Quick scoring"
        _reset()

    def test_stage_position_is_reported(self) -> None:
        """The dashboard can show "step N of M"."""
        _reset()
        progress.begin_run("tester")
        progress.set_stage("scraping")
        state = progress.get("tester")
        assert state is not None
        assert state["stage_index"] == 1
        assert state["stage_count"] == len(progress.STAGES)
        _reset()

    def test_advance_increments_and_records_detail(self) -> None:
        """Each completed item moves the counter and names the item."""
        _reset()
        progress.begin_run("tester")
        progress.set_stage("quick_eval", 3)
        progress.advance("Quality Engineer @ Acme")
        state = progress.get("tester")
        assert state is not None
        assert state["current"] == 1
        assert state["detail"] == "Quality Engineer @ Acme"
        _reset()

    def test_percent_is_computed(self) -> None:
        """A determinate stage reports a percentage."""
        _reset()
        progress.begin_run("tester")
        progress.set_stage("quick_eval", 4)
        progress.advance()
        state = progress.get("tester")
        assert state is not None
        assert state["percent"] == 25
        _reset()

    def test_percent_is_none_without_a_total(self) -> None:
        """A stage of unknown size reports no percentage."""
        _reset()
        progress.begin_run("tester")
        progress.set_stage("scraping")
        state = progress.get("tester")
        assert state is not None
        assert state["percent"] is None
        _reset()

    def test_new_stage_resets_the_counter(self) -> None:
        """Counts are per stage, not cumulative."""
        _reset()
        progress.begin_run("tester")
        progress.set_stage("quick_eval", 10)
        progress.advance()
        progress.set_stage("evaluating", 5)
        state = progress.get("tester")
        assert state is not None
        assert state["current"] == 0
        _reset()

    def test_unknown_stage_still_renders(self) -> None:
        """An unrecognised stage degrades to a title-cased label."""
        _reset()
        progress.begin_run("tester")
        progress.set_stage("custom_thing", 1)
        state = progress.get("tester")
        assert state is not None
        assert state["stage_label"] == "Custom Thing"
        assert state["stage_index"] == 0
        _reset()


class TestEta:
    """The estimate must be absent rather than wrong."""

    def test_no_estimate_before_any_item_completes(self) -> None:
        """A first guess from zero data would be meaningless."""
        _reset()
        progress.begin_run("tester")
        progress.set_stage("quick_eval", 100)
        state = progress.get("tester")
        assert state is not None
        assert state["eta_seconds"] is None
        _reset()

    def test_estimate_extrapolates_from_elapsed_time(self) -> None:
        """Ten seconds for one of five items implies forty left."""
        _reset()
        progress.begin_run("tester")
        progress.set_stage("quick_eval", 5)
        progress.advance()
        state_obj = progress._runs["tester"]
        state_obj.stage_started_at = datetime.now(UTC) - timedelta(seconds=10)
        state = progress.get("tester")
        assert state is not None
        eta = state["eta_seconds"]
        assert isinstance(eta, int)
        assert 35 <= eta <= 45
        _reset()

    def test_no_estimate_once_the_stage_completes(self) -> None:
        """A finished stage has nothing left to estimate."""
        _reset()
        progress.begin_run("tester")
        progress.set_stage("quick_eval", 2)
        progress.advance()
        progress.advance()
        state = progress.get("tester")
        assert state is not None
        assert state["eta_seconds"] is None
        _reset()

    def test_elapsed_time_is_reported(self) -> None:
        """Total elapsed time is available regardless of stage."""
        _reset()
        progress.begin_run("tester")
        state = progress.get("tester")
        assert state is not None
        assert isinstance(state["elapsed_seconds"], int)
        _reset()
