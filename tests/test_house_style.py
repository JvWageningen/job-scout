"""Every generator that writes prose for the applicant follows the house style.

Each test feeds a fake model the marks of generated text (dashes, a spaced
hyphen, markdown bold) and checks that what comes back is plain, that the
prompt carried the house style, and that facts other code compares are left
exactly as the model returned them. The letter writer, the interview
generators and the CV tailor are covered next to their own fixtures.
"""

from __future__ import annotations

import json

from job_scout import coach, cover_letter_generator, feedback, interview_prep
from job_scout.coach import CoachAnswer
from job_scout.evaluator import evaluate_fit
from job_scout.models import CvProfile, CvRole, JobListing
from job_scout.resume_tailor import tailor_resume_text
from tests.helpers import FakeLLMClient
from tests.style_checks import GENERATED, PLAIN, assert_plain, assert_styled_prompt


def _job() -> JobListing:
    """Build a vacancy with nothing in it that the house style would ban."""
    return JobListing(
        title="Kwaliteitsadviseur",
        company="Voorbeeld Meettechniek",
        location="Deventer",
        url="https://voorbeeld.example/vacature/1",
        source="test",
        description="Je bewaakt de kwaliteit van onze meetmethoden en de audits.",
    )


def _profile() -> CvProfile:
    """Build a parsed CV profile, including a role without an end date."""
    return CvProfile(
        skills=["Python", "Metrologie"],
        education=["MSc Toegepaste Natuurkunde"],
        past_roles=[
            CvRole(title="Adviseur", company="Voorbeeld BV", start_date="2020")
        ],
        years_experience=6,
    )


class TestCoverLetterGenerator:
    """The older letter command, screening questions and screening answers."""

    def test_the_letter_comes_back_plain(self) -> None:
        client = FakeLLMClient(
            [f"Beste team,\n\n{GENERATED}\n\nMet vriendelijke groet,"]
        )

        letter = cover_letter_generator.generate_cover_letter(
            _profile(), "Meetmethoden bewaken.", "Adviseur", "Voorbeeld", client=client
        )

        assert letter == f"Beste team,\n\n{PLAIN}\n\nMet vriendelijke groet,"
        assert_styled_prompt(client.calls[0][0])

    def test_screening_questions_come_back_plain(self) -> None:
        response = json.dumps({"questions": [GENERATED, "\U0001f680"]})
        client = FakeLLMClient([response])

        questions = cover_letter_generator.extract_screening_questions(
            "Meetmethoden bewaken.", client=client
        )

        assert questions == [PLAIN]
        assert_styled_prompt(client.calls[0][0])

    def test_screening_answers_come_back_plain_under_their_own_question(
        self,
    ) -> None:
        question = "Hoeveel jaar werk je met meetreeksen?"
        client = FakeLLMClient([json.dumps({"answers": {question: GENERATED}})])

        answers = cover_letter_generator.answer_screening_questions(
            [question], _profile(), "Meetmethoden bewaken.", client=client
        )

        assert answers == {question: PLAIN}
        assert_styled_prompt(client.calls[0][0])


def test_interview_prep_questions_come_back_plain_and_keep_their_keywords() -> None:
    """Keywords are matched against STAR stories, so they are not restyled."""
    keyword = "CRO \u2014 A/B"
    response = json.dumps(
        {"questions": [{"question": GENERATED, "keywords": [keyword]}]}
    )
    client = FakeLLMClient([response])

    questions = interview_prep.extract_behavioral_questions(
        "Meetmethoden bewaken.", client=client
    )

    assert [q.question for q in questions] == [PLAIN]
    assert questions[0].keywords == [keyword]
    assert_styled_prompt(client.calls[0][0])


class TestFeedback:
    """A document review is prose the candidate reads, and may paste from."""

    _RESPONSE = json.dumps(
        {
            "score": 58,
            "summary": GENERATED,
            "strengths": [GENERATED],
            "points": [
                {
                    "section": "Opening \u2014 alinea",
                    "severity": "important",
                    "issue": GENERATED,
                    "suggestion": GENERATED,
                    "example": GENERATED,
                }
            ],
            "missing_keywords": ["ISO 17025"],
        }
    )

    def test_a_letter_review_comes_back_plain(self) -> None:
        client = FakeLLMClient([self._RESPONSE])

        review = feedback.review_cover_letter("Beste team, ...", _job(), client=client)

        assert review.summary == PLAIN
        assert review.strengths == [PLAIN]
        point = review.points[0]
        assert (point.issue, point.suggestion, point.example) == (PLAIN, PLAIN, PLAIN)
        assert point.section == "Opening, alinea"
        assert point.severity == "important"
        assert review.missing_keywords == ["ISO 17025"]
        assert_styled_prompt(client.calls[0][0])

    def test_a_cv_review_carries_the_house_style(self) -> None:
        client = FakeLLMClient([self._RESPONSE])

        review = feedback.review_cv("Adviseur bij Voorbeeld BV.", client=client)

        assert review.summary == PLAIN
        assert_styled_prompt(client.calls[0][0])


class TestCoach:
    """The coach's proposal and its own intake questions."""

    def test_a_proposal_comes_back_plain_and_keeps_its_labels(self) -> None:
        response = json.dumps(
            {
                "summary": GENERATED,
                "negative_description": GENERATED,
                "follow_up": GENERATED,
                "tracks": [
                    {
                        "name": "Kwaliteit en proces",
                        "description": GENERATED,
                        "mode": "standalone",
                        "keywords_dutch": ["kwaliteitsadviseur"],
                        "keywords_english": ["quality advisor"],
                    }
                ],
            }
        )
        client = FakeLLMClient([response])

        proposal = coach.propose_tracks(
            [CoachAnswer(id="direction", answer="Meer metingen")],
            cv=_profile(),
            client=client,
        )

        assert (proposal.summary, proposal.follow_up) == (PLAIN, PLAIN)
        assert proposal.negative_description == PLAIN
        track = proposal.tracks[0]
        assert track.description == PLAIN
        assert track.name == "Kwaliteit en proces"
        assert track.keywords_dutch == ["kwaliteitsadviseur"]
        assert_styled_prompt(client.calls[0][0])

    def test_the_intake_questions_follow_the_house_style_too(self) -> None:
        for question in coach.baseline_questions(_profile()):
            assert_plain(question.question)
            assert_plain(question.hint)
            assert "--" not in question.question + question.hint


def test_the_evaluation_reasoning_comes_back_plain() -> None:
    """The traceable 'Title-only:' prefix survives, being a hyphen in a word."""
    response = json.dumps(
        {
            "fit_score": 64,
            "fit_reasoning": f"Title-only: {GENERATED}",
            "matches_negative": False,
            "negative_reasoning": GENERATED,
            "salary_min": None,
            "salary_max": None,
            "salary_period": None,
            "vacation_days": None,
            "compensation_reasoning": GENERATED,
        }
    )
    client = FakeLLMClient([response])

    fit, negative, compensation = evaluate_fit(
        _job(), "Kwaliteitsadviseur", "", "", client=client
    )

    assert fit.reasoning == f"Title-only: {PLAIN}"
    assert negative.reasoning == PLAIN
    assert compensation.reasoning == PLAIN
    assert fit.fit_score == 64
    assert_styled_prompt(client.calls[0][0])


def test_a_tailored_resume_comes_back_plain_but_keeps_its_date_lines() -> None:
    """On a date line the dash is a range, and a comma would change it."""
    date_line = "Adviseur, Voorbeeld BV, jan 2019 \u2013 heden"
    client = FakeLLMClient([f"PROFIEL\n{GENERATED}\n\nERVARING\n{date_line}"])

    tailored = tailor_resume_text(
        "Adviseur bij Voorbeeld BV.",
        _profile(),
        "Meetmethoden bewaken.",
        keywords=["metrologie"],
        client=client,
    )

    assert tailored == f"PROFIEL\n{PLAIN}\n\nERVARING\n{date_line}"
    assert_styled_prompt(client.calls[0][0])
