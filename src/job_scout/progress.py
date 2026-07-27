"""Live progress reporting for a running pipeline.

A full run takes minutes to well over an hour -- scraping, screening, two
evaluation passes, then enrichment -- and until it finishes the dashboard can
only say "running", which is indistinguishable from "stuck". This module lets
the pipeline publish which stage it is in and how far through, so the
dashboard can show real progress and a rough estimate of the time left.

The pipeline runs in a background thread of the dashboard process, so an
in-process registry is enough; nothing is persisted.
"""

from __future__ import annotations

import threading
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime

# Ordered so the dashboard can show "stage 3 of 6" without knowing the names.
STAGES: tuple[str, ...] = (
    "scraping",
    "screening",
    "quick_eval",
    "evaluating",
    "verifying",
    "enriching",
    "notifying",
)

_HUMAN_STAGES: dict[str, str] = {
    "scraping": "Scraping job boards",
    "screening": "Screening job titles",
    "quick_eval": "Quick scoring",
    "evaluating": "Full evaluation",
    "verifying": "Checking vacancies are still open",
    "enriching": "Finding employer pages and company reviews",
    "notifying": "Sending notifications",
}

# The user whose run is active on this thread. Set once per run so the
# reporting helpers do not have to be threaded through every call site.
_active_user: ContextVar[str | None] = ContextVar("_active_user", default=None)

_lock = threading.Lock()


@dataclass
class RunProgress:
    """How far a single pipeline run has got."""

    stage: str = ""
    current: int = 0
    total: int = 0
    detail: str = ""
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    stage_started_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def stage_label(self) -> str:
        """Return a human-readable name for the current stage."""
        return _HUMAN_STAGES.get(self.stage, self.stage.replace("_", " ").title())

    @property
    def stage_index(self) -> int:
        """Return the 1-based position of this stage, or 0 if unknown."""
        return STAGES.index(self.stage) + 1 if self.stage in STAGES else 0

    def eta_seconds(self) -> float | None:
        """Estimate the seconds left in the current stage.

        Extrapolates from how long the completed items in this stage took.
        Returns None until there is enough data to be meaningful, so the
        dashboard shows nothing rather than a wild first guess.

        Returns:
            Estimated seconds remaining, or None.
        """
        if self.total <= 0 or self.current <= 0 or self.current >= self.total:
            return None
        elapsed = (datetime.now(UTC) - self.stage_started_at).total_seconds()
        if elapsed <= 0:
            return None
        return (elapsed / self.current) * (self.total - self.current)

    def as_dict(self) -> dict[str, object]:
        """Render the progress as a JSON-friendly dictionary.

        Returns:
            Dictionary describing the current stage and position.
        """
        eta = self.eta_seconds()
        return {
            "stage": self.stage,
            "stage_label": self.stage_label,
            "stage_index": self.stage_index,
            "stage_count": len(STAGES),
            "current": self.current,
            "total": self.total,
            "detail": self.detail,
            "percent": round(100 * self.current / self.total) if self.total else None,
            "eta_seconds": round(eta) if eta is not None else None,
            "elapsed_seconds": round(
                (datetime.now(UTC) - self.started_at).total_seconds()
            ),
        }


_runs: dict[str | None, RunProgress] = {}


def begin_run(user: str | None) -> None:
    """Mark a run as started and bind it to the calling thread.

    Args:
        user: User the run belongs to, or None for the global run.
    """
    _active_user.set(user)
    with _lock:
        _runs[user] = RunProgress()


def end_run(user: str | None) -> None:
    """Discard progress for a finished run.

    Args:
        user: User the run belongs to.
    """
    with _lock:
        _runs.pop(user, None)


def set_stage(stage: str, total: int = 0, *, detail: str = "") -> None:
    """Record that the active run has entered a new stage.

    Args:
        stage: Stage key, ideally one of STAGES.
        total: Number of items this stage will process, if known.
        detail: Optional extra context for the dashboard.
    """
    user = _active_user.get()
    with _lock:
        progress = _runs.get(user)
        if progress is None:
            return
        progress.stage = stage
        progress.total = total
        progress.current = 0
        progress.detail = detail
        progress.stage_started_at = datetime.now(UTC)


def advance(detail: str = "", *, step: int = 1) -> None:
    """Record progress within the active stage.

    Args:
        detail: What was just processed, shown as the current item.
        step: How many items completed.
    """
    user = _active_user.get()
    with _lock:
        progress = _runs.get(user)
        if progress is None:
            return
        progress.current += step
        if detail:
            progress.detail = detail


def get(user: str | None) -> dict[str, object] | None:
    """Return the current progress for a user's run.

    Args:
        user: User the run belongs to.

    Returns:
        A JSON-friendly progress dictionary, or None when no run is tracked.
    """
    with _lock:
        progress = _runs.get(user)
        return progress.as_dict() if progress else None
