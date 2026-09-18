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
from job_scout import company_research, company_review
from job_scout.company_research import CompanyResearchError
from job_scout.company_review import CompanyReviewError
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
from job_scout.llm.base import CallPurpose, LLMError
from job_scout.models import (
    CompanyResearch,
    CompanyReview,
    Config,
    HiringManagerSuggestion,
    JobListing,
)
from job_scout.websearch import SearchResult
from tests.helpers import FakeLLMClient
from tests.style_checks import GENERATED, PLAIN, assert_plain, assert_styled_prompt

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
    sources=["https://voorbeeld.example/deltameet/over-ons"],
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
    sources=[f"https://voorbeeld.example/reviews/{n}" for n in range(3)],
)

FRESH_GROWTH = "Nieuwe meethal in aanbouw naast het hoofdkantoor"
FRESH_CON = "inwerken gebeurt vooral naast een collega op de werkvloer"
STALE_CON = "niemand weet wie de audits plant"
SEARXNG = "http://searxng.voorbeeld.example"
NO_PUBLIC_INFO = "no public information found about the company"
RESEARCH_FAILED = "company research could not be completed this time"
NO_REVIEW = "no company review yet"
THIN_REVIEW = "company review is based on little evidence"

FOUND = CompanyResearch(
    company_name=COMPANY,
    industry="Meetdiensten",
    growth_signals=FRESH_GROWTH,
    research_notes="Voert metingen uit voor publieke opdrachtgevers.",
    sources=["https://voorbeeld.example/over-ons"],
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


class CompanyLookups:
    """Stands in for web research and the company review, recording each call.

    Both search the web for real, so every test replaces them. They are looked
    up in job_scout.interview_questions, whose helper both generators share.
    """

    def __init__(self) -> None:
        """Start with nothing found and every rewritten review still thin."""
        self.found: CompanyResearch | None = None
        self.research_error: Exception | None = None
        self.confidence = "low"
        self.review_sources = 1
        self.review_found = True
        self.research_calls: list[tuple[JobListing, Config, object]] = []
        self.suggest_managers: list[bool] = []
        self.review_calls: list[tuple[str, object, str | None]] = []

    def research(
        self,
        job: JobListing,
        settings: Config,
        client: object = None,
        *,
        suggest_managers: bool = False,
    ) -> CompanyResearch | None:
        """Record a research request and return what the web "found"."""
        self.research_calls.append((job, settings, client))
        self.suggest_managers.append(suggest_managers)
        if self.research_error is not None:
            raise self.research_error
        return self.found

    def review(
        self,
        company: str,
        *,
        client: object,
        timeout: int = 15,
        searxng_url: str | None = None,
        api_key: str | None = None,
    ) -> CompanyReview | None:
        """Record a review request and return a review as configured."""
        self.review_calls.append((company, client, searxng_url))
        if not self.review_found:
            return None
        return CompanyReview(
            company=company,
            summary="Opnieuw beoordeeld op basis van openbare bronnen.",
            cons=[FRESH_CON],
            confidence=self.confidence,
            sources=[
                f"https://voorbeeld.example/ervaringen/{n}"
                for n in range(self.review_sources)
            ],
        )


@pytest.fixture(autouse=True)
def lookups(monkeypatch: pytest.MonkeyPatch) -> CompanyLookups:
    """Keep every test off the network by stubbing research and review."""
    stub = CompanyLookups()
    monkeypatch.setattr("job_scout.interview_questions.research_company", stub.research)
    monkeypatch.setattr("job_scout.interview_questions.review_company", stub.review)
    return stub


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
        NO_PUBLIC_INFO,
        THIN_REVIEW,
    ]
    prompt = client.calls[0][0]
    assert "These sources are absent" in prompt
    assert "Do not guess what they would have said" in prompt
    partial = _add_job(db, slug="partial", description=DUTCH_DESCRIPTION)
    db.save_company_research(partial, RESEARCH.model_dump_json())
    assert generate_interview_questions(USER, partial, fake()).missing_context == [
        THIN_REVIEW
    ]


def _thin_review() -> CompanyReview:
    """Return a stored review written while web search found nothing."""
    return REVIEW.model_copy(update={"cons": [STALE_CON], "confidence": "low"})


def test_missing_research_is_run_stored_and_used(
    db: Database, lookups: CompanyLookups
) -> None:
    """With no research stored the company is researched now, and it sticks."""
    config.write_global_config({"llm_provider": "local", "searxng_url": SEARXNG})
    job_id = _add_job(db, slug="fresh", description=DUTCH_DESCRIPTION)
    db.save_company_review(COMPANY, REVIEW.model_dump_json())
    lookups.found = FOUND
    client = fake()
    result = generate_interview_questions(USER, job_id, client)
    assert len(lookups.research_calls) == 1
    assert lookups.suggest_managers == [False]
    job, settings, _ = lookups.research_calls[0]
    assert job.company == COMPANY
    assert settings.searxng_url == SEARXNG
    stored = db.get_company_research(job_id)
    assert stored is not None
    assert CompanyResearch.model_validate_json(stored) == FOUND
    assert FRESH_GROWTH in client.calls[0][0]
    assert result.missing_context == []


def test_research_reuses_the_generators_own_client(
    db: Database, lookups: CompanyLookups
) -> None:
    """A second client would probe the model host a second time."""
    job_id = _add_job(db, slug="client", description=DUTCH_DESCRIPTION)
    client = fake()
    generate_interview_questions(USER, job_id, client)
    assert lookups.research_calls[0][2] is client
    assert lookups.review_calls[0][1] is client


def test_stored_research_is_not_run_again(
    db: Database, lookups: CompanyLookups
) -> None:
    """Research already on file is used as it is."""
    job_id = _add_job(db, slug="stored", description=DUTCH_DESCRIPTION)
    db.save_company_research(job_id, RESEARCH.model_dump_json())
    generate_interview_questions(USER, job_id, fake())
    assert lookups.research_calls == []


def test_research_that_finds_nothing_says_so(
    db: Database, lookups: CompanyLookups
) -> None:
    """Searched and found nothing must not read like never searched."""
    job_id = _add_job(db, slug="unknown", description=DUTCH_DESCRIPTION)
    db.save_company_review(COMPANY, REVIEW.model_dump_json())
    client = fake()
    result = generate_interview_questions(USER, job_id, client)
    assert len(lookups.research_calls) == 1
    assert result.missing_context == [NO_PUBLIC_INFO]
    assert "no company research yet" not in result.missing_context
    assert db.get_company_research(job_id) is None
    assert '"company_research"' not in client.calls[0][0]


def test_a_low_confidence_review_is_refreshed_exactly_once(
    db: Database, lookups: CompanyLookups
) -> None:
    """Cons from a review written on no evidence must not reach the questions."""
    job_id = _add_job(db, slug="thin", description=DUTCH_DESCRIPTION)
    db.save_company_research(job_id, RESEARCH.model_dump_json())
    db.save_company_review(COMPANY, _thin_review().model_dump_json())
    lookups.confidence = "medium"
    lookups.review_sources = 3
    client = fake()
    result = generate_interview_questions(USER, job_id, client)
    assert [call[0] for call in lookups.review_calls] == [COMPANY]
    prompt = client.calls[0][0]
    assert FRESH_CON in prompt
    assert STALE_CON not in prompt
    stored = db.get_company_review(COMPANY)
    assert stored is not None
    assert CompanyReview.model_validate_json(stored).confidence == "medium"
    assert result.missing_context == []


def test_a_review_still_thin_after_refresh_is_used_and_flagged(
    db: Database, lookups: CompanyLookups
) -> None:
    """One refresh per generation, never a loop, and the weakness is stated."""
    job_id = _add_job(db, slug="thin2", description=DUTCH_DESCRIPTION)
    db.save_company_research(job_id, RESEARCH.model_dump_json())
    db.save_company_review(COMPANY, _thin_review().model_dump_json())
    client = fake()
    result = generate_interview_questions(USER, job_id, client)
    assert len(lookups.review_calls) == 1
    assert FRESH_CON in client.calls[0][0]
    assert result.missing_context == [THIN_REVIEW]


def test_a_sound_review_is_not_refreshed(
    db: Database, dutch_job: int, lookups: CompanyLookups
) -> None:
    """With research and a confident review on file, nothing is looked up."""
    generate_interview_questions(USER, dutch_job, fake())
    assert lookups.review_calls == []
    assert lookups.research_calls == []


def test_a_failed_refresh_falls_back_to_what_is_stored(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A review that cannot be rewritten leaves the stored one, or none, in use."""

    def broken(company: str, **_: object) -> CompanyReview:
        raise CompanyReviewError("The review was not a JSON object")

    monkeypatch.setattr("job_scout.interview_questions.review_company", broken)
    thin = _add_job(db, slug="thin3", description=DUTCH_DESCRIPTION)
    db.save_company_research(thin, RESEARCH.model_dump_json())
    db.save_company_review(COMPANY, _thin_review().model_dump_json())
    result = generate_interview_questions(USER, thin, fake())
    assert result.missing_context == [THIN_REVIEW]
    other = "Kwadrant Meetlab"
    bare = _add_job(db, slug="none", description=DUTCH_DESCRIPTION, company=other)
    db.save_company_research(bare, RESEARCH.model_dump_json())
    result = generate_interview_questions(USER, bare, fake())
    assert result.missing_context == ["no company review yet"]


def _review_evidence(monkeypatch: pytest.MonkeyPatch, count: int) -> None:
    """Put the real review_company back, with its web search stubbed.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        count: How many distinct snippets, each from its own URL, the web holds.
    """
    snippets = [f"{COMPANY} ervaringen {n}: {FRESH_CON}" for n in range(count)]
    sources = [f"https://voorbeeld.example/ervaringen/{n}" for n in range(count)]
    monkeypatch.setattr(
        "job_scout.interview_questions.review_company", company_review.review_company
    )
    monkeypatch.setattr(
        company_review,
        "gather_company_evidence",
        lambda company, **_: (list(snippets), list(sources)),
    )


def _stored_review(db: Database, company: str = COMPANY) -> CompanyReview | None:
    """Read back the review the generation left in the cache."""
    raw = db.get_company_review(company)
    return CompanyReview.model_validate_json(raw) if raw else None


def _job_with_research(db: Database, slug: str) -> int:
    """Save a vacancy whose company research is already on file."""
    job_id = _add_job(db, slug=slug, description=DUTCH_DESCRIPTION)
    db.save_company_research(job_id, RESEARCH.model_dump_json())
    return job_id


# The model claims high confidence; two sources cannot earn that.
_SELF_ASSURED = json.dumps(
    {
        "work_score": 71,
        "summary": "Prima werkgever.",
        "cons": [FRESH_CON],
        "confidence": "high",
    }
)


class EvaluationDown:
    """A client whose ``evaluation`` host cannot be reached.

    Question writing goes to the default host and works, which is exactly the
    split the purpose routing allows.
    """

    def __init__(self) -> None:
        """Start with no calls recorded."""
        self.calls: list[tuple[str, CallPurpose]] = []

    def complete(
        self, prompt: str, *, purpose: CallPurpose, timeout: float | None = None
    ) -> str:
        """Fail every evaluation call; answer everything else."""
        self.calls.append((prompt, purpose))
        if purpose == "evaluation":
            raise LLMError("evaluation host unreachable")
        return _good()

    def check_available(self) -> tuple[bool, str | None]:
        """Report as available, as a probe would before the first call."""
        return True, None


def test_a_review_without_sources_is_thin_whatever_it_says_about_itself(
    db: Database, lookups: CompanyLookups
) -> None:
    """A review from no evidence used to rate itself medium and pass as sound."""
    job_id = _job_with_research(db, "unsourced")
    unsourced = REVIEW.model_copy(update={"cons": [STALE_CON], "sources": []})
    db.save_company_review(COMPANY, unsourced.model_dump_json())
    lookups.confidence = "medium"
    lookups.review_sources = 0
    result = generate_interview_questions(USER, job_id, fake())
    assert len(lookups.review_calls) == 1
    assert result.missing_context == [THIN_REVIEW]
    lookups.review_found = False
    db.save_company_review(COMPANY, unsourced.model_dump_json())
    result = generate_interview_questions(USER, job_id, fake())
    assert result.missing_context == [THIN_REVIEW]


def test_a_refresh_with_no_web_evidence_asks_nothing_and_stores_nothing(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no snippet to summarise, the model would only have its memory."""
    _review_evidence(monkeypatch, 0)
    job_id = _job_with_research(db, "noweb")
    db.save_company_review(COMPANY, _thin_review().model_dump_json())
    client = fake()
    result = generate_interview_questions(USER, job_id, client)
    assert [purpose for _, purpose in client.calls] == ["behavioral_questions"]
    assert _stored_review(db) == _thin_review()
    assert result.missing_context == [THIN_REVIEW]
    other = "Kwadrant Meetlab"
    bare = _add_job(db, slug="noweb2", description=DUTCH_DESCRIPTION, company=other)
    db.save_company_research(bare, RESEARCH.model_dump_json())
    result = generate_interview_questions(USER, bare, fake())
    assert _stored_review(db, other) is None
    assert result.missing_context == [NO_REVIEW]


def test_a_refreshed_review_is_graded_on_its_sources_not_its_own_word(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two web sources make a thin review, whatever confidence the model claims."""
    _review_evidence(monkeypatch, 2)
    job_id = _job_with_research(db, "graded")
    client = FakeLLMClient([_SELF_ASSURED, _good()])
    result = generate_interview_questions(USER, job_id, client)
    assert [purpose for _, purpose in client.calls] == [
        "evaluation",
        "behavioral_questions",
    ]
    stored = _stored_review(db)
    assert stored is not None
    assert stored.confidence == "low"
    assert len(stored.sources) == 2
    assert result.missing_context == [THIN_REVIEW]


@pytest.mark.parametrize("answer", ["Sorry, dat weet ik niet.", '["geen", "object"]'])
def test_a_failed_refresh_never_overwrites_the_stored_review(
    db: Database, monkeypatch: pytest.MonkeyPatch, answer: str
) -> None:
    """A placeholder review saved over the real one would erase what was known."""
    _review_evidence(monkeypatch, 3)
    job_id = _job_with_research(db, "failed")
    db.save_company_review(COMPANY, _thin_review().model_dump_json())
    client = FakeLLMClient([answer, _good()])
    result = generate_interview_questions(USER, job_id, client)
    assert _stored_review(db) == _thin_review()
    assert STALE_CON in client.calls[1][0]
    assert result.missing_context == [THIN_REVIEW]


def test_an_unreachable_model_during_refresh_keeps_the_stored_review(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A provider failure is not a reason to lose the review on file."""
    _review_evidence(monkeypatch, 3)
    job_id = _job_with_research(db, "down")
    db.save_company_review(COMPANY, _thin_review().model_dump_json())
    result = generate_interview_questions(USER, job_id, EvaluationDown())
    assert _stored_review(db) == _thin_review()
    assert result.missing_context == [THIN_REVIEW]


@pytest.mark.parametrize(
    ("error", "review_refreshed"),
    [
        (CompanyResearchError("The research answer was not JSON"), True),
        (LLMError("evaluation host unreachable"), False),
    ],
)
def test_a_failed_lookup_is_not_reported_as_nothing_found(
    db: Database, lookups: CompanyLookups, error: Exception, review_refreshed: bool
) -> None:
    """The web may hold plenty; the lookup failing says nothing about that."""
    job_id = _add_job(db, slug="lookup", description=DUTCH_DESCRIPTION)
    db.save_company_review(COMPANY, _thin_review().model_dump_json())
    lookups.research_error = error
    client = fake()
    result = generate_interview_questions(USER, job_id, client)
    assert result.missing_context == [RESEARCH_FAILED, THIN_REVIEW]
    assert NO_PUBLIC_INFO not in result.missing_context
    assert db.get_company_research(job_id) is None
    assert '"company_research"' not in client.calls[0][0]
    assert bool(lookups.review_calls) is review_refreshed


def test_an_unreachable_lookup_host_is_asked_only_once(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Research failing to reach the model means the review is not tried too."""
    page = SearchResult(
        url="https://deltameet.example/over-ons",
        title=f"Over {COMPANY}",
        snippet="Meetdiensten voor publieke opdrachtgevers.",
    )
    monkeypatch.setattr(
        "job_scout.interview_questions.research_company",
        company_research.research_company,
    )
    monkeypatch.setattr(company_research, "web_search", lambda query, **_: [page])
    _review_evidence(monkeypatch, 3)
    job_id = _add_job(db, slug="unreachable", description=DUTCH_DESCRIPTION)
    client = EvaluationDown()
    result = generate_interview_questions(USER, job_id, client)
    assert [purpose for _, purpose in client.calls] == [
        "evaluation",
        "behavioral_questions",
    ]
    assert result.missing_context == [RESEARCH_FAILED, NO_REVIEW]
    assert db.get_company_research(job_id) is None
    assert _stored_review(db) is None


def test_a_question_citing_failed_research_is_dropped() -> None:
    """A failed lookup is as absent from the prompt as one that found nothing."""
    from job_scout.interview_questions import _drop_unsupported

    cited = InterviewQuestion(
        question="Invented?",
        theme=QuestionTheme.COMPANY,
        why="...",
        grounded_in="company research: growth",
    )
    assert _drop_unsupported([cited], [RESEARCH_FAILED]) == []


def test_stored_research_without_sources_is_researched_again(
    db: Database, lookups: CompanyLookups
) -> None:
    """Research that cites nothing was written from model memory."""
    job_id = _add_job(db, slug="memory", description=DUTCH_DESCRIPTION)
    remembered = RESEARCH.model_copy(
        update={"sources": [], "growth_signals": STALE_CON}
    )
    db.save_company_research(job_id, remembered.model_dump_json())
    db.save_company_review(COMPANY, REVIEW.model_dump_json())
    lookups.found = FOUND
    client = fake()
    result = generate_interview_questions(USER, job_id, client)
    assert len(lookups.research_calls) == 1
    assert STALE_CON not in client.calls[0][0]
    assert FRESH_GROWTH in client.calls[0][0]
    stored = db.get_company_research(job_id)
    assert stored is not None
    assert CompanyResearch.model_validate_json(stored) == FOUND
    assert result.missing_context == []


def test_unsourced_research_is_not_used_when_nothing_better_is_found(
    db: Database, lookups: CompanyLookups
) -> None:
    """Finding nothing new does not bring the remembered research back."""
    job_id = _add_job(db, slug="memory2", description=DUTCH_DESCRIPTION)
    remembered = RESEARCH.model_copy(
        update={"sources": [], "growth_signals": STALE_CON}
    )
    db.save_company_research(job_id, remembered.model_dump_json())
    db.save_company_review(COMPANY, REVIEW.model_dump_json())
    client = fake()
    result = generate_interview_questions(USER, job_id, client)
    assert STALE_CON not in client.calls[0][0]
    assert '"company_research"' not in client.calls[0][0]
    assert result.missing_context == [NO_PUBLIC_INFO]


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


def test_every_field_the_user_reads_comes_back_plain(
    db: Database, dutch_job: int
) -> None:
    """The question, why and source label lose the marks of generated text."""
    first = _question(GENERATED, "role", grounded="company review \u2014 cons")
    first["why"] = GENERATED
    others = [_question(text, theme) for text, theme in THEMED[1:]]
    client = FakeLLMClient([_payload([first, *others])])

    result = generate_interview_questions(USER, dutch_job, client)

    question = result.questions[0]
    assert (question.question, question.why) == (PLAIN, PLAIN)
    assert question.grounded_in == "company review, cons"
    for item in result.questions:
        assert_plain(item.question + item.why + item.grounded_in)
    assert_styled_prompt(client.calls[0][0])


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
    with pytest.raises(InterviewQuestionError, match="No CV information"):
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


def test_research_that_found_nothing_cannot_be_cited() -> None:
    """Research the web had nothing for is as absent as research never run."""
    from job_scout.interview_questions import _drop_unsupported

    kept = _drop_unsupported(
        [
            InterviewQuestion(
                question="Invented?",
                theme=QuestionTheme.COMPANY,
                why="...",
                grounded_in="company research: growth",
            ),
            InterviewQuestion(
                question="Real?",
                theme=QuestionTheme.ROLE,
                why="...",
                grounded_in="vacancy",
            ),
        ],
        [NO_PUBLIC_INFO],
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


@pytest.mark.parametrize(
    ("notes", "allowed"),
    [
        ("wat ga ik verdienen?", True),
        ("graag iets over de beloning", True),
        ("arbeidsvoorwaarden graag", True),
        ("vraag naar inschaling", True),
        ("what is the pay", True),
        ("I used paypal at my last job", False),
        ("schaalbaarheid van het systeem", False),
        ("nothing about money", False),
    ],
)
def test_pay_questions_follow_what_the_applicant_actually_wrote(
    notes: str, allowed: bool
) -> None:
    """Substring matching read these wrongly, and did so invisibly.

    "pay" sat inside paypal and "schaal" inside schaalbaarheid, while the
    phrasings a Dutch applicant actually types -- verdienen, beloning -- matched
    nothing at all, so asking for pay questions silently produced none.
    """
    from job_scout.interview_questions import _pay_allowed

    assert _pay_allowed(notes) is allowed
