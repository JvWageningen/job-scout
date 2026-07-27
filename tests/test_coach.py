"""Tests for the guided job-coach intake."""

from __future__ import annotations

import json

from job_scout.coach import (
    CoachAnswer,
    _build_proposal_prompt,
    _coerce_tracks,
    _format_cv,
    _unique_id,
    baseline_questions,
    propose_tracks,
)
from job_scout.models import CvProfile, CvRole


class FakeLLMClient:
    """Minimal LLM client stub returning canned responses."""

    def __init__(self, responses: list[str]) -> None:
        """Initialise the stub.

        Args:
            responses: Responses to return, in order.
        """
        self.responses = responses
        self.calls: list[tuple[str, str]] = []

    def complete(self, prompt: str, purpose: str = "") -> str:
        """Return the next canned response.

        Args:
            prompt: Recorded for assertions.
            purpose: Recorded for assertions.

        Returns:
            The next canned response.

        Raises:
            LLMError: When no response is available.
        """
        from job_scout.llm.base import LLMError

        self.calls.append((prompt, purpose))
        if not self.responses:
            raise LLMError("no response")
        return self.responses.pop(0)


def _cv() -> CvProfile:
    """Build a CV profile for grounding tests."""
    return CvProfile(
        skills=["Metrology", "Python", "Optics"],
        years_experience=7,
        education=["BSc Applied Physics"],
        past_roles=[
            CvRole(title="Sales Engineer", company="Laser 2000", end_date="2024-07"),
            CvRole(title="Approval Expert", company="NMi", end_date=None),
        ],
    )


class TestBaselineQuestions:
    """The intake must be answerable by someone who is unsure."""

    def test_offers_an_explicit_dont_know_option(self) -> None:
        """Someone with no clear direction has something to pick."""
        options = baseline_questions(None)[0].options
        assert any("don't know" in o.lower() for o in options)

    def test_every_question_allows_being_unsure(self) -> None:
        """No question forces a confident answer."""
        assert all(q.allow_unsure for q in baseline_questions(None))

    def test_grounds_options_in_cv_skills(self) -> None:
        """Skills from the CV are offered instead of a blank box."""
        strengths = next(q for q in baseline_questions(_cv()) if q.id == "strengths")
        assert "Metrology" in strengths.options

    def test_hints_reference_recent_roles(self) -> None:
        """Recent roles are surfaced to jog the candidate's memory."""
        liked = next(q for q in baseline_questions(_cv()) if q.id == "liked")
        assert "Approval Expert" in liked.hint

    def test_works_without_a_cv(self) -> None:
        """The intake still functions when no CV is configured."""
        questions = baseline_questions(None)
        assert len(questions) >= 4
        assert all(q.question for q in questions)

    def test_asks_about_blended_interests(self) -> None:
        """There is a question for things wanted within a role, not as the job."""
        blend = next(q for q in baseline_questions(None) if q.id == "blend")
        assert "whole job" in blend.question.lower()


class TestProposalPrompt:
    """The prompt must steer away from vague or duplicated directions."""

    def test_includes_cv_context(self) -> None:
        """CV details reach the model."""
        prompt = _build_proposal_prompt([CoachAnswer(id="a", answer="x")], _cv())
        assert "Metrology" in prompt
        assert "NMi" in prompt

    def test_marks_the_current_role(self) -> None:
        """The model is told which role is current."""
        assert "(current)" in _format_cv(_cv())

    def test_requires_concrete_searchable_directions(self) -> None:
        """The prompt rejects vague labels explicitly."""
        prompt = _build_proposal_prompt([], None)
        assert "specific enough to search for" in prompt

    def test_explains_the_blend_concept(self) -> None:
        """The model knows when to emit a blend rather than a track."""
        prompt = _build_proposal_prompt([], None)
        assert '"mode": "blend"' in prompt

    def test_handles_no_answers(self) -> None:
        """Empty answers fall back to the CV rather than breaking."""
        assert "rely on the CV" in _build_proposal_prompt([], _cv())


class TestProposeTracks:
    """Turning answers into tracks."""

    def _response(self) -> str:
        return json.dumps(
            {
                "summary": "Two directions based on your metrology work.",
                "negative_description": "No people management.",
                "follow_up": "How much travel is acceptable?",
                "tracks": [
                    {
                        "name": "Quality & Efficiency",
                        "description": "Quality and process improvement.",
                        "mode": "standalone",
                        "keywords_dutch": ["kwaliteits engineer"],
                        "keywords_english": ["quality engineer"],
                    },
                    {
                        "name": "AI & coding",
                        "description": "building AI tools alongside the main work",
                        "mode": "blend",
                    },
                ],
            }
        )

    def test_parses_tracks_and_modes(self) -> None:
        """Standalone and blend tracks are distinguished."""
        proposal = propose_tracks(
            [CoachAnswer(id="direction", answer="not sure")],
            cv=_cv(),
            client=FakeLLMClient([self._response()]),
        )
        assert [t.mode for t in proposal.tracks] == ["standalone", "blend"]
        assert proposal.tracks[0].name == "Quality & Efficiency"

    def test_derives_readable_ids(self) -> None:
        """Track ids are slugs of their names, not opaque identifiers."""
        proposal = propose_tracks(
            [CoachAnswer(id="a", answer="x")],
            client=FakeLLMClient([self._response()]),
        )
        assert proposal.tracks[0].id == "quality-efficiency"

    def test_carries_summary_and_follow_up(self) -> None:
        """The candidate sees reasoning, not just output."""
        proposal = propose_tracks(
            [CoachAnswer(id="a", answer="x")],
            client=FakeLLMClient([self._response()]),
        )
        assert "metrology" in proposal.summary
        assert proposal.follow_up
        assert proposal.negative_description == "No people management."

    def test_llm_failure_yields_empty_proposal(self) -> None:
        """A failed call must not wipe the existing profile."""
        proposal = propose_tracks(
            [CoachAnswer(id="a", answer="x")], client=FakeLLMClient([])
        )
        assert proposal.tracks == []

    def test_malformed_json_yields_empty_proposal(self) -> None:
        """Unparseable output degrades gracefully."""
        proposal = propose_tracks(
            [CoachAnswer(id="a", answer="x")], client=FakeLLMClient(["not json"])
        )
        assert proposal.tracks == []


class TestCoerceTracks:
    """Proposals must always be searchable."""

    def test_drops_entries_without_a_name(self) -> None:
        """Nameless tracks are skipped."""
        assert _coerce_tracks([{"description": "x"}]) == []

    def test_rejects_non_list(self) -> None:
        """A non-list payload yields nothing."""
        assert _coerce_tracks("nope") == []

    def test_promotes_when_all_tracks_are_blend(self) -> None:
        """An all-blend proposal would search nothing, so it is repaired."""
        tracks = _coerce_tracks(
            [
                {"name": "AI", "description": "ai", "mode": "blend"},
                {"name": "Coding", "description": "code", "mode": "blend"},
            ]
        )
        assert tracks[0].mode == "standalone"
        assert any(t.mode == "standalone" for t in tracks)

    def test_deduplicates_ids(self) -> None:
        """Two tracks with the same name still get distinct ids."""
        tracks = _coerce_tracks(
            [{"name": "Quality"}, {"name": "Quality"}],
        )
        assert len({t.id for t in tracks}) == 2

    def test_caps_the_number_of_tracks(self) -> None:
        """A runaway proposal is truncated."""
        tracks = _coerce_tracks([{"name": f"T{i}"} for i in range(10)])
        assert len(tracks) <= 4

    def test_unique_id_suffixes(self) -> None:
        """Collisions get a numeric suffix."""
        assert _unique_id("quality", {"quality"}) == "quality-2"
        assert _unique_id("quality", {"quality", "quality-2"}) == "quality-3"
