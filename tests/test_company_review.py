"""Tests for the company work-quality review feature."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from job_scout.company_review import (
    _coerce_score,
    gather_company_evidence,
    review_company,
)
from job_scout.database import Database
from job_scout.models import CompanyReview
from job_scout.websearch import SearchResult
from tests.helpers import FakeLLMClient

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
        "confidence": "medium",
    }
)


def test_gather_evidence_collects_snippets_and_sources() -> None:
    """Evidence gathering flattens search snippets and dedupes."""
    results = [
        SearchResult(url="https://a.nl", title="Praxis reviews", snippet="3.6 stars"),
        SearchResult(url="https://b.nl", title="Praxis omzet", snippet="stable"),
    ]
    with patch("job_scout.company_review.web_search", return_value=results):
        snippets, sources = gather_company_evidence("Praxis")
    assert any("3.6 stars" in s for s in snippets)
    assert "https://a.nl" in sources


def test_review_company_synthesises_from_evidence() -> None:
    """review_company parses the LLM JSON into a CompanyReview."""
    results = [SearchResult(url="https://a.nl", title="Praxis", snippet="3.6 stars")]
    client = FakeLLMClient([_REVIEW_JSON])
    with patch("job_scout.company_review.web_search", return_value=results):
        review = review_company("Praxis", client=client)
    assert review.work_score == 68
    assert review.confidence == "medium"
    assert "Friendly colleagues" in review.pros
    assert review.company_age == "Founded 1978"
    assert review.sources == ["https://a.nl"]
    assert client.calls[0][1] == "evaluation"


def test_review_company_handles_llm_error() -> None:
    """A failed LLM call yields a low-confidence, score-less review, not a crash."""
    with (
        patch("job_scout.company_review.web_search", return_value=[]),
        patch(
            "job_scout.company_review.gather_company_evidence", return_value=([], [])
        ),
    ):
        client = FakeLLMClient([], repeat_last=False)  # raises LLMError
        review = review_company("Obscure Co", client=client)
    assert review.work_score is None
    assert review.confidence == "low"


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
    review = CompanyReview(company="Praxis", work_score=68, confidence="medium")
    db.save_company_review("Praxis", review.model_dump_json())

    cached = db.get_company_review("  praxis ")  # case + whitespace normalised
    assert cached is not None
    assert CompanyReview.model_validate_json(cached).work_score == 68
    assert db.get_company_review("Praxis", max_age_days=-1) is None  # stale
    assert db.get_company_review("Unknown") is None
