"""Tests for the company work-quality review feature.

Every company and URL here is fictional; URLs use the reserved ``.example``
top-level domain. No test touches the network: web_search is patched in every
test that reaches it.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from job_scout.company_review import (
    MIN_REVIEW_SOURCES,
    CompanyReviewError,
    _coerce_score,
    evidence_confidence,
    gather_company_evidence,
    review_company,
)
from job_scout.database import Database
from job_scout.llm.base import LLMError
from job_scout.models import CompanyReview
from job_scout.websearch import SearchResult
from tests.helpers import FakeLLMClient

_COMPANY = "Kwadrant Meetlab"

_REVIEW_JSON = json.dumps(
    {
        "work_score": 68,
        "summary": "Collegial but high workload at peak times.",
        "pros": ["Friendly colleagues", "Discounts"],
        "cons": ["Workload", "Pay"],
        "employee_sentiment": "Mixed, ~3.6/5.",
        "financial_health": "Stable parent group.",
        "growth": "Modest online growth.",
        "company_age": "Founded 1978",
        "confidence": "high",
    }
)


def _results(count: int) -> list[SearchResult]:
    """Return *count* distinct results that all name the company."""
    return [
        SearchResult(
            url=f"https://reviews{n}.example/kwadrant-meetlab",
            title=f"Kwadrant Meetlab reviews {n}",
            snippet=f"Rated {n}.5 out of 5 by employees.",
        )
        for n in range(count)
    ]


def test_gather_evidence_collects_snippets_and_sources() -> None:
    """Evidence gathering flattens search snippets and dedupes."""
    results = [
        SearchResult(url="https://a.example", title="Kwadrant Meetlab", snippet="3.6"),
        SearchResult(
            url="https://b.example", title="Kwadrant Meetlab omzet", snippet="x"
        ),
    ]
    with patch("job_scout.company_review.web_search", return_value=results):
        snippets, sources = gather_company_evidence(_COMPANY)
    assert any("3.6" in s for s in snippets)
    assert sources == ["https://a.example", "https://b.example"]


def test_gather_evidence_drops_namesakes() -> None:
    """Reviews of a different organisation are not evidence about this one."""
    results = [
        SearchResult(
            url="https://a.example", title="Kwadrant Bouw reviews", snippet="2/5"
        ),
        SearchResult(url="https://b.example", title="Meetlab Zuid", snippet="4/5"),
    ]
    with patch("job_scout.company_review.web_search", return_value=results):
        assert gather_company_evidence(_COMPANY) == ([], [])


def test_review_company_synthesises_from_evidence() -> None:
    """The model's JSON becomes a review; its own confidence is ignored."""
    client = FakeLLMClient([_REVIEW_JSON])
    with patch("job_scout.company_review.web_search", return_value=_results(1)):
        review = review_company(_COMPANY, client=client)
    assert review is not None
    assert review.work_score == 68
    assert review.confidence == "low"
    assert "Friendly colleagues" in review.pros
    assert review.company_age == "Founded 1978"
    assert review.sources == ["https://reviews0.example/kwadrant-meetlab"]
    assert client.calls[0][1] == "evaluation"


@pytest.mark.parametrize(
    ("count", "confidence"),
    [
        (1, "low"),
        (MIN_REVIEW_SOURCES - 1, "low"),
        (MIN_REVIEW_SOURCES, "medium"),
        (6, "high"),
    ],
)
def test_confidence_is_counted_from_the_sources(count: int, confidence: str) -> None:
    """How sure the review is depends on how much was read, not on the model."""
    assert evidence_confidence(count) == confidence
    client = FakeLLMClient([_REVIEW_JSON])
    with patch("job_scout.company_review.web_search", return_value=_results(count)):
        review = review_company(_COMPANY, client=client)
    assert review is not None
    assert review.confidence == confidence


def test_no_evidence_means_no_model_call_and_no_review() -> None:
    """Without a single snippet the model is not asked to fill in from memory."""
    client = FakeLLMClient([_REVIEW_JSON])
    with patch("job_scout.company_review.web_search", return_value=[]):
        assert review_company(_COMPANY, client=client) is None
    assert review_company("  ", client=client) is None
    assert client.calls == []


def test_the_prompt_allows_only_the_snippets() -> None:
    """The model is told to use the evidence only and to leave a score out."""
    client = FakeLLMClient([_REVIEW_JSON])
    with patch("job_scout.company_review.web_search", return_value=_results(2)):
        review_company(_COMPANY, client=client)
    prompt = client.calls[0][0]
    assert "own knowledge" not in prompt
    assert "ALWAYS give" not in prompt
    assert "Use nothing from memory" in prompt
    assert "Otherwise it is null" in prompt
    assert "Rated 1.5 out of 5" in prompt


def test_a_score_the_model_leaves_out_stays_out() -> None:
    """With nothing to base a score on, the review carries no score at all."""
    unscored = json.dumps({"work_score": None, "summary": None, "pros": [], "cons": []})
    with patch("job_scout.company_review.web_search", return_value=_results(3)):
        review = review_company(_COMPANY, client=FakeLLMClient([unscored]))
    assert review is not None
    assert review.work_score is None
    assert review.summary == ""
    assert review.confidence == "medium"


def test_a_failed_model_call_is_raised_not_dressed_up_as_a_review() -> None:
    """A placeholder review could be stored over a good one; an error cannot."""
    with patch("job_scout.company_review.web_search", return_value=_results(3)):
        with pytest.raises(LLMError):
            review_company(_COMPANY, client=FakeLLMClient([], repeat_last=False))
        with pytest.raises(CompanyReviewError):
            review_company(_COMPANY, client=FakeLLMClient(["not json"]))
        with pytest.raises(CompanyReviewError):
            review_company(_COMPANY, client=FakeLLMClient(['["a", "list"]']))


def test_coerce_score_clamps_and_rejects_junk() -> None:
    """Score coercion clamps to [0, 100] and rejects non-numeric values."""
    assert _coerce_score(150) == 100
    assert _coerce_score(-5) == 0
    assert _coerce_score("72") == 72
    assert _coerce_score(None) is None
    assert _coerce_score("high") is None
    assert _coerce_score(True) is None


def test_company_review_cache_roundtrip_and_freshness(tmp_path: Path) -> None:
    """The DB caches reviews by normalised name and honours freshness."""
    db = Database(tmp_path / "jobs.db")
    review = CompanyReview(company=_COMPANY, work_score=68, confidence="medium")
    db.save_company_review(_COMPANY, review.model_dump_json())

    cached = db.get_company_review("  kwadrant meetlab ")  # case + whitespace
    assert cached is not None
    assert CompanyReview.model_validate_json(cached).work_score == 68
    assert db.get_company_review(_COMPANY, max_age_days=-1) is None  # stale
    assert db.get_company_review("Unknown") is None


def test_the_pipeline_caches_nothing_when_the_web_names_nobody(tmp_path: Path) -> None:
    """A run over a company nobody writes about stores no invented review."""
    from job_scout.cli import _get_or_build_review

    db = Database(tmp_path / "jobs.db")
    client = FakeLLMClient([_REVIEW_JSON])
    with patch("job_scout.company_review.web_search", return_value=[]):
        review = _get_or_build_review(
            _COMPANY, db, client, searxng_url=None, api_key=None, dry_run=False
        )
    assert review is None
    assert db.get_company_review(_COMPANY) is None
    assert client.calls == []


@pytest.fixture
def review_user(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Database:
    """One user for the company-review command, with its database."""
    import job_scout.config as app_config

    monkeypatch.setattr(app_config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(app_config, "CONFIG_PATH", tmp_path / "config.yaml")
    app_config.write_global_config({"llm_provider": "local"})
    app_config.save_user_config("Sam", {})
    return Database(app_config.user_db_path("Sam"))


@pytest.mark.parametrize(
    ("results", "answer", "expected"),
    [
        ([], _REVIEW_JSON, "No public web information"),
        (_results(3), "not json", "Could not write the review"),
    ],
)
def test_the_review_command_stores_nothing_it_could_not_write(
    review_user: Database,
    monkeypatch: pytest.MonkeyPatch,
    results: list[SearchResult],
    answer: str,
    expected: str,
) -> None:
    """Neither an empty web nor a failed call ends up in the cache."""
    from click.testing import CliRunner

    from job_scout.cli import cli

    monkeypatch.setattr(
        "job_scout.cli.get_llm_client", lambda _c: FakeLLMClient([answer])
    )
    with patch("job_scout.company_review.web_search", return_value=results):
        result = CliRunner().invoke(cli, ["company-review", _COMPANY, "--refresh"])
    assert result.exit_code == 1
    assert expected in result.output
    assert review_user.get_company_review(_COMPANY) is None


def test_the_review_command_prints_a_review_without_a_score(
    review_user: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A review the evidence gave no score prints n/a, and is stored."""
    from click.testing import CliRunner

    from job_scout.cli import cli

    unscored = json.dumps({"work_score": None, "summary": "Weinig bekend.", "cons": []})
    monkeypatch.setattr(
        "job_scout.cli.get_llm_client", lambda _c: FakeLLMClient([unscored])
    )
    with patch("job_scout.company_review.web_search", return_value=_results(3)):
        result = CliRunner().invoke(cli, ["company-review", _COMPANY])
    assert result.exit_code == 0, result.output
    assert "work score: n/a (medium)" in result.output
    assert review_user.get_company_review(_COMPANY) is not None
