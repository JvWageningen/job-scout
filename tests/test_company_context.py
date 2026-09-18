"""Company research and the company review are looked up once, then reused.

These tests run the real research and review code with web search replaced, so
every query and every model call is counted. All people, employers and URLs
are invented; URLs use the reserved ``.example`` domain. No test touches the
network.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any

import pytest

import job_scout.config as config
from job_scout import company_research, company_review
from job_scout.company_lookups import (
    FAILED_COOLDOWN,
    RESEARCH_COOLDOWN,
    REVIEW_COOLDOWN,
    LookupKind,
    LookupOutcome,
    recall,
)
from job_scout.cv.models import CVDocument, TextSection
from job_scout.cv.storage import ProfileStore
from job_scout.database import Database
from job_scout.interview_answers import generate_interview_answers
from job_scout.interview_questions import (
    NO_PUBLIC_INFO,
    NO_REVIEW,
    RESEARCH_FAILED,
    THIN_REVIEW,
    CompanyContext,
    InterviewQuestion,
    QuestionTheme,
    _drop_unsupported,
    company_context,
    gap_kind,
    generate_interview_questions,
)
from job_scout.models import CompanyResearch, CompanyReview, JobListing
from job_scout.websearch import SearchResult
from job_scout.writing_style import HOUSE_STYLE
from tests.helpers import FakeLLMClient

USER = "Noor"
COMPANY = "Kwadrant Meetlab"
# Midday UTC is 18 September in every time zone the tests could run in.
NOW = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)
CHECKED = "18 September 2026"
DESCRIPTION = (
    "Wij zoeken een kalibratiespecialist die onze meetapparatuur op orde houdt. "
    "Je werkt in een klein team en je bereidt de jaarlijkse audits voor."
)
NOTES = "Kalibreert meetapparatuur voor laboratoria"
PAGES = [
    SearchResult(
        url="https://kwadrant-meetlab.example/over-ons",
        title="Over Kwadrant Meetlab",
        snippet="Kwadrant Meetlab kalibreert meetapparatuur voor laboratoria.",
    ),
    SearchResult(
        url="https://werkgevers.example/kwadrant-meetlab",
        title="Werken bij Kwadrant Meetlab",
        snippet="Medewerkers van Kwadrant Meetlab noemen de werkdruk rond audits hoog.",
    ),
]
RESEARCH_ANSWER = json.dumps(
    {
        "industry": "Kalibratie",
        "company_size": None,
        "culture_indicators": ["korte lijnen \u2014 weinig lagen"],
        "tech_stack_hints": [],
        "growth_signals": None,
        "research_notes": f"{NOTES} \u2014 vooral in de regio.",
    }
)
REVIEW_ANSWER = json.dumps(
    {
        "work_score": None,
        "summary": "Behulpzame collega's \u2013 de werkdruk rond audits is hoog.",
        "pros": ["**behulpzame collega's**"],
        "cons": ["werkdruk rond audits"],
        "employee_sentiment": None,
        "financial_health": None,
        "growth": None,
        "company_age": None,
    }
)
QUESTIONS = json.dumps(
    {
        "questions": [
            {
                "question": text,
                "theme": theme,
                "why": "Sluit aan op wat Noor eerder deed.",
                "grounded_in": "vacancy",
            }
            for text, theme in [
                ("Wie plant de audits en hoe ver vooruit?", "ways_of_working"),
                ("Hoe groot is het team dat de kalibraties doet?", "team"),
                ("Wat moet ik na een jaar bereikt hebben?", "growth"),
                ("Welke meting levert nu de meeste discussie op?", "role"),
            ]
        ]
    }
)
ANSWERS = json.dumps(
    {
        "questions": [
            {
                "question": "Wat heb je aan kalibratie verbeterd?",
                "kind": "experience",
                "why_asked": "De vacature vraagt erom.",
                "draft_answer": "Ik heb het schema voor weegschalen opnieuw opgezet.",
                "based_on": ["CV: Ervaring"],
                "footing": "strong",
            }
        ]
    }
)


class Web:
    """Stands in for web search in both lookups and records every query."""

    def __init__(self) -> None:
        """Start with the two pages that name the company."""
        self.results: list[SearchResult] = list(PAGES)
        self.queries: list[str] = []

    def __call__(self, query: str, **_: Any) -> list[SearchResult]:
        """Record the query and return the current results."""
        self.queries.append(query)
        return list(self.results)

    def research_queries(self) -> list[str]:
        """Return the queries research sent, as opposed to the review."""
        asked = set(company_research._research_queries(COMPANY))
        return [query for query in self.queries if query in asked]

    def review_queries(self) -> list[str]:
        """Return the queries the review sent."""
        asked = set(company_review._evidence_queries(COMPANY))
        return [query for query in self.queries if query in asked]


@pytest.fixture(autouse=True)
def web(monkeypatch: pytest.MonkeyPatch) -> Web:
    """Keep every test off the network."""
    stub = Web()
    monkeypatch.setattr(company_research, "web_search", stub)
    monkeypatch.setattr(company_review, "web_search", stub)
    return stub


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Database:
    """Create one user with a Dutch CV and an empty database."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.yaml")
    config.write_global_config({"llm_provider": "local"})
    config.save_user_config(USER, {})
    ProfileStore(config.user_cv_dir(USER)).save(
        "nederlands",
        CVDocument(
            full_name="Noor Jansen",
            language="NL",
            main=[
                TextSection(
                    title="Ervaring",
                    body="Noor kalibreert sinds 2020 weegschalen bij Ijkpunt Oost.",
                )
            ],
        ),
    )
    return Database(config.user_db_path(USER))


def _job(db: Database, slug: str, company: str = COMPANY) -> int:
    """Save one vacancy and return its id."""
    return db.save_job(
        JobListing(
            title="Kalibratiespecialist",
            company=company,
            location="Zwolle",
            url=f"https://vacatures.example/{slug}",
            source="test",
            description=DESCRIPTION,
        )
    )


class Clock:
    """The time the research and review code reads while a test runs."""

    def __init__(self) -> None:
        """Start at the tests' fixed moment."""
        self.now = NOW


CLOCK = Clock()


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    """Make research and reviews carry the test's time, not the machine's.

    A result's own date counts as a lookup, so it must agree with the time the
    test passes to company_context, whatever day the tests run on.
    """
    CLOCK.now = NOW

    class Frozen(datetime):
        """datetime whose now() is the test clock."""

        @classmethod
        def now(cls, tz: tzinfo | None = None) -> Frozen:
            """Return the test clock's time."""
            moment = CLOCK.now if tz is None else CLOCK.now.astimezone(tz)
            return cls.fromtimestamp(moment.timestamp(), moment.tzinfo)

    monkeypatch.setattr(company_research, "datetime", Frozen)
    monkeypatch.setattr(company_review, "datetime", Frozen)
    return CLOCK


def _context(
    db: Database, job_id: int, client: FakeLLMClient, now: datetime
) -> CompanyContext:
    """Run company_context for a stored vacancy at a given moment."""
    CLOCK.now = now
    job = db.get_job(job_id)
    assert job is not None
    return company_context(db, job, job_id, client, now=now)


def _purposes(client: FakeLLMClient) -> list[str]:
    """Return the purpose of every model call, in order."""
    return [purpose for _, purpose in client.calls]


def _thin_review() -> CompanyReview:
    """Return a stored review resting on two pages, as old versions wrote them."""
    return CompanyReview(
        company=COMPANY,
        summary="Weinig bekend.",
        cons=["niemand weet wie de audits plant"],
        confidence="low",
        sources=["https://a.example/1", "https://b.example/2"],
    )


def _sourced_research() -> CompanyResearch:
    """Return research found from the web by an earlier lookup."""
    return CompanyResearch(
        company_name=COMPANY,
        research_notes=NOTES,
        sources=[PAGES[0].url],
        research_timestamp=NOW - timedelta(days=3),
    )


def test_a_second_generation_of_either_kind_looks_nothing_up(
    db: Database, web: Web
) -> None:
    """The first click pays for the lookups; the next clicks reuse them."""
    job_id = _job(db, "a")
    first = FakeLLMClient([RESEARCH_ANSWER, REVIEW_ANSWER, QUESTIONS])
    result = generate_interview_questions(USER, job_id, first)
    assert _purposes(first) == ["evaluation", "evaluation", "behavioral_questions"]
    assert result.missing_context == [THIN_REVIEW]
    searched = len(web.queries)
    assert web.research_queries()
    assert web.review_queries()

    answers = FakeLLMClient([ANSWERS])
    generate_interview_answers(USER, job_id, answers)
    again = FakeLLMClient([QUESTIONS])
    second = generate_interview_questions(USER, job_id, again)

    assert len(web.queries) == searched
    assert _purposes(answers) == ["behavioral_questions"]
    assert _purposes(again) == ["behavioral_questions"]
    assert NOTES in again.calls[0][0]
    assert second.missing_context == [THIN_REVIEW]


def test_a_second_vacancy_at_the_same_company_reuses_the_research(
    db: Database, web: Web
) -> None:
    """Research describes the employer, so it is not bought again per vacancy."""
    first = _job(db, "a")
    generate_interview_questions(
        USER, first, FakeLLMClient([RESEARCH_ANSWER, REVIEW_ANSWER, QUESTIONS])
    )
    searched = len(web.queries)
    other = _job(db, "b", company="kwadrant  MEETLAB")
    client = FakeLLMClient([QUESTIONS])
    result = generate_interview_questions(USER, other, client)
    assert len(web.queries) == searched
    assert _purposes(client) == ["behavioral_questions"]
    assert NOTES in client.calls[0][0]
    assert result.missing_context == [THIN_REVIEW]
    # Per-vacancy storage is unchanged: the research stays with the vacancy it
    # was found for, and the per-job readers see exactly that.
    assert db.get_company_research(first) is not None
    assert db.get_company_research(other) is None


def test_nothing_found_is_remembered_until_its_cooldown_ends(
    db: Database, web: Web
) -> None:
    """An employer the web does not know is not searched for on every click."""
    web.results = []
    job_id = _job(db, "a")
    client = FakeLLMClient([QUESTIONS])
    first = _context(db, job_id, client, NOW)
    assert first.missing == [NO_PUBLIC_INFO, NO_REVIEW]
    searched = len(web.queries)
    assert searched > 0

    soon = _context(db, job_id, client, NOW + REVIEW_COOLDOWN - timedelta(hours=1))
    assert len(web.queries) == searched
    assert soon.missing == [
        f"{NO_PUBLIC_INFO} (checked {CHECKED})",
        f"{NO_REVIEW} (checked {CHECKED})",
    ]

    later = _context(db, job_id, client, NOW + RESEARCH_COOLDOWN + timedelta(hours=1))
    assert len(web.queries) > searched
    assert later.missing == [NO_PUBLIC_INFO, NO_REVIEW]
    assert client.calls == []


def test_research_and_review_cooldowns_run_separately(db: Database, web: Web) -> None:
    """After two weeks only the review is looked for again; research waits a month."""
    web.results = []
    job_id = _job(db, "a")
    client = FakeLLMClient([QUESTIONS])
    _context(db, job_id, client, NOW)
    research, review = len(web.research_queries()), len(web.review_queries())
    _context(db, job_id, client, NOW + REVIEW_COOLDOWN + timedelta(hours=1))
    assert len(web.research_queries()) == research
    assert len(web.review_queries()) == 2 * review
    _context(db, job_id, client, NOW + RESEARCH_COOLDOWN + timedelta(hours=1))
    assert len(web.research_queries()) == 2 * research


def test_a_thin_review_is_refreshed_once_then_used_as_it_is(
    db: Database, web: Web
) -> None:
    """The Findwhere case: two sources, rewritten on every click until now."""
    job_id = _job(db, "a")
    db.save_company_research(job_id, _sourced_research().model_dump_json())
    db.save_company_review(COMPANY, _thin_review().model_dump_json())
    client = FakeLLMClient([REVIEW_ANSWER])
    first = _context(db, job_id, client, NOW)
    assert _purposes(client) == ["evaluation"]
    assert first.missing == [THIN_REVIEW]
    assert first.review is not None
    assert len(first.review.sources) == 2
    searched = len(web.queries)

    for hours in (1, 24, 24 * 13):
        again = _context(db, job_id, client, NOW + timedelta(hours=hours))
        assert again.missing == [THIN_REVIEW]
        assert again.review == first.review
    assert len(web.queries) == searched
    assert _purposes(client) == ["evaluation"]

    _context(db, job_id, client, NOW + REVIEW_COOLDOWN + timedelta(hours=1))
    assert _purposes(client) == ["evaluation", "evaluation"]


def test_a_thin_review_written_recently_elsewhere_is_not_rewritten(
    db: Database, web: Web
) -> None:
    """A review the daily run wrote two days ago has not gained sources since."""
    job_id = _job(db, "a")
    db.save_company_research(job_id, _sourced_research().model_dump_json())
    recent = _thin_review().model_copy(update={"reviewed_at": NOW - timedelta(days=2)})
    db.save_company_review(COMPANY, recent.model_dump_json())
    client = FakeLLMClient([REVIEW_ANSWER])
    result = _context(db, job_id, client, NOW)
    assert web.queries == []
    assert client.calls == []
    assert result.missing == [THIN_REVIEW]
    assert result.review_checked_at == NOW - timedelta(days=2)


def test_a_failed_lookup_is_retried_after_the_short_cooldown(
    db: Database, web: Web
) -> None:
    """A failure says nothing about the company, so it is not held for a month."""
    job_id = _job(db, "a")
    broken = FakeLLMClient(["Sorry, dat weet ik niet."])
    first = _context(db, job_id, broken, NOW)
    assert first.missing == [RESEARCH_FAILED, NO_REVIEW]
    assert _purposes(broken) == ["evaluation", "evaluation"]
    searched = len(web.queries)

    held = _context(db, job_id, broken, NOW + FAILED_COOLDOWN - timedelta(minutes=5))
    assert len(web.queries) == searched
    assert len(broken.calls) == 2
    assert held.missing == [f"{RESEARCH_FAILED} (tried {CHECKED})", NO_REVIEW]

    working = FakeLLMClient([RESEARCH_ANSWER, REVIEW_ANSWER])
    retried = _context(
        db, job_id, working, NOW + FAILED_COOLDOWN + timedelta(minutes=5)
    )
    assert _purposes(working) == ["evaluation", "evaluation"]
    assert retried.research is not None
    assert retried.missing == [THIN_REVIEW]


def test_an_unreachable_model_holds_both_lookups_for_the_short_cooldown(
    db: Database, web: Web
) -> None:
    """The review would ask the same unreachable host, so it waits as well."""
    job_id = _job(db, "a")
    unreachable = FakeLLMClient([], repeat_last=False)
    first = _context(db, job_id, unreachable, NOW)
    assert first.missing == [RESEARCH_FAILED, NO_REVIEW]
    assert web.review_queries() == []
    review = recall(db, COMPANY, LookupKind.REVIEW)
    assert set(review.last) == {LookupOutcome.FAILED}
    searched = len(web.queries)
    _context(db, job_id, unreachable, NOW + timedelta(minutes=10))
    assert len(web.queries) == searched
    assert len(unreachable.calls) == 1


def test_research_without_sources_counts_as_absent_once(db: Database, web: Web) -> None:
    """Research from model memory is looked past once, then the lookup stands."""
    web.results = []
    job_id = _job(db, "a")
    remembered = CompanyResearch(company_name=COMPANY, research_notes="Uit het hoofd.")
    db.save_company_research(job_id, remembered.model_dump_json())
    client = FakeLLMClient([QUESTIONS])
    first = _context(db, job_id, client, NOW)
    assert web.research_queries()
    assert first.research is None
    assert first.missing[0] == NO_PUBLIC_INFO
    searched = len(web.research_queries())
    second = _context(db, job_id, client, NOW + timedelta(days=1))
    assert len(web.research_queries()) == searched
    assert second.research is None
    assert second.missing[0] == f"{NO_PUBLIC_INFO} (checked {CHECKED})"
    assert db.get_company_research(job_id) is not None


def test_the_context_says_when_the_company_was_last_looked_up(
    db: Database, web: Web
) -> None:
    """A caller can show "company research from <date>"."""
    job_id = _job(db, "a")
    client = FakeLLMClient([RESEARCH_ANSWER, REVIEW_ANSWER])
    fresh = _context(db, job_id, client, NOW)
    assert fresh.research_checked_at == NOW
    assert fresh.review_checked_at == NOW
    later = _context(db, job_id, client, NOW + timedelta(days=2))
    assert later.research_checked_at == NOW
    assert later.review_checked_at == NOW


def test_research_stored_before_lookups_were_remembered_is_dated_by_itself(
    db: Database, web: Web
) -> None:
    """Old research has no lookup row, but it says when it was written."""
    job_id = _job(db, "a")
    db.save_company_research(job_id, _sourced_research().model_dump_json())
    db.save_company_review(COMPANY, _thin_review().model_dump_json())
    result = _context(db, job_id, FakeLLMClient([REVIEW_ANSWER]), NOW)
    assert web.research_queries() == []
    assert result.research_checked_at == NOW - timedelta(days=3)


def test_a_failure_is_not_a_check(db: Database, web: Web) -> None:
    """A lookup that failed checked nothing, so it has no date to show."""
    job_id = _job(db, "a")
    result = _context(db, job_id, FakeLLMClient([], repeat_last=False), NOW)
    assert result.research_checked_at is None
    assert result.review_checked_at is None


def test_research_and_review_prose_is_stored_in_the_house_style(
    db: Database, web: Web
) -> None:
    """The prompts carry the style; what comes back is cleaned before storing."""
    job_id = _job(db, "a")
    client = FakeLLMClient([RESEARCH_ANSWER, REVIEW_ANSWER])
    _context(db, job_id, client, NOW)
    assert all(HOUSE_STYLE in prompt for prompt, _ in client.calls)
    stored_research = db.get_company_research(job_id)
    stored_review = db.get_company_review(COMPANY)
    assert stored_research is not None
    assert stored_review is not None
    research = CompanyResearch.model_validate_json(stored_research)
    review = CompanyReview.model_validate_json(stored_review)
    assert research.research_notes == f"{NOTES}, vooral in de regio."
    assert research.culture_indicators == ["korte lijnen, weinig lagen"]
    assert review.summary == "Behulpzame collega's, de werkdruk rond audits is hoog."
    assert review.pros == ["behulpzame collega's"]
    assert research.sources == [page.url for page in PAGES]
    for text in (stored_research, stored_review):
        assert "\\u2014" not in text
        assert "\\u2013" not in text
        assert "\u2014" not in text
        assert "\u2013" not in text


def test_dated_entries_still_drop_questions_that_cite_them() -> None:
    """A dated entry means the same as the plain one to the citation check."""
    research = f"{NO_PUBLIC_INFO} (checked {CHECKED})"
    review = f"{NO_REVIEW} (checked {CHECKED})"
    failed = f"{RESEARCH_FAILED} (tried {CHECKED})"
    assert gap_kind(research) == NO_PUBLIC_INFO
    assert gap_kind(review) == NO_REVIEW
    assert gap_kind(failed) == RESEARCH_FAILED
    assert gap_kind(THIN_REVIEW) == THIN_REVIEW
    assert gap_kind("no vacancy description") == "no vacancy description"

    def cites(source: str) -> InterviewQuestion:
        return InterviewQuestion(
            question="Klopt het dat jullie groeien?",
            theme=QuestionTheme.COMPANY,
            why="Het raakt aan waar Noor heen wil.",
            grounded_in=source,
        )

    assert _drop_unsupported([cites("company research: growth")], [research]) == []
    assert _drop_unsupported([cites("company research: growth")], [failed]) == []
    assert _drop_unsupported([cites("company review: cons")], [review]) == []
    kept = cites("vacancy")
    assert _drop_unsupported([kept], [research, review]) == [kept]
