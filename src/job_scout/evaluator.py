"""LLM integration for job evaluation and keyword generation."""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from job_scout.llm.base import LLMClient, LLMError
from job_scout.llm.factory import get_llm_client
from job_scout.models import (
    CompensationEvaluation,
    Config,
    FitEvaluation,
    JobListing,
    KeywordsResult,
    NegativeEvaluation,
)


def check_llm_available(config: Config) -> tuple[bool, str | None]:
    """Check whether the configured LLM provider is ready to use.

    Args:
        config: Application configuration.

    Returns:
        (True, None) if available, (False, error_message) otherwise.
    """
    try:
        client = get_llm_client(config)
    except LLMError as exc:
        return False, str(exc)
    return client.check_available()


def _extract_json(text: str) -> dict[str, Any]:
    """Extract a JSON object from LLM output, stripping markdown fences.

    Args:
        text: Raw text that may contain a fenced JSON block.

    Returns:
        Parsed dictionary.

    Raises:
        json.JSONDecodeError: If no valid JSON can be found.
    """
    if "```json" in text:
        start = text.index("```json") + 7
        end = text.index("```", start)
        text = text[start:end].strip()
    elif "```" in text:
        start = text.index("```") + 3
        end = text.index("```", start)
        text = text[start:end].strip()

    try:
        return json.loads(text)  # type: ignore[no-any-return]
    except json.JSONDecodeError:
        pass

    # Last resort: find the first '{' ... last '}' block (handles preamble text)
    brace_start = text.find("{")
    brace_end = text.rfind("}") + 1
    if brace_start != -1 and brace_end > brace_start:
        return json.loads(text[brace_start:brace_end])  # type: ignore[no-any-return]

    raise json.JSONDecodeError("No JSON object found", text, 0)


def _build_fit_prompt(
    job: JobListing, profile: str, cv_text: str, negative_desc: str
) -> str:
    """Build the prompt for evaluating a job listing.

    Args:
        job: The job listing to evaluate.
        profile: Candidate's profile description.
        cv_text: Extracted text from the candidate's CV.
        negative_desc: Description of roles/criteria to reject.

    Returns:
        Complete prompt string.
    """
    job_text = (
        f"Title: {job.title}\n"
        f"Company: {job.company}\n"
        f"Location: {job.location or 'Not specified'}\n"
        f"Description:\n{(job.description or '')[:2000]}"
    )
    return f"""Evaluate this job listing for a candidate. Respond ONLY with valid JSON.

CANDIDATE PROFILE:
{profile}

CANDIDATE CV (excerpt):
{cv_text[:3000]}

NEGATIVE CRITERIA (roles to reject):
{negative_desc}

JOB LISTING:
{job_text}

Evaluate this job. Respond with this exact JSON structure:
{{
  "fit_score": <integer 0-100>,
  "fit_reasoning": "<one concise sentence>",
  "matches_negative": <true or false>,
  "negative_reasoning": "<one concise sentence>",
  "salary_min": <integer or null>,
  "salary_max": <integer or null>,
  "salary_period": "<monthly or yearly or null>",
  "vacation_days": <integer or null>,
  "compensation_reasoning": "<one concise sentence>"
}}

fit_score guidelines:
- 0 = completely irrelevant role (different field entirely)
- 40-59 = partial match (related field but different focus)
- 60-79 = good fit (right role, minor gaps are OK)
- 80-100 = strong/perfect match
Be LENIENT on experience gaps: if the role fits but asks for \
1-3 more years of experience than the candidate has, deduct only \
5-15 points — do NOT reject for experience alone. \
Focus on role relevance and transferable skills over exact \
experience requirements.
matches_negative: true if this job matches the negative criteria
salary_min/salary_max: gross salary in EUR if stated or estimable \
from the description, null if unknown. Always normalize to monthly \
amounts (divide yearly by 12). If only one number is given, use it \
for both min and max.
salary_period: "monthly" or "yearly" — always "monthly" after \
normalization
vacation_days: annual vacation days if mentioned, null if unknown. \
The Dutch legal minimum is 20 days. If the listing says \
"marktconform" or similar, estimate based on industry norms."""


def _build_quick_eval_prompt(job: JobListing, profile: str, cv_text: str) -> str:
    """Build a short prompt for the quick first-pass fit score.

    Args:
        job: The job listing to evaluate.
        profile: Candidate's profile description.
        cv_text: Extracted text from the candidate's CV.

    Returns:
        Complete prompt string.
    """
    job_text = (
        f"Title: {job.title}\n"
        f"Company: {job.company}\n"
        f"Location: {job.location or 'Not specified'}\n"
        f"Description:\n{(job.description or '')[:1000]}"
    )
    return f"""Give a quick fit score (0-100) for this job vs the candidate. \
Respond ONLY with JSON.

CANDIDATE PROFILE:
{profile[:800]}

CV (excerpt):
{cv_text[:600]}

JOB:
{job_text}

Respond with: {{"fit_score": <integer 0-100>}}
fit_score: 0=irrelevant, 40-59=partial match, 60-79=good fit, 80-100=strong match."""


def quick_evaluate_fit(
    job: JobListing,
    profile: str,
    cv_text: str,
    *,
    client: LLMClient | None = None,
) -> int:
    """Run a cheap first-pass fit score for a job.

    Uses the quick_eval model (glm-4.7-flash by default) and a short prompt
    to produce a fit_score only — no reasoning, no compensation data.

    Args:
        job: Job listing to evaluate.
        profile: Candidate's profile description.
        cv_text: Extracted text from the candidate's CV.
        client: LLM client to use; if None, one is built from config.

    Returns:
        Fit score 0-100; 0 on parse or LLM failure.
    """
    from job_scout.config import load_llm_config  # noqa: PLC0415

    if client is None:
        client = get_llm_client(load_llm_config())

    prompt = _build_quick_eval_prompt(job, profile, cv_text)
    try:
        output = client.complete(prompt, purpose="quick_eval")
        data = _extract_json(output)
        return int(data.get("fit_score", 0))
    except (json.JSONDecodeError, LLMError, ValueError) as exc:
        logger.warning(f"Quick eval failed for {job.title!r}: {exc}")
        return 0


def evaluate_fit(
    job: JobListing,
    profile: str,
    cv_text: str,
    negative_desc: str,
    *,
    client: LLMClient | None = None,
) -> tuple[FitEvaluation, NegativeEvaluation, CompensationEvaluation]:
    """Evaluate a job's fit using the configured LLM provider.

    Args:
        job: Job listing to evaluate.
        profile: Candidate's profile description.
        cv_text: Extracted text from the candidate's CV.
        negative_desc: Description of roles/criteria to reject.
        client: LLM client to use; if None, one is built from config.

    Returns:
        Tuple of (FitEvaluation, NegativeEvaluation, CompensationEvaluation).

    Raises:
        LLMError: If the LLM provider is not available.
    """
    from job_scout.config import load_llm_config  # noqa: PLC0415

    if client is None:
        client = get_llm_client(load_llm_config())

    prompt = _build_fit_prompt(job, profile, cv_text, negative_desc)
    default_comp = CompensationEvaluation()

    try:
        output = client.complete(prompt, purpose="evaluation")
        data = _extract_json(output)
        fit = FitEvaluation(
            fit_score=int(data.get("fit_score", 0)),
            reasoning=str(data.get("fit_reasoning", "No reasoning provided")),
        )
        neg = NegativeEvaluation(
            matches_negative=bool(data.get("matches_negative", False)),
            reasoning=str(data.get("negative_reasoning", "No reasoning provided")),
        )
        comp = CompensationEvaluation(
            salary_min=_safe_int(data.get("salary_min")),
            salary_max=_safe_int(data.get("salary_max")),
            salary_period=data.get("salary_period"),
            vacation_days=_safe_int(data.get("vacation_days")),
            reasoning=str(data.get("compensation_reasoning", "")),
        )
        return fit, neg, comp
    except json.JSONDecodeError as e:
        logger.warning(f"Failed to parse LLM JSON: {e}")
        return (
            FitEvaluation(fit_score=0, reasoning="Evaluation parse error"),
            NegativeEvaluation(matches_negative=False, reasoning="Parse error"),
            default_comp,
        )
    except LLMError as e:
        logger.warning(f"LLM call failed during job evaluation: {e}")
        return (
            FitEvaluation(fit_score=0, reasoning="Evaluation failed"),
            NegativeEvaluation(matches_negative=False, reasoning="LLM error"),
            default_comp,
        )


def _safe_int(val: object) -> int | None:
    """Convert a value to int, returning None for non-numeric values.

    Args:
        val: Value to convert.

    Returns:
        Integer value, or None if conversion fails.
    """
    if val is None:
        return None
    try:
        return int(str(val))
    except (ValueError, TypeError):
        return None


def generate_keywords(
    profile: str,
    cv_text: str,
    *,
    client: LLMClient | None = None,
) -> KeywordsResult:
    """Generate Dutch and English job search keywords using the configured LLM.

    Args:
        profile: Candidate's profile description.
        cv_text: Extracted text from the candidate's CV.
        client: LLM client to use; if None, one is built from config.

    Returns:
        KeywordsResult with dutch and english keyword lists.
    """
    from job_scout.config import load_llm_config  # noqa: PLC0415

    if client is None:
        client = get_llm_client(load_llm_config())

    prompt = (
        "Based on the following profile and CV, generate job search keywords "
        "for the Dutch job market. Respond ONLY with valid JSON.\n\n"
        f"PROFILE:\n{profile}\n\n"
        f"CV (excerpt):\n{cv_text[:3000]}\n\n"
        "Generate:\n"
        "1. 5-10 Dutch search keywords (multi-word phrases for job board search)\n"
        "2. 5-10 English search keywords (multi-word phrases for job board search)\n"
        "3. 15-25 title_include keywords: short words/fragments that "
        "SHOULD appear in relevant job titles (e.g. 'CRO', 'conversie', "
        "'conversion', 'optimalisatie', 'analyst', 'marketeer'). These are "
        "single words or short fragments, NOT full job titles. "
        "Be broad enough to catch relevant jobs.\n"
        "4. 10-15 title_exclude keywords: words that indicate clearly IRRELEVANT jobs "
        "(e.g. 'SAP', 'payroll', 'accountant', 'chauffeur', 'verpleeg'). Only exclude "
        "roles that are obviously unrelated.\n\n"
        "Respond with this exact JSON structure:\n"
        '{{"dutch": [...], "english": [...], '
        '"title_include": [...], "title_exclude": [...]}}'
    )

    try:
        output = client.complete(prompt, purpose="keywords", timeout=120)
        data = _extract_json(output)
        return KeywordsResult(
            dutch=data.get("dutch", []),
            english=data.get("english", []),
            title_include=data.get("title_include", []),
            title_exclude=data.get("title_exclude", []),
        )
    except (json.JSONDecodeError, KeyError) as e:
        logger.error(f"Failed to parse keyword generation output: {e}")
        return KeywordsResult(dutch=[], english=[])
