"""LLM-based company research and hiring manager discovery."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from loguru import logger

from job_scout.llm.base import LLMClient, LLMError
from job_scout.llm.factory import get_llm_client
from job_scout.models import (
    CompanyResearch,
    Config,
    HiringManagerSuggestion,
    JobListing,
)


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

    # Last resort: find the first '{' ... last '}' block
    brace_start = text.find("{")
    brace_end = text.rfind("}") + 1
    if brace_start != -1 and brace_end > brace_start:
        return json.loads(text[brace_start:brace_end])  # type: ignore[no-any-return]

    raise json.JSONDecodeError("No JSON object found", text, 0)


def _build_research_prompt(job: JobListing) -> str:
    """Build the prompt for researching a company.

    Args:
        job: The job listing containing company information.

    Returns:
        Complete prompt string.
    """
    job_desc = (job.description or "")[:3000]

    return f"""Research this company based on the job posting.
Respond ONLY with valid JSON.

COMPANY: {job.company}
JOB TITLE: {job.title}
LOCATION: {job.location or "Not specified"}
JOB DESCRIPTION:
{job_desc}

Analyze the job posting and provide:
1. industry: Likely industry/sector
   (e.g. "Software/Tech", "Finance")
2. company_size: Estimated company size
   ("startup", "small", "medium", "large", "enterprise")
3. culture_indicators: 3-5 culture/values indicators from
   the job description
4. tech_stack_hints: 2-4 technology hints from description
5. growth_signals: Brief string about company growth signals
6. research_notes: Brief 1-2 sentence summary

RESPOND WITH VALID JSON ONLY:
{{
  "industry": "...",
  "company_size": "...",
  "culture_indicators": ["...", "..."],
  "tech_stack_hints": ["...", "..."],
  "growth_signals": "...",
  "research_notes": "..."
}}"""


def research_company(job: JobListing, config: Config) -> CompanyResearch | None:
    """Research a company based on a job listing using LLM.

    Args:
        job: The job listing to research.
        config: Application configuration.

    Returns:
        CompanyResearch object, or None if research fails.
    """
    try:
        client = get_llm_client(config)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Cannot research company: {exc}")
        return None

    prompt = _build_research_prompt(job)

    try:
        response = client.complete(prompt, purpose="evaluation")
        data = _extract_json(response)

        hiring_managers = _suggest_hiring_managers(job, data, config, client)

        return CompanyResearch(
            company_name=job.company,
            industry=data.get("industry"),
            company_size=data.get("company_size"),
            culture_indicators=data.get("culture_indicators", []),
            tech_stack_hints=data.get("tech_stack_hints", []),
            growth_signals=data.get("growth_signals"),
            research_notes=data.get("research_notes", ""),
            hiring_managers=hiring_managers,
            research_timestamp=datetime.now(UTC),
        )
    except (json.JSONDecodeError, LLMError, KeyError) as exc:
        logger.debug(f"Company research failed for {job.company}: {exc}")
        return None


def _suggest_hiring_managers(
    job: JobListing,
    research_data: dict[str, Any],
    config: Config,
    client: LLMClient,
) -> list[HiringManagerSuggestion]:
    """Suggest likely hiring managers for a job posting.

    Uses LLM to generate suggestions based on job title, company, and
    research.

    Args:
        job: The job listing.
        research_data: Research data from company research.
        config: Application configuration.
        client: LLM client for querying.

    Returns:
        List of hiring manager suggestions.
    """
    prompt = f"""Based on this job posting, suggest 1-3 likely
hiring managers (names/roles).
Do NOT make up email addresses or LinkedIn URLs.
Only include them if you can infer them with high confidence.

COMPANY: {job.company}
JOB TITLE: {job.title}
JOB DESCRIPTION: {(job.description or "")[:2000]}
COMPANY RESEARCH: {json.dumps(research_data)}

For each suggestion, provide:
1. name: Full name (or best guess from common name patterns)
2. role: Likely role (e.g., "Engineering Manager", "Hiring Manager")
3. email: Email if inferable (e.g., firstname@company.com), otherwise null
4. linkedin_url: LinkedIn URL if inferable, otherwise null
5. confidence: 0-100 confidence in this suggestion
6. reasoning: Brief reason for this suggestion

RESPOND WITH VALID JSON ONLY - list of suggestions:
[
  {{
    "name": "...",
    "role": "...",
    "email": "..." or null,
    "linkedin_url": "..." or null,
    "confidence": 50-80,
    "reasoning": "..."
  }}
]"""

    try:
        response = client.complete(prompt, purpose="evaluation")
        suggestions_data = _extract_json(response)

        # Handle both dict (single) and list (multiple)
        if isinstance(suggestions_data, dict):
            suggestions_list: list[dict[str, Any]] = [suggestions_data]
        elif isinstance(suggestions_data, list):
            suggestions_list = suggestions_data
        else:
            return []

        suggestions: list[HiringManagerSuggestion] = []
        for item in suggestions_list[:3]:  # Limit to 3 suggestions
            try:
                suggestion = HiringManagerSuggestion(
                    name=item.get("name", "Unknown"),
                    role=item.get("role"),
                    email=item.get("email"),
                    linkedin_url=item.get("linkedin_url"),
                    confidence=item.get("confidence", 50),
                    reasoning=item.get("reasoning", ""),
                )
                suggestions.append(suggestion)
            except (KeyError, TypeError, ValueError):
                continue

        return suggestions
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"Hiring manager suggestion failed for {job.company}: {exc}")
        return []
