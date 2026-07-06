"""Cover letter and screening question generation for job applications."""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from job_scout.llm.base import LLMClient
from job_scout.models import CvProfile


def extract_screening_questions(
    job_description: str,
    *,
    client: LLMClient | None = None,
) -> list[str]:
    """Extract screening questions from a job description.

    Uses LLM to identify likely screening questions that may be asked during
    the application process.

    Args:
        job_description: The full job description text.
        client: LLM client to use; if None, one is built from config.

    Returns:
        List of screening questions extracted from the job description.

    Raises:
        ValueError: If LLM response cannot be parsed.
    """
    from job_scout.config import load_llm_config  # noqa: PLC0415
    from job_scout.llm.factory import get_llm_client  # noqa: PLC0415

    if client is None:
        client = get_llm_client(load_llm_config())

    prompt = (
        "Extract the most likely screening questions that a hiring manager would "
        "ask based on this job description. Focus on questions about required "
        "skills, experience, and specific requirements mentioned in the job posting. "
        "Respond ONLY with valid JSON.\n\n"
        f"JOB DESCRIPTION:\n{job_description[:2000]}\n\n"
        'Return valid JSON with format: {"questions": ["question1", "question2", ...]}'
    )

    try:
        response = client.complete(prompt, purpose="screening_questions")
        data = _parse_json_response(response)
        questions = data.get("questions", [])
        if not isinstance(questions, list):
            logger.warning(
                f"Expected questions list, got {type(questions)}: {questions}"
            )
            return []
        return [str(q).strip() for q in questions if q]
    except ValueError as e:
        logger.error(f"Failed to extract screening questions: {e}")
        return []


def generate_cover_letter(
    cv_profile: CvProfile,
    job_description: str,
    job_title: str,
    company_name: str,
    *,
    client: LLMClient | None = None,
) -> str:
    """Generate a tailored cover letter for a specific job.

    Uses LLM to create a professional cover letter that highlights relevant
    experience and skills from the CV profile.

    Args:
        cv_profile: Structured CV profile extracted from CV.
        job_description: The target job description.
        job_title: The target job title.
        company_name: The target company name.
        client: LLM client to use; if None, one is built from config.

    Returns:
        Generated cover letter text.

    Raises:
        ValueError: If LLM response cannot be parsed.
    """
    from job_scout.config import load_llm_config  # noqa: PLC0415
    from job_scout.llm.factory import get_llm_client  # noqa: PLC0415

    if client is None:
        client = get_llm_client(load_llm_config())

    profile_summary = _format_cv_profile_summary(cv_profile)

    prompt = (
        "You are an expert cover letter writer. Write a professional, compelling "
        "cover letter for a job application. The letter should:\n"
        "- Highlight the applicant's most relevant experience and skills\n"
        "- Show enthusiasm for the specific role and company\n"
        "- Be concise and professional (3-4 paragraphs)\n"
        "- Use a professional tone appropriate for Dutch business culture\n\n"
        f"APPLICANT PROFILE:\n{profile_summary}\n\n"
        f"TARGET POSITION: {job_title} at {company_name}\n\n"
        f"JOB DESCRIPTION:\n{job_description[:1500]}\n\n"
        "Write the cover letter as plain text, starting with a greeting and "
        "ending with a professional closing. Do NOT include placeholders or "
        "bracketed fields like [Your Name] - write as if the applicant is "
        "already known to be applying."
    )

    try:
        response = client.complete(prompt, purpose="cover_letter")
        cover_letter = response.strip()
        logger.debug(f"Generated cover letter: {len(cover_letter)} chars")
        return cover_letter
    except Exception as e:
        logger.error(f"Failed to generate cover letter: {e}")
        return ""


def answer_screening_questions(
    questions: list[str],
    cv_profile: CvProfile,
    job_description: str,
    *,
    client: LLMClient | None = None,
) -> dict[str, str]:
    """Generate answers to screening questions.

    Uses LLM to create thoughtful answers to screening questions based on
    the applicant's CV profile and the job requirements.

    Args:
        questions: List of screening questions to answer.
        cv_profile: Structured CV profile extracted from CV.
        job_description: The target job description.
        client: LLM client to use; if None, one is built from config.

    Returns:
        Dictionary mapping questions to answers.

    Raises:
        ValueError: If LLM response cannot be parsed.
    """
    from job_scout.config import load_llm_config  # noqa: PLC0415
    from job_scout.llm.factory import get_llm_client  # noqa: PLC0415

    if client is None:
        client = get_llm_client(load_llm_config())

    if not questions:
        return {}

    profile_summary = _format_cv_profile_summary(cv_profile)
    questions_str = "\n".join([f"{i + 1}. {q}" for i, q in enumerate(questions)])

    prompt = (
        "You are an expert at helping job applicants answer screening questions. "
        "Generate thoughtful, honest, and professional answers to the following "
        "screening questions. The answers should be based on the applicant's "
        "background and demonstrate fit for the role. Each answer should be "
        "2-3 sentences, concise but substantive. Respond ONLY with valid JSON.\n\n"
        f"APPLICANT PROFILE:\n{profile_summary}\n\n"
        f"JOB DESCRIPTION:\n{job_description[:1000]}\n\n"
        f"SCREENING QUESTIONS:\n{questions_str}\n\n"
        'Return valid JSON with format: {"answers": {"question1": "answer1", '
        '"question2": "answer2", ...}}'
    )

    try:
        response = client.complete(prompt, purpose="screening_answers")
        data = _parse_json_response(response)
        answers_dict = data.get("answers", {})
        if not isinstance(answers_dict, dict):
            logger.warning(
                f"Expected answers dict, got {type(answers_dict)}: {answers_dict}"
            )
            return {}
        # Map original questions to answers
        result = {}
        for q in questions:
            # Try to find answer by exact match or by index
            if q in answers_dict:
                result[q] = str(answers_dict[q]).strip()
        return result
    except Exception as e:
        logger.error(f"Failed to generate screening answers: {e}")
        return {}


def _format_cv_profile_summary(profile: CvProfile) -> str:
    """Format CvProfile as a readable summary for the LLM context.

    Args:
        profile: Structured CV profile.

    Returns:
        Formatted profile summary text.
    """
    parts = []

    if profile.skills:
        parts.append(f"Skills: {', '.join(profile.skills)}")

    if profile.years_experience is not None:
        parts.append(f"Experience: {profile.years_experience} years")

    if profile.past_roles:
        roles_str = "; ".join(
            [
                f"{role.title} at {role.company} ({role.start_date} - {role.end_date})"
                if role.start_date
                else f"{role.title} at {role.company}"
                for role in profile.past_roles
                if role.title and role.company
            ]
        )
        if roles_str:
            parts.append(f"Past roles: {roles_str}")

    if profile.education:
        parts.append(f"Education: {', '.join(profile.education)}")

    return "\n".join(parts) if parts else "(Profile data unavailable)"


def _parse_json_response(response: str) -> dict[str, Any]:
    """Parse JSON from LLM response, handling markdown fences.

    Args:
        response: Raw response text from LLM.

    Returns:
        Parsed JSON dictionary.

    Raises:
        ValueError: If no valid JSON found.
    """
    text = response.strip()

    # Try markdown fences first
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

    # Find first {...} block (handles preamble text)
    brace_start = text.find("{")
    brace_end = text.rfind("}") + 1

    if brace_start >= 0 and brace_end > brace_start:
        try:
            return json.loads(text[brace_start:brace_end])  # type: ignore[no-any-return]
        except json.JSONDecodeError:
            pass

    raise ValueError("No JSON object found in LLM response")
