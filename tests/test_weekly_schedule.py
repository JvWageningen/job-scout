"""Tests for the in-container weekly scheduler."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from job_scout import weekly_schedule as scheduler
from job_scout.weekly_schedule import (
    ScheduleSettings,
    ScheduleSlot,
    next_run_after,
    parse_slots,
)

AMS = ZoneInfo("Europe/Amsterdam")

# The deployed schedule: Tuesday 17:00 and Saturday 03:00.
DEPLOYED = [ScheduleSlot(1, 17, 0), ScheduleSlot(5, 3, 0)]


class TestScheduleSlot:
    """Out-of-range values are rejected at construction."""

    def test_rejects_bad_weekday(self) -> None:
        """Weekday 7 does not exist."""
        with pytest.raises(ValueError, match="weekday must be 0-6"):
            ScheduleSlot(7, 12, 0)

    def test_rejects_bad_hour(self) -> None:
        """Hour 24 does not exist."""
        with pytest.raises(ValueError, match="hour must be 0-23"):
            ScheduleSlot(1, 24, 0)

    def test_rejects_bad_minute(self) -> None:
        """Minute 60 does not exist."""
        with pytest.raises(ValueError, match="minute must be 0-59"):
            ScheduleSlot(1, 12, 60)

    def test_readable_label(self) -> None:
        """The label is what gets logged at start-up."""
        assert str(ScheduleSlot(1, 17, 0)) == "tue 17:00"


class TestParseSlots:
    """The schedule is configurable via one environment variable."""

    def test_parses_the_deployed_schedule(self) -> None:
        """'tue:17:00,sat:03:00' is the shipped default."""
        assert parse_slots("tue:17:00,sat:03:00") == DEPLOYED

    def test_is_case_and_space_insensitive(self) -> None:
        """Hand-edited values should still work."""
        assert parse_slots(" TUE:17:00 , Sat:03:00 ") == DEPLOYED

    def test_accepts_full_day_names(self) -> None:
        """Only the first three letters matter."""
        assert parse_slots("tuesday:17:00") == [ScheduleSlot(1, 17, 0)]

    def test_rejects_unknown_day(self) -> None:
        """A typo must fail loudly rather than silently never running."""
        with pytest.raises(ValueError, match="Unknown day"):
            parse_slots("funday:17:00")

    def test_rejects_missing_minutes(self) -> None:
        """'tue:17' is ambiguous and rejected."""
        with pytest.raises(ValueError, match="Expected 'day:HH:MM'"):
            parse_slots("tue:17")

    def test_rejects_empty(self) -> None:
        """An empty spec would mean 'never run'."""
        with pytest.raises(ValueError, match="No schedule entries"):
            parse_slots("   ")

    def test_rejects_out_of_range_time(self) -> None:
        """Range validation survives parsing."""
        with pytest.raises(ValueError, match="Bad time"):
            parse_slots("tue:99:00")


class TestNextRunAfter:
    """Picking the next occurrence is the whole correctness surface."""

    def test_picks_the_sooner_of_two_slots(self) -> None:
        """From Monday, Tuesday 17:00 comes before Saturday 03:00."""
        now = datetime(2026, 8, 24, 9, 0, tzinfo=AMS)  # Monday
        assert next_run_after(now, DEPLOYED) == datetime(2026, 8, 25, 17, 0, tzinfo=AMS)

    def test_same_day_before_the_time(self) -> None:
        """Tuesday morning still runs Tuesday afternoon."""
        now = datetime(2026, 8, 25, 9, 0, tzinfo=AMS)  # Tuesday
        assert next_run_after(now, DEPLOYED) == datetime(2026, 8, 25, 17, 0, tzinfo=AMS)

    def test_same_day_after_the_time_rolls_forward(self) -> None:
        """Tuesday evening waits for Saturday, not a second Tuesday run."""
        now = datetime(2026, 8, 25, 17, 30, tzinfo=AMS)  # Tuesday, past 17:00
        assert next_run_after(now, DEPLOYED) == datetime(2026, 8, 29, 3, 0, tzinfo=AMS)

    def test_exactly_on_the_slot_moves_to_the_next(self) -> None:
        """Strictly-after prevents an immediate re-run of the slot just fired."""
        now = datetime(2026, 8, 25, 17, 0, tzinfo=AMS)
        assert next_run_after(now, DEPLOYED) == datetime(2026, 8, 29, 3, 0, tzinfo=AMS)

    def test_saturday_after_run_wraps_to_next_tuesday(self) -> None:
        """The weekly cycle closes correctly."""
        now = datetime(2026, 8, 29, 4, 0, tzinfo=AMS)  # Saturday, past 03:00
        assert next_run_after(now, DEPLOYED) == datetime(2026, 9, 1, 17, 0, tzinfo=AMS)

    def test_single_slot_one_week_later(self) -> None:
        """A slot whose time has passed today lands next week."""
        now = datetime(2026, 8, 25, 18, 0, tzinfo=AMS)  # Tuesday
        assert next_run_after(now, [ScheduleSlot(1, 17, 0)]) == datetime(
            2026, 9, 1, 17, 0, tzinfo=AMS
        )

    def test_requires_aware_datetime(self) -> None:
        """A naive time would silently schedule in the wrong zone."""
        with pytest.raises(ValueError, match="timezone-aware"):
            next_run_after(datetime(2026, 8, 24, 9, 0), DEPLOYED)  # noqa: DTZ001

    def test_requires_at_least_one_slot(self) -> None:
        """An empty schedule is a configuration error."""
        with pytest.raises(ValueError, match="At least one schedule slot"):
            next_run_after(datetime(2026, 8, 24, 9, 0, tzinfo=AMS), [])

    def test_local_time_holds_across_dst_change(self) -> None:
        """17:00 stays 17:00 the week the clocks go back, not 16:00 or 18:00."""
        # DST ends in the EU on Sunday 25 October 2026.
        before = next_run_after(datetime(2026, 10, 20, 18, 0, tzinfo=AMS), DEPLOYED)
        after = next_run_after(datetime(2026, 10, 26, 9, 0, tzinfo=AMS), DEPLOYED)
        assert before.hour == 3  # Saturday 24 Oct, still CEST
        assert after.hour == 17  # Tuesday 27 Oct, now CET
        assert after.utcoffset() != before.utcoffset()


SETTINGS = ScheduleSettings(slots=tuple(DEPLOYED))


class TestRunScheduler:
    """The loop itself: bounded via max_runs so it terminates in tests."""

    def test_runs_immediately_when_asked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """--run-now triggers one run before any waiting."""
        monkeypatch.setattr(scheduler, "_sleep_until", lambda *_a, **_kw: True)
        calls: list[int] = []
        scheduler.run_scheduler(
            SETTINGS, lambda: calls.append(1), run_immediately=True, max_runs=1
        )
        assert calls == [1]

    def test_a_failing_run_does_not_stop_the_schedule(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """One bad run must not silently end the deployment."""
        monkeypatch.setattr(scheduler, "_sleep_until", lambda *_a, **_kw: True)
        calls: list[int] = []

        def flaky() -> None:
            calls.append(len(calls))
            if len(calls) == 1:
                raise RuntimeError("scrape exploded")

        scheduler.run_scheduler(SETTINGS, flaky, max_runs=2)
        assert len(calls) == 2

    def test_settings_are_re_read_every_cycle(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A provider is re-invoked, so dashboard edits apply without a restart."""
        monkeypatch.setattr(scheduler, "_sleep_until", lambda *_a, **_kw: True)
        reads = {"n": 0}

        def provider() -> ScheduleSettings:
            reads["n"] += 1
            return SETTINGS

        scheduler.run_scheduler(provider, lambda: None, max_runs=2)
        assert reads["n"] >= 2

    def test_disabled_schedule_does_not_run(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Pausing from the dashboard stops runs but keeps the loop alive."""
        monkeypatch.setattr(scheduler, "_sleep_until", lambda *_a, **_kw: True)
        polls = {"n": 0}
        calls: list[int] = []

        def fake_sleep(_seconds: float) -> None:
            polls["n"] += 1
            if polls["n"] > 3:
                raise KeyboardInterrupt  # break out of the paused loop

        monkeypatch.setattr(scheduler.time_module, "sleep", fake_sleep)
        paused = ScheduleSettings(slots=tuple(DEPLOYED), enabled=False)
        with pytest.raises(KeyboardInterrupt):
            scheduler.run_scheduler(paused, lambda: calls.append(1), max_runs=1)
        assert calls == []

    def test_empty_slots_do_not_run(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A schedule with no times must not fire, and must not crash."""
        monkeypatch.setattr(scheduler, "_sleep_until", lambda *_a, **_kw: True)
        calls: list[int] = []

        def fake_sleep(_seconds: float) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(scheduler.time_module, "sleep", fake_sleep)
        with pytest.raises(KeyboardInterrupt):
            scheduler.run_scheduler(
                ScheduleSettings(slots=()), lambda: calls.append(1), max_runs=1
            )
        assert calls == []

    def test_describe_reports_pause(self) -> None:
        """The log line makes a paused schedule obvious."""
        assert ScheduleSettings(slots=(), enabled=False).describe() == "paused"
        assert "tue 17:00" in SETTINGS.describe()
