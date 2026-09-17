"""Contracts for the questions the candidate asks the employer.

All people and employers here are invented. The repository is public, so no
fixture may carry a real name, address or company.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

import job_scout.config as config
from job_scout.cv.models import (
    ContactItem,
    ContactSection,
    CVDocument,
    DetailItem,
    DetailsSection,
    TextSection,
)
from job_scout.cv.storage import ProfileStore
from job_scout.database import Database
from job_scout.interview_questions import (
    InterviewQuestion,
    InterviewQuestionError,
    QuestionTheme,
    generate_interview_questions,
)
from job_scout.letters.models import LetterLanguage
from job_scout.models import (
    CompanyResearch,
    CompanyReview,
    HiringManagerSuggestion,
    JobListing,
)
from tests.helpers import FakeLLMClient

USER = "Sam"
COMPANY = "Deltameet Institute"
EMAIL = "sam.de.vries@voorbeeld.example"
PHONE = "06-11122233"
BIRTHDAY = "04-04-1984"
NL_EMPLOYER = "Ravelijn Zorggroep"
EN_EMPLOYER = "Ravelijn Care Group"
MANAGER = "Fenna Oosterhuis"
CON = "werkdruk piekt rond de audits"
CULTURE = "zelfsturende teams"

DUTCH_DESCRIPTION = (
    "Wij zoeken een collega die de kwaliteit van onze meetmethoden bewaakt. "
    "Je werkt in een team dat metingen uitvoert voor publieke opdrachtgevers "
    "en je ondersteunt het team bij de audits."
)
ENGLISH_DESCRIPTION = (
    "You will join the quality team and support the measurement programme. "
    "We are looking for a colleague who can improve the calibration process "
    "and who will work with the laboratory on the yearly audits."
)

RESEARCH = CompanyResearch(
    company_name=COMPANY,
    industry="Meetdiensten",
    company_size="120 medewerkers",
    culture_indicators=[CULTURE, "weinig hiërarchie"],
    tech_stack_hints=["Python", "LIMS"],
    growth_signals="Tweede vestiging geopend in 2025",
    research_notes="Werkt vooral voor publieke opdrachtgevers.",
    hiring_managers=[HiringManagerSuggestion(name=MANAGER, role="Teamleider")],
)
REVIEW = CompanyReview(
    company=COMPANY,
    work_score=64,
    summary="Solide werkgever met wisselende werkdruk.",
    pros=["goede begeleiding van nieuwe collega's"],
    cons=[CON, "loopbaanpaden zijn vaag"],
    employee_sentiment="gemengd",
    financial_health="stabiel",
    growth="licht groeiend",
    company_age="18 jaar",
    confidence="medium",
    sources=["https://voorbeeld.example/reviews"],
)

THEMED = [
    ("Wie beslist er als het team het oneens is over een meetmethode?", "role"),
    ("Hoe is het team samengesteld sinds de tweede vestiging openging?", "team"),
    ("Welke publieke opdrachtgever levert de meeste discussie op?", "company"),
    ("Wat moet ik in dit eerste jaar bereiken om door te groeien?", "growth"),
    ("Hoe verdelen jullie het werk als de audits samenvallen?", "ways_of_working"),
    ("Wat is er veranderd sinds de werkdruk rond audits werd gemeld?", "concerns"),
    ("Welke stap in het LIMS-proces kost jullie nu de meeste tijd?", "ways_of_working"),
    ("Hoe ziet een loopbaanpad hier concreet uit, met een voorbeeld?", "growth"),
]


def _question(text: str, theme: str, *, grounded: str = "vacancy") -> dict[str, str]:
    """Build one question payload as the model would return it."""
    return {
        "question": text,
        "theme": theme,
        "why": "Sluit aan op wat Sam eerder deed.",
        "grounded_in": grounded,
    }


def _payload(questions: list[dict[str, str]]) -> str:
    """Serialise a model response."""
    return json.dumps({"questions": questions}, ensure_ascii=False)


def _good() -> str:
    """Return a well-formed set spread across the themes."""
    return _payload([_question(text, theme) for text, theme in THEMED])


def fake() -> FakeLLMClient:
    """Return a client that answers with the well-formed set."""
    return FakeLLMClient([_good()])


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Database:
    """Create one user with a Dutch and an English CV, and an empty database."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.yaml")
    config.write_global_config({"llm_provider": "local"})
    config.save_user_config(USER, {})
    store = ProfileStore(config.user_cv_dir(USER))
    private = [
        ContactSection(
            title="Contact",
            items=[ContactItem(value=EMAIL), ContactItem(value=PHONE)],
        ),
        DetailsSection(
            title="Persoonlijk",
            items=[
                DetailItem(label="Geboortedatum", value=BIRTHDAY),
                DetailItem(label="Woonplaats", value="Zwolle"),
            ],
        ),
    ]
    store.save(
        "nederlands",
        CVDocument(
            full_name="Sam de Vries",
            language="NL",
            sidebar=list(private),
            main=[
                TextSection(
                    title="Ervaring",
                    body=f"Sam werkt sinds 2021 bij {NL_EMPLOYER} aan meetmethoden.",
                )
            ],
        ),
    )
    store.save(
        "default",
        CVDocument(
            full_name="Sam de Vries",
            language="EN",
            sidebar=list(private),
            main=[
                TextSection(
                    title="Experience",
                    body=f"Sam has worked at {EN_EMPLOYER} on calibration since 2021.",
                )
            ],
        ),
    )
    return Database(config.user_db_path(USER))


def _add_job(
    db: Database,
    *,
    slug: str,
    description: str | None,
    company: str = COMPANY,
) -> int:
    """Save one vacancy and return its id."""
    return db.save_job(
        JobListing(
            title="Adviseur meetmethoden",
            company=company,
            location="Deventer",
            url=f"https://voorbeeld.example/vacature/{slug}",
            source="test",
            description=description,
        )
    )


@pytest.fixture
def dutch_job(db: Database) -> int:
    """A Dutch vacancy with both company research and a company review."""
    job_id = _add_job(db, slug="nl", description=DUTCH_DESCRIPTION)
    db.save_company_research(job_id, RESEARCH.model_dump_json())
    db.save_company_review(COMPANY, REVIEW.model_dump_json())
    return job_id


def test_happy_path_spreads_across_themes(db: Database, dutch_job: int) -> None:
    """A full set comes back grounded, in Dutch, with nothing reported missing."""
    client = fake()
    moment = datetime(2026, 9, 17, 9, 0, tzinfo=UTC)
    result = generate_interview_questions(
        USER, dutch_job, client, now=moment, notes="Ik wil het team leren kennen."
    )
    assert result.job_id == dutch_job
    assert result.company == COMPANY
    assert result.language is LetterLanguage.NL
    assert result.generated_at == moment
    assert result.missing_context == []
    assert len(result.questions) == len(THEMED)
    assert {q.theme for q in result.questions} == {
        QuestionTheme.ROLE,
        QuestionTheme.TEAM,
        QuestionTheme.COMPANY,
        QuestionTheme.GROWTH,
        QuestionTheme.WAYS_OF_WORKING,
        QuestionTheme.CONCERNS,
    }
    assert all(q.grounded_in for q in result.questions)
    assert len(client.calls) == 1
    assert client.calls[0][1] == "behavioral_questions"


def test_fenced_json_with_prose_is_accepted(db: Database, dutch_job: int) -> None:
    """A chatty model that fences its JSON still produces a usable set."""
    client = FakeLLMClient([f"Hier zijn de vragen:\n```json\n{_good()}\n```\nSucces!"])
    result = generate_interview_questions(USER, dutch_job, client)
    assert len(result.questions) == len(THEMED)


def test_language_is_detected_and_can_be_overridden(db: Database) -> None:
    """Detection follows the vacancy; an explicit language wins over it."""
    dutch = _add_job(db, slug="nl2", description=DUTCH_DESCRIPTION)
    english = _add_job(db, slug="en", description=ENGLISH_DESCRIPTION)
    assert (
        generate_interview_questions(USER, dutch, fake()).language is LetterLanguage.NL
    )
    assert (
        generate_interview_questions(USER, english, fake()).language
        is LetterLanguage.EN
    )
    client = fake()
    forced = generate_interview_questions(
        USER, dutch, client, language=LetterLanguage.EN
    )
    assert forced.language is LetterLanguage.EN
    prompt = client.calls[0][0]
    assert EN_EMPLOYER in prompt
    assert NL_EMPLOYER not in prompt
    assert "plain professional English" in prompt


def test_untitled_description_falls_back_to_the_title(db: Database) -> None:
    """A vacancy without body text still gets a language, from its title."""
    job_id = _add_job(db, slug="bare", description=None)
    result = generate_interview_questions(USER, job_id, fake())
    assert result.language is LetterLanguage.NL


def test_missing_context_names_absent_sources(db: Database) -> None:
    """Absent grounding is reported rather than quietly filled in by the model."""
    bare = _add_job(db, slug="bare2", description=None, company="Kwadrant Meetlab")
    client = fake()
    result = generate_interview_questions(USER, bare, client)
    assert result.missing_context == [
        "no vacancy description",
        "no company research yet",
        "no company review yet",
    ]
    prompt = client.calls[0][0]
    assert "These sources are absent" in prompt
    assert "Do not guess what they would have said" in prompt
    partial = _add_job(db, slug="partial", description=DUTCH_DESCRIPTION)
    db.save_company_research(partial, RESEARCH.model_dump_json())
    assert generate_interview_questions(USER, partial, fake()).missing_context == [
        "no company review yet"
    ]


def test_prompt_carries_the_real_grounding(db: Database, dutch_job: int) -> None:
    """Cons, culture indicators and CV facts are what make a question specific."""
    client = fake()
    generate_interview_questions(USER, dutch_job, client)
    prompt = client.calls[0][0]
    assert CON in prompt
    assert CULTURE in prompt
    assert "Tweede vestiging geopend in 2025" in prompt
    assert NL_EMPLOYER in prompt
    assert "meetmethoden bewaakt" in prompt
    assert "natural Dutch" in prompt


def test_prompt_states_the_rules_that_make_this_worth_having(
    db: Database, dutch_job: int
) -> None:
    """The anti-generic ban and the no-money default are explicit in the prompt."""
    client = fake()
    generate_interview_questions(USER, dutch_job, client)
    prompt = client.calls[0][0]
    assert "answerable ONLY by a person who works at this company" in prompt
    assert "what does a typical day look like" in prompt
    assert "what is the culture like" in prompt
    assert "where do you see the company in five years" in prompt
    assert "Do not ask about salary, holiday or benefits" in prompt
    assert "Invent nothing about the company" in prompt
    assert "grounded_in" in prompt


def test_notes_can_open_the_door_to_pay_questions(db: Database, dutch_job: int) -> None:
    """Money is off the list until the applicant's own notes ask for it."""
    client = fake()
    generate_interview_questions(
        USER, dutch_job, client, notes="Vraag ook naar het salaris en de vakantiedagen."
    )
    prompt = client.calls[0][0]
    assert "up to two" in prompt
    assert "Do not ask about salary, holiday or benefits" not in prompt


def test_no_contact_or_personal_details_reach_the_prompt(
    db: Database, dutch_job: int
) -> None:
    """The CV's factual sections go to the model; the private ones never do."""
    client = fake()
    generate_interview_questions(USER, dutch_job, client)
    prompt = client.calls[0][0]
    assert EMAIL not in prompt
    assert PHONE not in prompt
    assert BIRTHDAY not in prompt
    assert "Geboortedatum" not in prompt
    assert "Zwolle" not in prompt
    assert MANAGER not in prompt


def test_missing_job_user_and_cv_all_raise(db: Database, tmp_path: Path) -> None:
    """Nothing to ground the questions in is an error, never an empty set."""
    with pytest.raises(InterviewQuestionError):
        generate_interview_questions(USER, 9999, fake())
    with pytest.raises(InterviewQuestionError):
        generate_interview_questions("Spookgebruiker", 1, fake())
    with pytest.raises(InterviewQuestionError):
        generate_interview_questions("../Sam", 1, fake())
    config.save_user_config("Robin", {})
    other = Database(config.user_db_path("Robin"))
    job_id = _add_job(other, slug="robin", description=DUTCH_DESCRIPTION)
    with pytest.raises(InterviewQuestionError, match="No usable CV"):
        generate_interview_questions("Robin", job_id, fake())


@pytest.mark.parametrize(
    "response",
    [
        "Sorry, ik kan dit niet.",
        '{"questions": [',
        '{"questions": []}',
        '{"questions": [{"question": "Wat betaalt het?", "theme": "salary", '
        '"why": "x", "grounded_in": "vacancy"}]}',
        '{"questions": [{"question": "Hoe gaat het?", "theme": "role"}]}',
        '{"questions": [{"question": "   ", "theme": "role", "why": "x", '
        '"grounded_in": "vacancy"}]}',
    ],
)
def test_unusable_responses_raise(db: Database, dutch_job: int, response: str) -> None:
    """Malformed, empty, mistyped and blank answers are all refused."""
    with pytest.raises(InterviewQuestionError):
        generate_interview_questions(USER, dutch_job, FakeLLMClient([response]))


def test_duplicate_questions_collapse(db: Database, dutch_job: int) -> None:
    """Near-identical phrasings are one question, not three."""
    text = "Hoe verdelen jullie het werk rond de audits?"
    client = FakeLLMClient(
        [
            _payload(
                [
                    _question(text, "ways_of_working"),
                    _question(text.upper(), "concerns"),
                    _question(f"  {text.replace('?', '!')}  ", "team"),
                    _question("Wie beslist over een nieuwe meetmethode?", "role"),
                ]
            )
        ]
    )
    result = generate_interview_questions(USER, dutch_job, client)
    assert [q.question for q in result.questions] == [
        text,
        "Wie beslist over een nieuwe meetmethode?",
    ]


def test_a_question_citing_a_source_that_was_absent_is_dropped() -> None:
    """A citation the prompt could not support must not reach the screen.

    The dashboard prints grounded_in under a banner naming the missing sources.
    A question claiming the company review while the banner says there is none
    puts two contradictory statements on one screen.
    """
    from job_scout.interview_questions import _drop_unsupported

    kept = _drop_unsupported(
        [
            InterviewQuestion(
                question="Invented?",
                theme=QuestionTheme.CONCERNS,
                why="...",
                grounded_in="company review: cons",
            ),
            InterviewQuestion(
                question="Real?",
                theme=QuestionTheme.ROLE,
                why="...",
                grounded_in="vacancy",
            ),
        ],
        ["no company review yet"],
    )

    assert [q.question for q in kept] == ["Real?"]


def test_citations_survive_when_every_source_was_present() -> None:
    """With nothing missing, nothing may be discarded."""
    from job_scout.interview_questions import _drop_unsupported

    questions = [
        InterviewQuestion(
            question="Kept",
            theme=QuestionTheme.TEAM,
            why="...",
            grounded_in="company review: cons",
        )
    ]

    assert _drop_unsupported(questions, []) == questions


def test_fewer_questions_are_asked_for_when_there_is_less_to_go_on() -> None:
    """A fixed target is what turns thin grounding into padded questions."""
    from job_scout.interview_questions import _budget

    assert _budget({}) == (4, 6, "you have only the vacancy and the CV")
    assert _budget({"company_review": {}})[:2] == (6, 9)
    assert _budget({"company_research": {}, "company_review": {}})[:2] == (8, 12)


def test_the_concerns_theme_is_not_forced_without_a_review() -> None:
    """With no reported weakness there is nothing real to probe."""
    from job_scout.interview_questions import _budget_rule

    assert "do not force the" in _budget_rule({})
    assert "do not force the" not in _budget_rule(
        {"company_research": {}, "company_review": {}}
    )
