"""Tests for LLM integration helpers in evaluator."""

from __future__ import annotations

import json

import pytest

from job_scout.evaluator import _build_fit_prompt, _extract_json
from job_scout.models import JobListing
from tests.helpers import FakeLLMClient


def test_extract_json_plain() -> None:
    """_extract_json parses a plain JSON string."""
    text = '{"fit_score": 75, "fit_reasoning": "Good"}'
    result = _extract_json(text)
    assert result["fit_score"] == 75


def test_extract_json_fenced_json_block() -> None:
    """_extract_json strips ```json fences before parsing."""
    text = '```json\n{"fit_score": 80, "matches_negative": false}\n```'
    result = _extract_json(text)
    assert result["fit_score"] == 80
    assert result["matches_negative"] is False


def test_extract_json_fenced_plain_block() -> None:
    """_extract_json strips plain ``` fences before parsing."""
    text = '```\n{"dutch": ["ingenieur"], "english": ["engineer"]}\n```'
    result = _extract_json(text)
    assert "dutch" in result


def test_extract_json_invalid_raises() -> None:
    """_extract_json raises JSONDecodeError on invalid input."""
    with pytest.raises(json.JSONDecodeError):
        _extract_json("This is not JSON at all.")


def test_evaluate_fit_success_with_fake_client(sample_job: JobListing) -> None:
    """evaluate_fit parses a valid LLM JSON response correctly."""
    from job_scout.evaluator import evaluate_fit

    response = json.dumps(
        {
            "fit_score": 82,
            "fit_reasoning": "Strong technical alignment",
            "matches_negative": False,
            "negative_reasoning": "No negative signals",
            "salary_min": 3500,
            "salary_max": 4500,
            "salary_period": "monthly",
            "vacation_days": 25,
            "compensation_reasoning": "Based on listing",
        }
    )
    client = FakeLLMClient([response])
    fit, neg, comp = evaluate_fit(sample_job, "Python developer", "", "", client=client)

    assert fit.fit_score == 82
    assert "alignment" in fit.reasoning
    assert neg.matches_negative is False
    assert comp.salary_min == 3500
    assert comp.salary_max == 4500
    assert comp.vacation_days == 25


def test_evaluate_fit_llm_error_returns_zero_score(sample_job: JobListing) -> None:
    """evaluate_fit returns fit_score=0 and no-negative when LLM raises LLMError."""
    from job_scout.evaluator import evaluate_fit

    client = FakeLLMClient([], repeat_last=False)
    fit, neg, comp = evaluate_fit(sample_job, "Python developer", "", "", client=client)

    assert fit.fit_score == 0
    assert neg.matches_negative is False
    assert comp.salary_min is None


def test_evaluate_fit_invalid_json_returns_zero_score(sample_job: JobListing) -> None:
    """evaluate_fit returns fit_score=0 when LLM returns non-JSON output."""
    from job_scout.evaluator import evaluate_fit

    client = FakeLLMClient(["Sorry, I cannot help with that."])
    fit, _neg, _comp = evaluate_fit(
        sample_job, "Python developer", "", "", client=client
    )

    assert fit.fit_score == 0


def test_build_fit_prompt_contains_job_details(sample_job: JobListing) -> None:
    """_build_fit_prompt includes job title, company, and profile in the prompt."""
    prompt = _build_fit_prompt(
        sample_job, "Senior Python dev", "CV text here", "No management roles"
    )
    assert sample_job.title in prompt
    assert sample_job.company in prompt
    assert "Senior Python dev" in prompt
    assert "No management roles" in prompt


def test_generate_keywords_returns_lists() -> None:
    """generate_keywords returns a KeywordsResult with correct lists."""
    from job_scout.evaluator import generate_keywords

    response = json.dumps(
        {
            "dutch": ["software ingenieur", "ontwikkelaar"],
            "english": ["engineer"],
            "title_include": ["developer", "engineer"],
            "title_exclude": ["SAP", "payroll"],
        }
    )
    client = FakeLLMClient([response])
    result = generate_keywords("Python developer", "CV", client=client)

    assert "software ingenieur" in result.dutch
    assert "engineer" in result.english
    assert "developer" in result.title_include
    assert "SAP" in result.title_exclude


def test_generate_keywords_returns_empty_on_bad_json() -> None:
    """generate_keywords returns empty lists when LLM output is not valid JSON."""
    from job_scout.evaluator import generate_keywords

    client = FakeLLMClient(["Not JSON"])
    result = generate_keywords("Profile", "CV", client=client)

    assert result.dutch == []
    assert result.english == []


def test_evaluate_fit_records_purpose(sample_job: JobListing) -> None:
    """evaluate_fit calls the client with purpose='evaluation'."""
    from job_scout.evaluator import evaluate_fit

    response = json.dumps(
        {
            "fit_score": 50,
            "fit_reasoning": "ok",
            "matches_negative": False,
            "negative_reasoning": "ok",
        }
    )
    client = FakeLLMClient([response])
    evaluate_fit(sample_job, "dev", "", "", client=client)

    assert client.calls[0][1] == "evaluation"


def test_generate_keywords_records_purpose() -> None:
    """generate_keywords calls the client with purpose='keywords'."""
    from job_scout.evaluator import generate_keywords

    response = json.dumps(
        {"dutch": [], "english": [], "title_include": [], "title_exclude": []}
    )
    client = FakeLLMClient([response])
    generate_keywords("profile", "cv", client=client)

    assert client.calls[0][1] == "keywords"
