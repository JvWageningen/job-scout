"""Every generator that writes prose for the applicant follows the house style.

Each test feeds a fake model the marks of generated text (dashes, a spaced
hyphen, markdown bold) and checks that what comes back is plain, that the
prompt carried the house style, and that facts other code compares are left
exactly as the model returned them. The letter writer, the interview
generators and the CV tailor are covered next to their own fixtures.
"""

from __future__ import annotations

import json
from typing import Any

from job_scout import coach, cover_letter_generator, feedback, interview_prep
from job_scout.cli import _eval_job_full_parallel
from job_scout.coach import CoachAnswer
from job_scout.evaluator import evaluate_fit
from job_scout.models import Config, CvProfile, CvRole, JobListing
from job_scout.resume_tailor import tailor_resume_text
from tests.helpers import FakeLLMClient
from tests.style_checks import GENERATED, PLAIN, assert_plain, assert_styled_prompt

EM, EN, EURO = "\u2014", "\u2013", "\u20ac"


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

    def test_a_quoted_sentence_still_shows_what_the_letter_says(self) -> None:
        """The review quotes the letter's dashes; cleaning them would misquote it.

        Only the quotation is kept. The model's own words around it, and the
        rewrite the candidate may paste, are cleaned as usual.
        """
        quoted = f'"Ik ben {EM} echt {EM} gemotiveerd"'
        response = json.dumps(
            {
                "summary": f"De opening {quoted} leest als gegenereerd {EM} jammer.",
                "points": [
                    {
                        "issue": f"De zin {quoted} gebruikt gedachtestreepjes.",
                        "suggestion": f'Vervang "{EM}" door een punt {EM} of schrap.',
                        "example": f"Ik ben gemotiveerd {EM} echt.",
                    }
                ],
            }
        )

        review = feedback.review_cover_letter(
            "Beste team, ...", _job(), client=FakeLLMClient([response])
        )

        assert review.summary == f"De opening {quoted} leest als gegenereerd, jammer."
        point = review.points[0]
        assert point.issue == f"De zin {quoted} gebruikt gedachtestreepjes."
        assert point.suggestion == f'Vervang "{EM}" door een punt, of schrap.'
        assert point.example == "Ik ben gemotiveerd, echt."

    def test_the_prompt_asks_for_exact_quotes(self) -> None:
        client = FakeLLMClient([self._RESPONSE])

        feedback.review_cover_letter("Beste team, ...", _job(), client=client)

        assert "exactly as the letter has it, in double" in client.calls[0][0]

    def test_a_point_or_strength_left_empty_by_the_clean_up_is_dropped(
        self,
    ) -> None:
        """An emoji-only strength would otherwise show as an empty bullet."""
        response = json.dumps(
            {
                "summary": "Degelijk.",
                "strengths": ["\u2705", "Concreet"],
                "points": [{"issue": "\U0001f680"}, {"issue": "Te lang."}],
            }
        )

        review = feedback.review_cv("CV", client=FakeLLMClient([response]))

        assert review.strengths == ["Concreet"]
        assert [point.issue for point in review.points] == ["Te lang."]


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


def test_a_salary_range_in_the_compensation_reasoning_stays_a_range() -> None:
    """Stating the range is what this field is for; a comma would split it."""
    response = json.dumps(
        {
            "fit_score": 64,
            "fit_reasoning": "Past goed.",
            "matches_negative": False,
            "negative_reasoning": "Geen.",
            "salary_min": 3500,
            "salary_max": 4800,
            "salary_period": "monthly",
            "vacation_days": None,
            "compensation_reasoning": (
                f"Schaal 10: {EURO} 3.500 - {EURO} 4.800 per maand, "
                f"ofwel EUR 42k{EN}58k per jaar {EM} marktconform."
            ),
        }
    )

    _, _, compensation = evaluate_fit(
        _job(), "Kwaliteitsadviseur", "", "", client=FakeLLMClient([response])
    )

    assert compensation.reasoning == (
        f"Schaal 10: {EURO} 3.500-{EURO} 4.800 per maand, "
        "ofwel EUR 42k-58k per jaar, marktconform."
    )


class _CachedEvaluation:
    """A database whose evaluation cache predates the house style."""

    def get_cached_evaluation(self, job: JobListing) -> tuple[int, dict[str, Any]]:
        return 72, {
            "fit_reasoning": GENERATED,
            "negative_match": False,
            "negative_reasoning": None,
            "salary_min": None,
            "salary_max": None,
            "salary_period": None,
            "vacation_days": None,
            "compensation_reasoning": f"{EURO}3.500 {EN} {EURO}4.800 {EM} **prima**",
        }


def test_reasoning_reused_from_the_evaluation_cache_is_cleaned_too() -> None:
    """A new vacancy row must not bring back the dashes of an old evaluation."""
    job = _job()
    client = FakeLLMClient([])

    _eval_job_full_parallel(
        (job, Config(), "", client, _CachedEvaluation())  # type: ignore[arg-type]
    )

    assert client.calls == []
    assert job.fit_score == 72
    assert job.fit_reasoning == PLAIN
    assert job.negative_reasoning is None
    assert job.compensation_reasoning == f"{EURO}3.500-{EURO}4.800, prima"


def test_a_tailored_resume_comes_back_plain_and_keeps_its_date_ranges() -> None:
    """On a date line the dash is a range: it becomes a hyphen, not a comma."""
    client = FakeLLMClient(
        [
            f"PROFIEL\n{GENERATED}\n\nERVARING\n"
            f"Adviseur, Voorbeeld BV, jan 2019 {EN} heden"
        ]
    )

    tailored = tailor_resume_text(
        "Adviseur bij Voorbeeld BV.",
        _profile(),
        "Meetmethoden bewaken.",
        keywords=["metrologie"],
        client=client,
    )

    assert tailored == (
        f"PROFIEL\n{PLAIN}\n\nERVARING\nAdviseur, Voorbeeld BV, jan 2019-heden"
    )
    assert_styled_prompt(client.calls[0][0])


def test_the_applicants_own_cv_lines_keep_their_punctuation() -> None:
    """The model is told to keep the CV's own lines; so is the clean-up.

    A dash between a language and its level, or an employer and its team, is
    the applicant's; only the lines the model reworded follow the style.
    """
    cv_text = (
        "Sam de Vries\n+31 6 - 1234 5678\n"
        "Data Engineer - Deltameet Institute - Mobility team\n"
        f"Hewlett Packard Enterprise {EN} Amstelveen\n"
        f"Talen: Nederlands {EN} moedertaal, Engels {EN} vloeiend\n"
        "Bouwde ETL-pipelines voor meetdata."
    )
    client = FakeLLMClient(
        [
            "Sam de Vries\n+31 6 - 1234 5678\n"
            "Data Engineer - Deltameet Institute - Mobility team\n"
            "Hewlett Packard Enterprise - Amstelveen\n"
            f"Talen: Nederlands {EN} moedertaal, Engels {EN} vloeiend\n"
            "* Bouwde **ETL-pipelines** voor meetdata.\n"
            f"- Leidde in 2021 de migratie {EM} kosten 30% omlaag"
        ]
    )

    tailored = tailor_resume_text(
        cv_text, _profile(), "Meetmethoden bewaken.", keywords=["x"], client=client
    )

    assert tailored.splitlines() == [
        "Sam de Vries",
        "+31 6 - 1234 5678",
        "Data Engineer - Deltameet Institute - Mobility team",
        "Hewlett Packard Enterprise - Amstelveen",
        f"Talen: Nederlands {EN} moedertaal, Engels {EN} vloeiend",
        "* Bouwde ETL-pipelines voor meetdata.",
        "- Leidde in 2021 de migratie, kosten 30% omlaag",
    ]


def test_a_resume_line_that_mentions_a_year_is_still_cleaned() -> None:
    """A profile sentence or a bullet with a year in it is prose, not a date line."""
    client = FakeLLMClient(
        [
            f"Data engineer sinds 2019 {EM} **robuuste** pipelines \U0001f680\n"
            f"- Leidde in 2021 de migratie {EM} kosten 30% omlaag - en **snel**\n"
            "- Mail: jan__de__vries@example.nl"
        ]
    )

    tailored = tailor_resume_text(
        "CV", _profile(), "Meetmethoden bewaken.", keywords=["x"], client=client
    )

    assert tailored == (
        "Data engineer sinds 2019, robuuste pipelines\n"
        "- Leidde in 2021 de migratie, kosten 30% omlaag, en snel\n"
        "- Mail: jan__de__vries@example.nl"
    )
