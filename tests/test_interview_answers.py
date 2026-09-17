"""Contracts for the questions the interviewer asks, and the drafted answers.

All people and employers here are invented. The repository is public, so no
fixture may carry a real name, address or company.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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
from job_scout.interview_answers import (
    AnswerFooting,
    InterviewAnswerError,
    LikelyQuestion,
    QuestionKind,
    generate_interview_answers,
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

STORY_SITUATION = "De kalibratie van twee meetbanken liep elk kwartaal uiteen."
STORY_ACTION = "Ik heb de meetreeksen naast elkaar gelegd en de drift uitgezocht."

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

ASKED = [
    ("Waarom wil je weg bij je huidige werkgever?", "motivation"),
    ("Wat heb je zelf aan meetmethoden verbeterd?", "experience"),
    ("Hoe zou je een afwijking in een meetreeks onderzoeken?", "technical"),
    ("Vertel eens over een keer dat een audit misliep.", "behavioural"),
    ("Je hebt niet met LIMS gewerkt. Hoe zie je dat?", "gap"),
    ("Wanneer zou je kunnen beginnen?", "practical"),
]


def _item(
    text: str,
    kind: str,
    *,
    footing: str = "strong",
    based_on: list[str] | None = None,
    answer: str = "Dat heb ik bij mijn huidige werkgever gedaan.",
) -> dict[str, Any]:
    """Build one question-with-answer payload as the model would return it."""
    return {
        "question": text,
        "kind": kind,
        "why_asked": "De vacature noemt dit met zoveel woorden.",
        "draft_answer": answer,
        "based_on": ["CV: Ervaring"] if based_on is None else based_on,
        "footing": footing,
    }


def _payload(questions: list[dict[str, Any]]) -> str:
    """Serialise a model response."""
    return json.dumps({"questions": questions}, ensure_ascii=False)


def _good() -> str:
    """Return a well-formed set spread across the kinds."""
    return _payload([_item(text, kind) for text, kind in ASKED])


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


def _add_story(db: Database) -> int:
    """Save one STAR story the applicant wrote, and return its id."""
    return db.save_star_story(
        STORY_SITUATION,
        "Ik moest voor de audit uitleggen welke bank klopte.",
        STORY_ACTION,
        "De banken liggen sinds die ronde binnen de tolerantie.",
        ["kalibratie", "audit"],
    )


@pytest.fixture
def dutch_job(db: Database) -> int:
    """A Dutch vacancy with company research, a review and a STAR story."""
    job_id = _add_job(db, slug="nl", description=DUTCH_DESCRIPTION)
    db.save_company_research(job_id, RESEARCH.model_dump_json())
    db.save_company_review(COMPANY, REVIEW.model_dump_json())
    _add_story(db)
    return job_id


def test_happy_path_spreads_across_kinds(db: Database, dutch_job: int) -> None:
    """A full set comes back answered, in Dutch, with nothing reported missing."""
    client = fake()
    moment = datetime(2026, 9, 17, 9, 0, tzinfo=UTC)
    result = generate_interview_answers(
        USER, dutch_job, client, now=moment, notes="Ik wil dichter bij huis werken."
    )
    assert result.job_id == dutch_job
    assert result.company == COMPANY
    assert result.language is LetterLanguage.NL
    assert result.generated_at == moment
    assert result.missing_context == []
    assert len(result.questions) == len(ASKED)
    assert {q.kind for q in result.questions} == {
        QuestionKind.MOTIVATION,
        QuestionKind.EXPERIENCE,
        QuestionKind.TECHNICAL,
        QuestionKind.BEHAVIOURAL,
        QuestionKind.GAP,
        QuestionKind.PRACTICAL,
    }
    assert all(q.draft_answer for q in result.questions)
    assert all(q.why_asked for q in result.questions)
    assert len(client.calls) == 1
    assert client.calls[0][1] == "behavioral_questions"


def test_fenced_json_with_prose_is_accepted(db: Database, dutch_job: int) -> None:
    """A chatty model that fences its JSON still produces a usable set."""
    client = FakeLLMClient([f"Hier is je voorbereiding:\n```json\n{_good()}\n```\n!"])
    result = generate_interview_answers(USER, dutch_job, client)
    assert len(result.questions) == len(ASKED)


def test_language_is_detected_and_can_be_overridden(db: Database) -> None:
    """Detection follows the vacancy; an explicit language wins over it."""
    dutch = _add_job(db, slug="nl2", description=DUTCH_DESCRIPTION)
    english = _add_job(db, slug="en", description=ENGLISH_DESCRIPTION)
    assert generate_interview_answers(USER, dutch, fake()).language is LetterLanguage.NL
    assert (
        generate_interview_answers(USER, english, fake()).language is LetterLanguage.EN
    )
    client = fake()
    forced = generate_interview_answers(USER, dutch, client, language=LetterLanguage.EN)
    assert forced.language is LetterLanguage.EN
    prompt = client.calls[0][0]
    assert EN_EMPLOYER in prompt
    assert NL_EMPLOYER not in prompt
    assert "plain spoken English" in prompt


def test_untitled_description_falls_back_to_the_title(db: Database) -> None:
    """A vacancy without body text still gets a language, from its title."""
    job_id = _add_job(db, slug="bare", description=None)
    assert (
        generate_interview_answers(USER, job_id, fake()).language is LetterLanguage.NL
    )


def test_missing_context_names_absent_sources(db: Database) -> None:
    """An empty story bank is reported too: it decides what answers may claim."""
    bare = _add_job(db, slug="bare2", description=None, company="Kwadrant Meetlab")
    client = fake()
    result = generate_interview_answers(USER, bare, client)
    assert result.missing_context == [
        "no vacancy description",
        "no company research yet",
        "no company review yet",
        "no STAR stories saved yet",
    ]
    prompt = client.calls[0][0]
    assert "These sources are absent" in prompt
    assert "no STAR stories saved yet" in prompt
    assert "Do not guess what they would have said" in prompt
    assert "Do not invent an anecdote to fill the space" in prompt


def test_a_saved_story_removes_it_from_missing_context(db: Database) -> None:
    """Once a story exists the bank is no longer reported as empty."""
    job_id = _add_job(db, slug="withstory", description=DUTCH_DESCRIPTION)
    _add_story(db)
    result = generate_interview_answers(USER, job_id, fake())
    assert "no STAR stories saved yet" not in result.missing_context
    assert result.missing_context == [
        "no company research yet",
        "no company review yet",
    ]


def test_star_stories_reach_the_prompt_and_can_back_an_answer(db: Database) -> None:
    """The story bank is the only anecdote source, so it must arrive intact."""
    job_id = _add_job(db, slug="story", description=DUTCH_DESCRIPTION)
    story_id = _add_story(db)
    label = f"STAR story {story_id}"
    client = FakeLLMClient(
        [
            _payload(
                [
                    _item(
                        "Vertel eens over een keer dat een audit misliep.",
                        "behavioural",
                        based_on=[label],
                        answer=(
                            "Bij een audit liepen twee meetbanken uiteen. Ik heb de "
                            "reeksen naast elkaar gelegd en de drift uitgezocht."
                        ),
                    )
                ]
            )
        ]
    )
    result = generate_interview_answers(USER, job_id, client)
    prompt = client.calls[0][0]
    assert STORY_SITUATION in prompt
    assert STORY_ACTION in prompt
    assert "kalibratie" in prompt
    assert label in prompt
    assert result.questions[0].based_on == [label]
    assert result.questions[0].footing is AnswerFooting.STRONG


def test_prompt_carries_the_real_grounding(db: Database, dutch_job: int) -> None:
    """Cons, culture indicators and CV facts are what make an answer specific."""
    client = fake()
    generate_interview_answers(USER, dutch_job, client)
    prompt = client.calls[0][0]
    assert CON in prompt
    assert CULTURE in prompt
    assert "Tweede vestiging geopend in 2025" in prompt
    assert NL_EMPLOYER in prompt
    assert "meetmethoden bewaakt" in prompt
    assert "natural spoken Dutch" in prompt


def test_prompt_states_the_rules_that_make_the_answers_trustworthy(
    db: Database, dutch_job: int
) -> None:
    """No invention and an honest gap are the two rules the answers rest on."""
    client = fake()
    generate_interview_answers(USER, dutch_job, client)
    prompt = client.calls[0][0]
    assert (
        "Never invent an employer, project, tool, number, qualification or "
        "achievement" in prompt
    )
    assert "the honest answer IS the answer" in prompt
    assert "say so plainly in the first sentence" in prompt
    assert "Do not bluff, do not pad, do not change the subject" in prompt
    assert "60 to 150 words" in prompt
    assert "why the candidate is leaving" in prompt
    assert "a requirement the CV does not meet" in prompt
    assert "motivation, experience, technical, behavioural, gap, practical" in prompt
    assert "strong, partial, gap" in prompt


def test_a_gap_answer_survives_with_its_footing(db: Database, dutch_job: int) -> None:
    """The honest answer is the point of the feature; parsing must not lose it."""
    client = FakeLLMClient(
        [
            _payload(
                [
                    _item(
                        "Je hebt niet met LIMS gewerkt. Hoe zie je dat?",
                        "gap",
                        footing="gap",
                        based_on=[],
                        answer=(
                            "Met LIMS heb ik niet gewerkt, dat klopt. Ik werk wel "
                            "dagelijks met meetreeksen in Python en ik zou me het "
                            "systeem in de eerste maanden eigen maken."
                        ),
                    ),
                    _item(
                        "Wat trekt je aan dit instituut?",
                        "motivation",
                        footing="partial",
                    ),
                ]
            )
        ]
    )
    result = generate_interview_answers(USER, dutch_job, client)
    assert result.questions[0].footing is AnswerFooting.GAP
    assert result.questions[0].kind is QuestionKind.GAP
    assert result.questions[0].based_on == []
    assert "heb ik niet gewerkt" in result.questions[0].draft_answer
    assert result.questions[1].footing is AnswerFooting.PARTIAL


def test_no_contact_or_personal_details_reach_the_prompt(
    db: Database, dutch_job: int
) -> None:
    """The CV's factual sections go to the model; the private ones never do."""
    client = fake()
    generate_interview_answers(USER, dutch_job, client)
    prompt = client.calls[0][0]
    assert EMAIL not in prompt
    assert PHONE not in prompt
    assert BIRTHDAY not in prompt
    assert "Geboortedatum" not in prompt
    assert "Zwolle" not in prompt
    assert MANAGER not in prompt


def test_missing_job_user_and_cv_all_raise(db: Database) -> None:
    """Nothing to ground the answers in is an error, never an empty set."""
    with pytest.raises(InterviewAnswerError):
        generate_interview_answers(USER, 9999, fake())
    with pytest.raises(InterviewAnswerError):
        generate_interview_answers("Spookgebruiker", 1, fake())
    with pytest.raises(InterviewAnswerError):
        generate_interview_answers("../Sam", 1, fake())
    config.save_user_config("Robin", {})
    other = Database(config.user_db_path("Robin"))
    job_id = _add_job(other, slug="robin", description=DUTCH_DESCRIPTION)
    with pytest.raises(InterviewAnswerError, match="No usable CV"):
        generate_interview_answers("Robin", job_id, fake())


@pytest.mark.parametrize(
    "response",
    [
        "Sorry, ik kan dit niet.",
        '{"questions": [',
        '{"questions": []}',
        '{"questions": [{"question": "Waarom wij?", "kind": "salary", '
        '"why_asked": "x", "draft_answer": "y", "footing": "strong"}]}',
        '{"questions": [{"question": "Waarom wij?", "kind": "motivation", '
        '"why_asked": "x", "draft_answer": "y", "footing": "sterk"}]}',
        '{"questions": [{"question": "Waarom wij?", "kind": "motivation"}]}',
        '{"questions": [{"question": "Waarom wij?", "kind": "motivation", '
        '"why_asked": "x", "draft_answer": "   ", "footing": "strong"}]}',
        '{"questions": [{"question": "   ", "kind": "motivation", '
        '"why_asked": "x", "draft_answer": "y", "footing": "strong"}]}',
    ],
)
def test_unusable_responses_raise(db: Database, dutch_job: int, response: str) -> None:
    """Malformed, empty, mistyped and blank answers are all refused."""
    with pytest.raises(InterviewAnswerError):
        generate_interview_answers(USER, dutch_job, FakeLLMClient([response]))


def test_duplicate_questions_collapse(db: Database, dutch_job: int) -> None:
    """One question deserves one answer, not three phrasings of the same one."""
    text = "Waarom wil je weg bij je huidige werkgever?"
    client = FakeLLMClient(
        [
            _payload(
                [
                    _item(text, "motivation"),
                    _item(text.upper(), "gap"),
                    _item(f"  {text.replace('?', '!')}  ", "practical"),
                    _item("Wat heb je zelf aan meetmethoden verbeterd?", "experience"),
                ]
            )
        ]
    )
    result = generate_interview_answers(USER, dutch_job, client)
    assert [q.question for q in result.questions] == [
        text,
        "Wat heb je zelf aan meetmethoden verbeterd?",
    ]


def test_fewer_questions_are_asked_for_when_there_is_less_to_go_on() -> None:
    """A fixed target is what turns thin grounding into invented answers."""
    from job_scout.interview_answers import _budget

    assert _budget({}) == (4, 6, "you have the vacancy and the CV")
    assert _budget({"company_review": {}})[:2] == (6, 9)
    assert _budget({"company_research": {}, "company_review": {}})[:2] == (8, 12)
    full = _budget({"company_research": {}, "company_review": {}, "star_stories": [{}]})
    assert full[:2] == (8, 12)
    assert "the applicant's own STAR stories" in full[2]


def test_an_empty_story_bank_is_stated_in_the_count_rule() -> None:
    """With no stories the model must be told to stay on the CV, not improvise."""
    from job_scout.interview_answers import _budget_rule

    assert "no STAR stories saved" in _budget_rule({})
    assert "Fewer excellent items beat more padded ones" in _budget_rule({})
    assert "no STAR stories saved" not in _budget_rule({"star_stories": [{}]})


def _answer(**values: object) -> LikelyQuestion:
    """Build a question whose fields individual tests override."""
    data: dict[str, object] = dict(
        question="Tell me about a difficult measurement.",
        kind=QuestionKind.BEHAVIOURAL,
        why_asked="The vacancy asks for experimental work.",
        draft_answer="I would start from the setup I built previously.",
        based_on=["STAR story 3"],
        footing=AnswerFooting.STRONG,
    )
    data.update(values)
    return LikelyQuestion.model_validate(data)


def test_a_citation_to_a_story_that_does_not_exist_is_removed() -> None:
    """The dashboard prints based_on as though it were verified, so verify it."""
    from job_scout.interview_answers import _check_citations

    item = _answer(based_on=["STAR story 9"])
    _check_citations([item], [{"label": "STAR story 3"}])

    assert item.based_on == []


def test_a_real_story_citation_survives() -> None:
    """The check must not punish the citation it exists to encourage."""
    from job_scout.interview_answers import _check_citations

    item = _answer(based_on=["STAR story 3"])
    _check_citations([item], [{"label": "STAR story 3"}])

    assert item.based_on == ["STAR story 3"]
    assert item.footing is AnswerFooting.STRONG


def test_a_story_citation_is_checked_even_when_the_bank_has_entries() -> None:
    """The earlier check only ran on an empty bank, which is the rarer case."""
    from job_scout.interview_answers import _check_citations

    item = _answer(based_on=["CV: Laser work", "STAR story 41"])
    _check_citations([item], [{"label": "STAR story 1"}, {"label": "STAR story 2"}])

    assert item.based_on == ["CV: Laser work"]


def test_an_answer_naming_no_source_cannot_claim_strong_footing() -> None:
    """Over-grading hides the answer that most needed rehearsing.

    The amber gap marker is what tells the candidate where to prepare, so a
    claim of strong support with nothing behind it fails in the worst direction.
    """
    from job_scout.interview_answers import _check_citations

    item = _answer(based_on=["STAR story 9"], footing=AnswerFooting.STRONG)
    _check_citations([item], [])

    assert item.based_on == []
    assert item.footing is AnswerFooting.PARTIAL


def test_a_gap_answer_is_left_alone_when_it_names_nothing() -> None:
    """A gap legitimately has no source: that is what makes it a gap."""
    from job_scout.interview_answers import _check_citations

    item = _answer(based_on=[], footing=AnswerFooting.GAP)
    _check_citations([item], [])

    assert item.footing is AnswerFooting.GAP


def test_an_empty_story_bank_steers_the_questions_it_can_answer() -> None:
    """Missing data must not be presented to the candidate as a missing career.

    With no saved anecdotes, a set full of "tell me about a time when" questions
    produces answers that read as weakness when the real problem is that nobody
    has written the stories down yet.
    """
    from job_scout.interview_answers import _NO_STORIES

    assert "at most one behavioural question" in _NO_STORIES
    assert "competence gap" in _NO_STORIES
    assert "Do not invent an anecdote" in _NO_STORIES
