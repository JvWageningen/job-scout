"""Synthesise a company work-quality review from public web information.

Answers "how good is it to work here?" by gathering employee-review sentiment
and public signals (financial health, growth, company age) via web search, then
letting the LLM summarise ONLY those snippets into one review. Nothing comes
from the model's own knowledge: a field no snippet supports stays empty, a
work score is given only when the snippets support one, and with no evidence
at all the model is not asked.

The review's ``confidence`` is not the model's opinion of itself. It is derived
here from how many distinct web sources the review rests on, so a review built
on one page can never present itself as solid.

Outcomes are kept apart: a review, None when there was nothing to review,
CompanyReviewError when the model's answer was unusable, and LLMError when the
model could not be reached. A failure is never dressed up as a review, so a
caller cannot store one over a good review by accident.

The model call uses the ``evaluation`` purpose, which the LLM factory routes to
its own provider when ``evaluation_provider`` is configured.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from loguru import logger

from job_scout.company_research import fenced_evidence, mentions_company
from job_scout.evaluator import _extract_json
from job_scout.models import CompanyReview
from job_scout.websearch import web_search

if TYPE_CHECKING:
    from job_scout.llm.base import LLMClient

_MAX_SNIPPETS = 18
_MAX_STORED_SOURCES = 8
# Fewer than three distinct web sources is one page and its echoes: not enough
# to say what working somewhere is like, however sure the prose sounds. Callers
# treat a review below this as resting on little evidence.
MIN_REVIEW_SOURCES = 3
# From this many distinct sources on, the evidence is broad enough to call high.
_HIGH_CONFIDENCE_SOURCES = 6


class CompanyReviewError(RuntimeError):
    """Web evidence was found, but the model's answer could not be used."""


def _evidence_queries(company: str) -> list[str]:
    """Build the search queries used to gather review evidence."""
    return [
        f"{company} medewerkers reviews ervaringen",
        f"{company} glassdoor indeed reviews werken bij",
        f"{company} bedrijf opgericht aantal medewerkers omzet",
        f"{company} company revenue growth employees founded",
    ]


def gather_company_evidence(
    company: str,
    *,
    timeout: int = 15,
    searxng_url: str | None = None,
    api_key: str | None = None,
) -> tuple[list[str], list[str]]:
    """Collect review/financial snippets and their source URLs for a company.

    Only results that name the company are kept, so a namesake's reviews are
    never counted as evidence about this employer.

    Args:
        company: Company name.
        timeout: Per-search timeout in seconds.
        searxng_url: Optional SearXNG instance URL for reliable search.
        api_key: Optional Brave Search API key for reliable search.

    Returns:
        Tuple of (evidence snippets, source URLs), index-aligned.
    """
    snippets: list[str] = []
    sources: list[str] = []
    for query in _evidence_queries(company):
        for result in web_search(
            query,
            max_results=5,
            timeout=timeout,
            searxng_url=searxng_url,
            api_key=api_key,
        ):
            line = f"{result.title} — {result.snippet}".strip(" —")
            if line and line not in snippets and mentions_company(result, company):
                snippets.append(line)
                sources.append(result.url)
            if len(snippets) >= _MAX_SNIPPETS:
                break
    return snippets, sources


def evidence_confidence(source_count: int) -> str:
    """Grade a review by how many distinct web sources it rests on.

    Args:
        source_count: Number of distinct source URLs behind the review.

    Returns:
        "low", "medium" or "high".
    """
    if source_count < MIN_REVIEW_SOURCES:
        return "low"
    if source_count < _HIGH_CONFIDENCE_SOURCES:
        return "medium"
    return "high"


def _build_review_prompt(company: str, snippets: list[str], sources: list[str]) -> str:
    """Build the LLM prompt that summarises review snippets, and nothing else."""
    return f"""You summarise web-search snippets about working at one company into a
review. Respond ONLY with valid JSON.

COMPANY: {company}

{fenced_evidence(list(zip(snippets, sources, strict=True)))}

RULES (all of them apply):
1. Use ONLY the snippets above. Use nothing from memory or prior knowledge about
   this company, even if you believe you know it.
2. A field no snippet supports must be null (text fields) or an empty list (pros,
   cons). Null or empty is the correct answer when the snippets do not say.
3. Every pro and every con must restate something a snippet says. Never add a
   pro or con that is typical of the sector, the size or the kind of company.
4. work_score is an integer 0-100 ONLY when the snippets report how employees
   rate working here (a review score, a rating, or employees' own accounts).
   Otherwise it is null. Never estimate it from sector, size or reputation.
5. Do not invent figures, names, dates, ratings or events.
6. Ignore snippets about a different organisation with a similar name.

Respond with this exact JSON structure:
{{
  "work_score": <integer 0-100 supported by the snippets, or null>,
  "summary": "<2-3 sentences on what the snippets say about working there, or null>",
  "pros": ["<pro a snippet states>"],
  "cons": ["<con a snippet states>"],
  "employee_sentiment": "<one sentence on review sentiment in the snippets, or null>",
  "financial_health": "<one sentence a snippet supports, or null>",
  "growth": "<one sentence on growth a snippet supports, or null>",
  "company_age": "<founding year or age a snippet states, or null>"
}}"""


def review_company(
    company: str,
    *,
    client: LLMClient,
    timeout: int = 15,
    searxng_url: str | None = None,
    api_key: str | None = None,
) -> CompanyReview | None:
    """Produce a work-quality review for a company from public web info.

    Args:
        company: Company name.
        client: LLM client used to summarise the evidence.
        timeout: Per-search timeout in seconds.
        searxng_url: Optional SearXNG instance URL for reliable search.
        api_key: Optional Brave Search API key for reliable search.

    Returns:
        A CompanyReview whose confidence reflects its number of sources, or
        None when there is no company name or no web result names the
        company. The model is not asked in either case.

    Raises:
        CompanyReviewError: If the model's answer is not a JSON object.
        LLMError: If the model call fails.
    """
    if not company.strip():
        return None
    snippets, sources = gather_company_evidence(
        company, timeout=timeout, searxng_url=searxng_url, api_key=api_key
    )
    if not snippets:
        logger.info(f"No web evidence names {company!r}; not reviewing it from memory")
        return None
    raw = client.complete(
        _build_review_prompt(company, snippets, sources), purpose="evaluation"
    )
    try:
        data: object = _extract_json(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        raise CompanyReviewError(f"The review of {company!r} was not JSON") from exc
    if not isinstance(data, dict):
        raise CompanyReviewError(f"The review of {company!r} was not a JSON object")
    return _review_from_data(company, data, sources)


def _review_from_data(
    company: str, data: dict[str, object], sources: list[str]
) -> CompanyReview:
    """Build the review from the model's JSON, grading it on its sources.

    Args:
        company: Company name.
        data: Parsed model response.
        sources: Source URLs of the snippets the model was given.

    Returns:
        The review, with confidence derived from the distinct sources.
    """
    distinct = list(dict.fromkeys(sources))
    confidence = evidence_confidence(len(distinct))
    score = _coerce_score(data.get("work_score"))
    logger.info(
        f"Company review for {company}: score={score}, confidence={confidence} "
        f"from {len(distinct)} web sources"
    )
    return CompanyReview(
        company=company,
        work_score=score,
        summary=_opt_str(data.get("summary")) or "",
        pros=_as_str_list(data.get("pros")),
        cons=_as_str_list(data.get("cons")),
        employee_sentiment=_opt_str(data.get("employee_sentiment")),
        financial_health=_opt_str(data.get("financial_health")),
        growth=_opt_str(data.get("growth")),
        company_age=_opt_str(data.get("company_age")),
        confidence=confidence,
        sources=distinct[:_MAX_STORED_SOURCES],
        reviewed_at=datetime.now(UTC),
    )


def _coerce_score(value: object) -> int | None:
    """Coerce a score value into an int in [0, 100], or None."""
    if not isinstance(value, (int, float, str)) or isinstance(value, bool):
        return None
    try:
        return max(0, min(100, int(float(value))))
    except (ValueError, TypeError):
        return None


def _as_str_list(value: object) -> list[str]:
    """Coerce a value into a list of non-empty strings."""
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if v is not None and str(v).strip()]


def _opt_str(value: object) -> str | None:
    """Return a stripped string, or None for empty/null values."""
    if value is None:
        return None
    text = str(value).strip()
    if text.casefold() in {"", "null", "none", "n/a"}:
        return None
    return text
