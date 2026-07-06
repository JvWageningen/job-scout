"""CV PDF parsing utilities."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast

from loguru import logger

from job_scout.llm.base import LLMClient
from job_scout.models import CvProfile, CvRole


def parse_cv(cv_path: str | Path) -> str:
    """Parse a CV PDF file and return its text content.

    Args:
        cv_path: Path to the PDF file.

    Returns:
        Extracted text content, or empty string on failure.

    Raises:
        FileNotFoundError: If the CV file does not exist.
    """
    path = Path(cv_path)
    if not path.exists():
        raise FileNotFoundError(
            f"CV file not found: {cv_path}\nRun 'job-scout init' to set your CV path."
        )

    try:
        import PyPDF2

        with path.open("rb") as f:
            reader = PyPDF2.PdfReader(f)
            pages = [
                page.extract_text() for page in reader.pages if page.extract_text()
            ]
        text = "\n".join(pages)
        logger.debug(f"Parsed CV: {len(text)} characters from {path.name}")
        return text
    except Exception as e:
        logger.warning(f"Failed to parse CV '{cv_path}': {e}")
        return ""


def _extract_json_from_response(text: str) -> dict[str, object]:
    """Extract JSON object from LLM output, stripping markdown fences.

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


def parse_cv_structured(raw_text: str, client: LLMClient) -> CvProfile:
    """Parse CV text using LLM and return structured CvProfile.

    Sends raw CV text to LLM for structured extraction of skills,
    experience, education, and past roles.

    Args:
        raw_text: Raw text extracted from the CV.
        client: LLMClient to use for structured parsing.

    Returns:
        Parsed CvProfile with skills, years_experience, education, past_roles.

    Raises:
        ValueError: If LLM response cannot be parsed as valid JSON.
    """
    prompt = (
        "Extract structured CV information and respond with JSON.\n"
        f"CV TEXT:\n{raw_text[:3000]}\n"
        "Return JSON with: skills, years_experience, education, past_roles.\n"
        "past_roles should be a list of objects with: title, company, "
        "start_date (YYYY-MM or similar), "
        "end_date (YYYY-MM or similar, or null if current), "
        "description (optional).\n"
        'Example: {"skills": ["Python"], "years_experience": 5, '
        '"education": [], "past_roles": '
        '[{"title": "Engineer", "company": "TechCorp", '
        '"start_date": "2020-01", "end_date": null, '
        '"description": null}]}'
    )

    response = client.complete(prompt, purpose="cv_parsing")
    data = _extract_json_from_response(response)

    # Parse past_roles into CvRole objects
    past_roles: list[CvRole] = []
    roles_data = cast(list[Any], data.get("past_roles", []) or [])
    for role_data in roles_data:
        if isinstance(role_data, dict):
            try:
                role = CvRole(
                    title=role_data.get("title", ""),
                    company=role_data.get("company", ""),
                    start_date=role_data.get("start_date"),
                    end_date=role_data.get("end_date"),
                    description=role_data.get("description"),
                )
                past_roles.append(role)
            except Exception as e:
                logger.warning(f"Failed to parse role: {role_data}, error: {e}")
        elif isinstance(role_data, str):
            # Backward compatibility: if it's a string, treat as title/company combined
            role = CvRole(title=role_data, company="")
            past_roles.append(role)

    # Validate and construct CvProfile
    profile = CvProfile(
        skills=data.get("skills", []) or [],
        years_experience=data.get("years_experience"),
        education=data.get("education", []) or [],
        past_roles=past_roles,
    )

    logger.debug(
        f"Parsed CV: {len(profile.skills)} skills, {profile.years_experience} years, "
        f"{len(profile.past_roles)} roles"
    )
    return profile


def compute_cv_hash(raw_text: str) -> str:
    """Compute SHA256 hash of CV text for caching purposes.

    Args:
        raw_text: Raw CV text.

    Returns:
        Hex string of SHA256 hash.
    """
    return hashlib.sha256(raw_text.encode()).hexdigest()
