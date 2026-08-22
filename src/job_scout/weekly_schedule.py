"""A small weekly scheduler for running the pipeline inside a container.

The container is the whole deployment, so it carries its own schedule rather
than relying on the host's cron -- ASUSTOR's ADM restricts user crontabs, and
a self-contained image is portable to any Docker host.

Times are evaluated in a real timezone rather than UTC so that a 17:00 run
stays at 17:00 across a daylight-saving change, which is what someone reading
"Tuesday at 17:00" expects.
"""

from __future__ import annotations

import time as time_module
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from loguru import logger

DEFAULT_TIMEZONE = "Europe/Amsterdam"

# Monday is 0, matching datetime.weekday().
WEEKDAY_NAMES: dict[str, int] = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}

# Longest a single sleep may last. Waking periodically keeps the loop
# responsive to shutdown and re-derives the target after a clock or DST jump.
_MAX_SLEEP_SECONDS = 300.0

# How often to re-check settings while the schedule is paused, so resuming
# from the dashboard takes effect without restarting the container.
_PAUSED_POLL_SECONDS = 30.0


@dataclass(frozen=True)
class ScheduleSlot:
    """One recurring weekly run time."""

    weekday: int
    hour: int
    minute: int = 0

    def __post_init__(self) -> None:
        """Reject out-of-range values early.

        Raises:
            ValueError: If the weekday or clock time is out of range.
        """
        if not 0 <= self.weekday <= 6:
            raise ValueError(f"weekday must be 0-6, got {self.weekday}")
        if not 0 <= self.hour <= 23:
            raise ValueError(f"hour must be 0-23, got {self.hour}")
        if not 0 <= self.minute <= 59:
            raise ValueError(f"minute must be 0-59, got {self.minute}")

    def __str__(self) -> str:
        """Return a human-readable ``tue 17:00`` style label."""
        name = next(k for k, v in WEEKDAY_NAMES.items() if v == self.weekday)
        return f"{name} {self.hour:02d}:{self.minute:02d}"


@dataclass(frozen=True)
class ScheduleSettings:
    """Everything the loop needs to decide when to run next.

    Passed as a provider callable so the dashboard can change the schedule
    while the container keeps running.
    """

    slots: tuple[ScheduleSlot, ...]
    timezone: str = DEFAULT_TIMEZONE
    enabled: bool = True

    def describe(self) -> str:
        """Return a one-line summary for logging."""
        if not self.enabled:
            return "paused"
        return ", ".join(str(s) for s in self.slots) or "no slots"


def parse_slots(spec: str) -> list[ScheduleSlot]:
    """Parse a schedule specification into slots.

    Args:
        spec: Comma-separated ``day:HH:MM`` entries, e.g.
            ``"tue:17:00,sat:03:00"``. Day names are the first three
            letters, case-insensitive.

    Returns:
        The parsed slots, in the order given.

    Raises:
        ValueError: If any entry is malformed or names an unknown day.
    """
    slots: list[ScheduleSlot] = []
    for raw in spec.split(","):
        entry = raw.strip()
        if not entry:
            continue
        parts = entry.split(":")
        if len(parts) != 3:
            raise ValueError(f"Expected 'day:HH:MM', got {entry!r}")
        day = parts[0].strip().lower()[:3]
        if day not in WEEKDAY_NAMES:
            raise ValueError(f"Unknown day {parts[0]!r} in {entry!r}")
        try:
            slots.append(ScheduleSlot(WEEKDAY_NAMES[day], int(parts[1]), int(parts[2])))
        except ValueError as exc:
            raise ValueError(f"Bad time in {entry!r}: {exc}") from exc
    if not slots:
        raise ValueError(f"No schedule entries found in {spec!r}")
    return slots


def _next_for_slot(now: datetime, slot: ScheduleSlot) -> datetime:
    """Return the first occurrence of one slot strictly after ``now``.

    Args:
        now: Timezone-aware reference time.
        slot: The weekly slot to project forward.

    Returns:
        The next matching datetime, in ``now``'s timezone.
    """
    for days_ahead in range(8):
        day = (now + timedelta(days=days_ahead)).date()
        if day.weekday() != slot.weekday:
            continue
        candidate = datetime.combine(
            day, time(slot.hour, slot.minute), tzinfo=now.tzinfo
        )
        if candidate > now:
            return candidate
    # Only reachable when today matched but the time had passed; the same
    # weekday next week is then the answer.
    day = (now + timedelta(days=7)).date()
    return datetime.combine(day, time(slot.hour, slot.minute), tzinfo=now.tzinfo)


def next_run_after(now: datetime, slots: Sequence[ScheduleSlot]) -> datetime:
    """Return the earliest scheduled time strictly after ``now``.

    Args:
        now: Timezone-aware reference time.
        slots: The weekly slots to consider.

    Returns:
        The soonest upcoming run time.

    Raises:
        ValueError: If no slots were given or ``now`` is naive.
    """
    if not slots:
        raise ValueError("At least one schedule slot is required")
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return min(_next_for_slot(now, slot) for slot in slots)


def run_scheduler(
    settings: ScheduleSettings | Callable[[], ScheduleSettings],
    action: Callable[[], None],
    *,
    run_immediately: bool = False,
    max_runs: int | None = None,
) -> None:
    """Run ``action`` on every scheduled occurrence, forever.

    Settings are re-read at the top of every cycle, so passing a provider
    callable lets the dashboard change the schedule -- or pause it -- without
    restarting the process. A failing action is logged and the loop
    continues, so one bad run does not silently end the schedule.

    Args:
        settings: The schedule, or a callable returning it. A callable is
            re-invoked each cycle to pick up changes.
        action: Callable invoked at each occurrence.
        run_immediately: Run once at start-up before waiting.
        max_runs: Stop after this many runs; None means never stop. Intended
            for tests.
    """
    provider = settings if callable(settings) else (lambda: settings)
    logger.info(f"Scheduler started; schedule: {provider().describe()}")

    runs = 0
    if run_immediately:
        runs += _invoke(action)

    previous = ""
    while max_runs is None or runs < max_runs:
        current = provider()
        summary = f"{current.describe()} ({current.timezone})"
        if summary != previous:
            logger.info(f"Schedule is now: {summary}")
            previous = summary

        if not current.enabled or not current.slots:
            # Paused from the dashboard. Keep polling so it can be resumed
            # without a restart, rather than exiting the loop.
            time_module.sleep(_PAUSED_POLL_SECONDS)
            continue

        tz = ZoneInfo(current.timezone)
        target = next_run_after(datetime.now(tz), current.slots)
        logger.info(f"Next run at {target.isoformat()}")
        if not _sleep_until(target, tz, provider=provider, baseline=current):
            # The schedule changed while waiting; recompute rather than
            # firing at a time the user has since edited away.
            continue
        runs += _invoke(action)


def _invoke(action: Callable[[], None]) -> int:
    """Call the action, logging any failure.

    Args:
        action: Callable to invoke.

    Returns:
        1 always, so callers can count attempts.
    """
    started = datetime.now(UTC)
    try:
        action()
    except Exception as exc:  # noqa: BLE001 - the loop must survive any failure
        logger.exception(f"Scheduled run failed: {exc}")
    else:
        elapsed = (datetime.now(UTC) - started).total_seconds()
        logger.info(f"Scheduled run finished in {elapsed:.0f}s")
    return 1


def _sleep_until(
    target: datetime,
    tz: ZoneInfo,
    *,
    provider: Callable[[], ScheduleSettings] | None = None,
    baseline: ScheduleSettings | None = None,
) -> bool:
    """Sleep in bounded steps until ``target`` passes.

    Args:
        target: Timezone-aware moment to wait for.
        tz: Timezone used to re-read the current time.
        provider: Optional settings provider, checked between steps so an
            edit made in the dashboard is noticed while waiting.
        baseline: The settings the target was derived from.

    Returns:
        True if the target was reached, False if the schedule changed first.
    """
    while True:
        remaining = (target - datetime.now(tz)).total_seconds()
        if remaining <= 0:
            return True
        time_module.sleep(min(remaining, _MAX_SLEEP_SECONDS))
        if provider is not None and baseline is not None and provider() != baseline:
            logger.info("Schedule changed while waiting; recomputing")
            return False
