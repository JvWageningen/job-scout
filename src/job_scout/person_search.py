"""Supplementary CV data via public web search on a person's full name.

Answers "what else is publicly known about this person?" to help fill gaps a
CV or LinkedIn import missed. Unlike LinkedIn import (the person's own
authoritative data), a name search can easily surface the wrong person, so
results are always meant for review before merging into a CvProfile -- never
applied automatically.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from loguru import logger

from job_scout.evaluator import _extract_json
from job_scout.llm.base import LLMError
from job_scout.models import CvRole, PersonSearchResult
from job_scout.websearch import web_search

if TYPE_CHECKING:
    from job_scout.llm.base import LLMClient

_MAX_SNIPPETS = 18
_MAX_EVIDENCE_CHARS = 4500


def _evidence_queries(full_name: str, known_context: str | None) -> list[str]:
    """Build the search queries used to gather evidence about a named person.

    Args:
        full_name: The person's full name.
        known_context: Optional disambiguating detail.

    Returns:
        List of search query strings.
    """
    queries = [
        f'"{full_name}" LinkedIn',
        f'"{full_name}" CV OR resume OR profile',
        f'"{full_name}" werkzaam OR functie OR ervaring',
    ]
    if known_context:
        queries.append(f'"{full_name}" {known_context}')
    return queries


def gather_person_evidence(
    full_name: str,
    *,
    known_context: str | None = None,
    timeout: int = 15,
    searxng_url: str | None = None,
    api_key: str | None = None,
) -> tuple[list[str], list[str]]:
    """Collect public search snippets and source URLs for a person's name.

    Args:
        full_name: The person's full name to search for.
        known_context: Optional disambiguating detail (e.g. current employer
            or city) appended to one query to reduce same-name collisions.
        timeout: Per-search timeout in seconds.
        searxng_url: Optional SearXNG instance URL for reliable search.
        api_key: Optional Brave Search API key for reliable search.

    Returns:
        Tuple of (evidence snippets, source URLs).
    """
    snippets: list[str] = []
    sources: list[str] = []
    for query in _evidence_queries(full_name, known_context):
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


def _build_person_prompt(
    full_name: str, evidence: str, known_context: str | None
) -> str:
    """Build the LLM prompt for extracting candidate CV additions about a person.

    Args:
        full_name: The person's full name.
        evidence: Formatted web-search evidence.
        known_context: Optional disambiguating detail.

    Returns:
        The prompt string.
    """
    context_line = (
        f"Known context to help disambiguate (current/recent employer, city, "
        f"etc.): {known_context}\n"
        if known_context
        else ""
    )
    return f"""You help identify PUBLIC professional facts about a named person, to
suggest possible additions to their CV. This is NOT their own authoritative data --
it is inferred from public web search, so a name collision with a different person
is a real risk. Respond ONLY with JSON.

FULL NAME: {full_name}
{context_line}
PUBLIC WEB EVIDENCE (search snippets):
{evidence or "(no useful public information was found)"}

Rules:
- Only include a fact if you are reasonably confident it refers to THIS specific
  person, using the known context (if given) to disambiguate from same-named
  others. If the evidence is ambiguous or clearly about a different person,
  leave the corresponding list(s) empty rather than guessing.
- Do NOT invent skills, employers, or dates that aren't supported by the evidence.
- If "{full_name}" is a common name and the evidence doesn't clearly narrow it
  down, say so in "notes" and set confidence to "low".

Respond with this exact JSON structure:
{{
  "skills": ["<skill found in evidence, if any>", ...],
  "education": ["<institution, with dates if known>", ...],
  "past_roles": [
    {{"title": "<job title>", "company": "<company>", "start_date": "<or null>",
      "end_date": "<or null>", "description": null}}
  ],
  "summary": "<1-2 sentences: what was found, or why nothing reliable was found>",
  "confidence": "<low|medium|high -- how uniquely the evidence identifies them>",
  "notes": "<caveats, e.g. common-name ambiguity, or empty string>"
}}"""


def search_person(
    full_name: str,
    *,
    known_context: str | None = None,
    client: LLMClient,
    timeout: int = 15,
    searxng_url: str | None = None,
    api_key: str | None = None,
) -> PersonSearchResult:
    """Search the public web for a named person and extract candidate CV additions.

    Args:
        full_name: The person's full name to search for.
        known_context: Optional disambiguating detail (e.g. current employer
            or city) to reduce same-name collisions.
        client: LLM client used to synthesise the result.
        timeout: Per-search timeout in seconds.
        searxng_url: Optional SearXNG instance URL for reliable search.
        api_key: Optional Brave Search API key for reliable search.

    Returns:
        A PersonSearchResult; low-confidence and empty lists when nothing
        reliable was found or the name is too ambiguous.
    """
    from datetime import UTC, datetime  # noqa: PLC0415

    if not full_name:
        return PersonSearchResult(full_name=full_name, notes="No name given.")

    snippets, sources = gather_person_evidence(
        full_name,
        known_context=known_context,
        timeout=timeout,
        searxng_url=searxng_url,
        api_key=api_key,
    )
    evidence = "\n".join(f"- {s}" for s in snippets)[:_MAX_EVIDENCE_CHARS]
    prompt = _build_person_prompt(full_name, evidence, known_context)
    try:
        raw = client.complete(prompt, purpose="evaluation")
        data = _extract_json(raw)
    except (LLMError, json.JSONDecodeError, ValueError) as exc:
        logger.warning(f"Person search failed for {full_name!r}: {exc}")
        return PersonSearchResult(
            full_name=full_name,
            summary="Could not synthesise a result.",
            sources=sources,
            searched_at=datetime.now(UTC),
        )

    logger.info(
        f"Person search for {full_name}: confidence={data.get('confidence')} "
        f"roles={len(data.get('past_roles') or [])}"
    )
    return PersonSearchResult(
        full_name=full_name,
        skills=_as_str_list(data.get("skills")),
        education=_as_str_list(data.get("education")),
        past_roles=_as_roles(data.get("past_roles")),
        summary=str(data.get("summary") or ""),
        confidence=str(data.get("confidence") or "low"),
        notes=str(data.get("notes") or ""),
        sources=sources[:8],
        searched_at=datetime.now(UTC),
    )


def _as_str_list(value: object) -> list[str]:
    """Coerce a value into a list of non-empty strings.

    Args:
        value: Raw value from the LLM response.

    Returns:
        List of non-empty strings (empty when the value is not a list).
    """
    if not isinstance(value, list):
        return []
    return [str(v) for v in value if str(v).strip()]


def _as_roles(value: object) -> list[CvRole]:
    """Coerce a value into validated CvRole entries, skipping malformed ones.

    Args:
        value: Raw value from the LLM response.

    Returns:
        List of CvRole (empty when the value is not a usable list).
    """
    if not isinstance(value, list):
        return []
    roles: list[CvRole] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        title, company = item.get("title"), item.get("company")
        if not title or not company:
            continue
        roles.append(
            CvRole(
                title=str(title),
                company=str(company),
                start_date=_opt_str(item.get("start_date")),
                end_date=_opt_str(item.get("end_date")),
                description=_opt_str(item.get("description")),
            )
        )
    return roles


def _opt_str(value: object) -> str | None:
    """Return a stripped string, or None for empty/null values.

    Args:
        value: Raw value from the LLM response.

    Returns:
        Stripped string, or None.
    """
    if value is None:
        return None
    text = str(value).strip()
    return text or None
