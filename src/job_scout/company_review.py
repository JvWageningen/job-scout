"""Synthesise a company work-quality review from public web information.

Answers "how good is it to work here?" by gathering employee-review sentiment
and public signals (financial health, growth, company age) via keyless web
search, then letting the LLM synthesise a single balanced review with an
estimated score. Deliberately honest about confidence: when little is found,
the score is low-confidence or omitted rather than invented.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from loguru import logger

from job_scout.evaluator import _extract_json
from job_scout.llm.base import LLMError
from job_scout.models import CompanyReview
from job_scout.websearch import web_search

if TYPE_CHECKING:
    from job_scout.llm.base import LLMClient

_MAX_SNIPPETS = 18
_MAX_EVIDENCE_CHARS = 4500


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

    Args:
        company: Company name.
        timeout: Per-search timeout in seconds.
        searxng_url: Optional SearXNG instance URL for reliable search.
        api_key: Optional Brave Search API key for reliable search.

    Returns:
        Tuple of (evidence snippets, source URLs).
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
            if line and line not in snippets:
                snippets.append(line)
                sources.append(result.url)
            if len(snippets) >= _MAX_SNIPPETS:
                break
    return snippets, sources


def _build_review_prompt(company: str, evidence: str) -> str:
    """Build the LLM prompt for synthesising a company work-quality review."""
    return f"""You assess how good a company is to work for. Respond ONLY with JSON.

COMPANY: {company}

PUBLIC WEB EVIDENCE (search snippets — reviews, financials, news):
{evidence or "(no useful public information was found)"}

Combine the evidence above with your own knowledge of this company to produce a
balanced work-quality review. Weigh employee-review sentiment most heavily, then
public signals (financial health, growth, company age/stability). ALWAYS give a
best-effort work_score using whatever is publicly known about the company (its
sector, size, reputation, stability); use null ONLY when the company is truly
unidentifiable or too generic to assess. Do NOT invent specific figures you are
unsure of. Set confidence to reflect how much you actually know: "high" = solid
live evidence; "medium" = a recognisable company or some evidence; "low" = mostly
general inference with little concrete data.

Respond with this exact JSON structure:
{{
  "work_score": <integer 0-100; null ONLY if the company is unidentifiable>,
  "summary": "<2-3 sentence balanced summary of what it's like to work there>",
  "pros": ["<short pro>", ...],
  "cons": ["<short con>", ...],
  "employee_sentiment": "<one sentence on review sentiment, or null>",
  "financial_health": "<one sentence, or null>",
  "growth": "<one sentence on growth/trajectory, or null>",
  "company_age": "<founding year or age if known, or null>",
  "confidence": "<low|medium|high, based on how much evidence was available>"
}}"""


def review_company(
    company: str,
    *,
    client: LLMClient,
    timeout: int = 15,
    searxng_url: str | None = None,
    api_key: str | None = None,
) -> CompanyReview:
    """Produce a work-quality review for a company from public web info.

    Args:
        company: Company name.
        client: LLM client used to synthesise the review.
        timeout: Per-search timeout in seconds.
        searxng_url: Optional SearXNG instance URL for reliable search.
        api_key: Optional Brave Search API key for reliable search.

    Returns:
        A CompanyReview; low-confidence and score None when little is known.
    """
    from datetime import UTC, datetime  # noqa: PLC0415

    if not company:
        return CompanyReview(company=company, summary="No company name.")

    snippets, sources = gather_company_evidence(
        company, timeout=timeout, searxng_url=searxng_url, api_key=api_key
    )
    evidence = "\n".join(f"- {s}" for s in snippets)[:_MAX_EVIDENCE_CHARS]
    prompt = _build_review_prompt(company, evidence)
    try:
        raw = client.complete(prompt, purpose="evaluation")
        data = _extract_json(raw)
    except (LLMError, json.JSONDecodeError, ValueError) as exc:
        logger.warning(f"Company review failed for {company!r}: {exc}")
        return CompanyReview(
            company=company,
            summary="Could not synthesise a review.",
            sources=sources,
            reviewed_at=datetime.now(UTC),
        )

    logger.info(
        f"Company review for {company}: score={data.get('work_score')} "
        f"confidence={data.get('confidence')}"
    )
    return CompanyReview(
        company=company,
        work_score=_coerce_score(data.get("work_score")),
        summary=str(data.get("summary") or ""),
        pros=_as_str_list(data.get("pros")),
        cons=_as_str_list(data.get("cons")),
        employee_sentiment=_opt_str(data.get("employee_sentiment")),
        financial_health=_opt_str(data.get("financial_health")),
        growth=_opt_str(data.get("growth")),
        company_age=_opt_str(data.get("company_age")),
        confidence=str(data.get("confidence") or "low"),
        sources=sources[:8],
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
    return [str(v) for v in value if str(v).strip()]


def _opt_str(value: object) -> str | None:
    """Return a stripped string, or None for empty/null values."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None
