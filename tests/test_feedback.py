"""Tests for reviewing a CV or a motivational letter."""

from __future__ import annotations

import json

import pytest

from job_scout import feedback
from job_scout.llm.base import LLMError
from job_scout.models import CvProfile, CvRole, JobListing

_GOOD_RESPONSE = json.dumps(
    {
        "score": 62,
        "summary": "Solid history, but the letter never says why this employer.",
        "strengths": ["Concrete metrics in the NMi role", "Clear career progression"],
        "points": [
            {
                "section": "Opening paragraph",
                "severity": "important",
                "issue": "Generic opener that would suit any vacancy.",
                "suggestion": "Name what this employer does that you care about.",
                "example": "Your work on radar signature modelling is why I applied.",
            }
        ],
        "missing_keywords": ["Six Sigma", "ISO 17025"],
    }
)


class FakeLLMClient:
    """Returns a canned response and records the prompt it was given."""

    def __init__(self, response: str = _GOOD_RESPONSE) -> None:
        self.response = response
        self.prompts: list[str] = []

    def complete(self, prompt: str, *, purpose: str = "") -> str:
        """Record the prompt and return the canned response."""
        self.prompts.append(prompt)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class FailingLLMClient:
    """Raises, to check a failed review degrades rather than 500s."""

    def complete(self, prompt: str, *, purpose: str = "") -> str:
        """Always fail."""
        raise LLMError("model is down")


def _job() -> JobListing:
    """Build a sample vacancy."""
    return JobListing(
        title="Quality Engineer",
        company="Acme BV",
        location="Haarlem",
        url="https://example.com/job/1",
        source="test",
        description="We need ISO 17025 experience and Six Sigma methods.",
    )


def _profile() -> CvProfile:
    """Build a sample parsed CV profile."""
    return CvProfile(
        skills=["Python", "Measurement"],
        education=["MSc Applied Physics"],
        past_roles=[CvRole(title="Engineer", company="NMi", start_date="2020-01")],
        years_experience=8,
    )


class TestReviewCv:
    """A CV can be judged generally or against one vacancy."""

    def test_general_review_parses(self) -> None:
        """The happy path returns structured feedback."""
        review = feedback.review_cv("My CV text", client=FakeLLMClient())
        assert review.score == 62
        assert review.points[0].severity == "important"
        assert review.target == "General review"

    def test_general_review_does_not_mention_a_vacancy(self) -> None:
        """Without a job there is nothing to match against."""
        client = FakeLLMClient()
        feedback.review_cv("My CV text", client=client)
        assert "TARGET VACANCY" not in client.prompts[0]

    def test_job_specific_review_includes_the_vacancy(self) -> None:
        """With a job, the prompt must carry it."""
        client = FakeLLMClient()
        review = feedback.review_cv("My CV text", job=_job(), client=client)
        assert "Quality Engineer" in client.prompts[0]
        assert "ISO 17025" in client.prompts[0]
        assert review.target == "Quality Engineer at Acme BV"

    def test_profile_is_included_when_available(self) -> None:
        """Parsed context helps the model avoid inventing things."""
        client = FakeLLMClient()
        feedback.review_cv("My CV text", profile=_profile(), client=client)
        assert "MSc Applied Physics" in client.prompts[0]

    def test_empty_cv_is_rejected(self) -> None:
        """Reviewing nothing is a caller error, not an LLM call."""
        client = FakeLLMClient()
        with pytest.raises(ValueError, match="No CV text"):
            feedback.review_cv("   ", client=client)
        assert client.prompts == []

    def test_llm_failure_returns_a_message_not_an_exception(self) -> None:
        """The dashboard shows a message rather than an error page."""
        review = feedback.review_cv("My CV text", client=FailingLLMClient())
        assert review.points == []
        assert "Could not generate feedback" in review.summary

    def test_malformed_json_is_handled(self) -> None:
        """A model that ignores the schema must not crash the endpoint."""
        review = feedback.review_cv("My CV", client=FakeLLMClient("not json at all"))
        assert "Could not generate feedback" in review.summary


class TestReviewCoverLetter:
    """A letter is always judged against the vacancy it answers."""

    def test_includes_job_and_letter(self) -> None:
        """Both must reach the prompt."""
        client = FakeLLMClient()
        review = feedback.review_cover_letter(
            "Geachte heer/mevrouw, ...", _job(), client=client
        )
        assert "Quality Engineer" in client.prompts[0]
        assert "Geachte heer/mevrouw" in client.prompts[0]
        assert review.target == "Quality Engineer at Acme BV"

    def test_asks_about_dutch_tone(self) -> None:
        """The whole point is a letter that lands in this market."""
        client = FakeLLMClient()
        feedback.review_cover_letter("Beste, ...", _job(), client=client)
        assert "Dutch business culture" in client.prompts[0]

    def test_empty_letter_is_rejected(self) -> None:
        """Nothing to review."""
        with pytest.raises(ValueError, match="No letter text"):
            feedback.review_cover_letter("  ", _job(), client=FakeLLMClient())

    def test_missing_keywords_are_surfaced(self) -> None:
        """These drive the chips in the dashboard."""
        review = feedback.review_cover_letter("text", _job(), client=FakeLLMClient())
        assert review.missing_keywords == ["Six Sigma", "ISO 17025"]


class TestCaps:
    """Output is bounded so one bad review cannot flood the panel."""

    def test_points_are_capped(self) -> None:
        """A model returning 50 points must not render 50."""
        many = json.dumps(
            {
                "summary": "x",
                "points": [
                    {"issue": f"issue {i}", "severity": "suggestion"} for i in range(50)
                ],
            }
        )
        review = feedback.review_cv("cv", client=FakeLLMClient(many))
        assert len(review.points) == feedback._MAX_POINTS

    def test_points_without_an_issue_are_dropped(self) -> None:
        """An empty entry would render as a blank card."""
        payload = json.dumps(
            {"summary": "x", "points": [{"issue": ""}, {"issue": "real problem"}]}
        )
        review = feedback.review_cv("cv", client=FakeLLMClient(payload))
        assert len(review.points) == 1
        assert review.points[0].issue == "real problem"

    def test_non_integer_score_becomes_none(self) -> None:
        """A model returning "high" must not break the score badge."""
        payload = json.dumps({"score": "high", "summary": "x", "points": []})
        assert feedback.review_cv("cv", client=FakeLLMClient(payload)).score is None
