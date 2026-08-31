"""Critique a CV or a motivational letter and say how to improve it.

Separate from ``cover_letter_generator`` and ``resume_tailor``, which write
documents. This reads a document the candidate already has and reports what
is weak about it, because reacting to specific criticism is far easier than
rewriting from a blank page.

A CV can be judged on its own merits or against one vacancy. A motivational
letter is always judged against a vacancy: "is this a good letter" is not a
meaningful question without the job it is answering.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from loguru import logger
from pydantic import BaseModel, Field

from job_scout.evaluator import _extract_json
from job_scout.llm.base import LLMError

if TYPE_CHECKING:
    from job_scout.llm.base import LLMClient
    from job_scout.models import CvProfile, JobListing

# Keeps one weak section from burying the rest of the review.
_MAX_POINTS = 8

# Long documents are truncated rather than refused; the opening of a CV or
# letter carries most of what the review is about.
_MAX_DOC_CHARS = 6000
_MAX_JOB_CHARS = 2500


class FeedbackPoint(BaseModel):
    """One specific, actionable observation about a document."""

    section: str = ""
    severity: str = "suggestion"  # blocker / important / suggestion
    issue: str
    suggestion: str = ""
    example: str = ""


class DocumentFeedback(BaseModel):
    """A full review of one document."""

    score: int | None = None  # 0-100, None when the LLM would not commit
    summary: str = ""
    strengths: list[str] = Field(default_factory=list)
    points: list[FeedbackPoint] = Field(default_factory=list)
    missing_keywords: list[str] = Field(default_factory=list)
    target: str = ""  # what it was judged against, for display


_SCHEMA = """{
  "score": <0-100 integer, or null>,
  "summary": "<2-3 sentences: the honest headline verdict>",
  "strengths": ["<what genuinely works, 2-4 items>"],
  "points": [
    {
      "section": "<which part of the document>",
      "severity": "blocker|important|suggestion",
      "issue": "<what is wrong, concretely>",
      "suggestion": "<what to do instead>",
      "example": "<a rewritten line if useful, else empty>"
    }
  ],
  "missing_keywords": ["<terms the job asks for that the document never uses>"]
}"""


def _job_context(job: JobListing) -> str:
    """Render a vacancy as prompt context.

    Args:
        job: The job to describe.

    Returns:
        A compact plain-text description.
    """
    description = (job.description or "")[:_MAX_JOB_CHARS]
    return (
        f"TARGET VACANCY: {job.title} at {job.company}\n"
        f"Location: {job.location or 'unknown'}\n\n"
        f"DESCRIPTION:\n{description}"
    )


def _profile_context(profile: CvProfile | None) -> str:
    """Render the parsed CV profile as prompt context.

    Args:
        profile: Parsed profile, if available.

    Returns:
        A compact summary, or a note that none was available.
    """
    if profile is None:
        return "(no parsed profile available)"
    roles = "; ".join(
        f"{r.title} at {r.company}" + (" (current)" if not r.end_date else "")
        for r in profile.past_roles[-6:]
    )
    return (
        f"Skills: {', '.join(profile.skills[:25]) or 'unknown'}\n"
        f"Education: {', '.join(profile.education[:4]) or 'unknown'}\n"
        f"Roles: {roles or 'unknown'}\n"
        f"Years of experience: {profile.years_experience or 'unknown'}"
    )


def _build_cv_prompt(
    cv_text: str, profile: CvProfile | None, job: JobListing | None
) -> str:
    """Build the prompt for reviewing a CV.

    Args:
        cv_text: Raw extracted CV text.
        profile: Parsed profile for extra context.
        job: Vacancy to judge against, or None for a general review.

    Returns:
        The complete prompt.
    """
    if job is None:
        framing = (
            "Review this CV on its own merits, for the Dutch job market. Judge "
            "clarity, structure, how well achievements are evidenced, and "
            "whether a recruiter skimming for 20 seconds would understand what "
            "this person does. Leave missing_keywords empty."
        )
        context = ""
    else:
        framing = (
            "Review this CV specifically as an application for the vacancy "
            "below. Judge how well it evidences what this vacancy asks for, "
            "what a reader would doubt, and what is buried or missing. In "
            "missing_keywords, list terms the vacancy asks for that the CV "
            "never uses."
        )
        context = f"\n{_job_context(job)}\n"

    return f"""You are a blunt but constructive Dutch-market recruiter.
{framing}

Be specific. "Add more detail" is useless; name the bullet and say what it
should say instead. Do not invent achievements the candidate has not claimed.
Order points with the most damaging first. At most {_MAX_POINTS} points.

PARSED PROFILE:
{_profile_context(profile)}
{context}
CV TEXT:
{cv_text[:_MAX_DOC_CHARS]}

Respond ONLY with JSON in exactly this shape:
{_SCHEMA}"""


def _build_letter_prompt(
    letter_text: str, job: JobListing, profile: CvProfile | None
) -> str:
    """Build the prompt for reviewing a motivational letter.

    Args:
        letter_text: The letter to review.
        job: The vacancy the letter is for.
        profile: Parsed CV profile for cross-checking claims.

    Returns:
        The complete prompt.
    """
    return f"""You are a blunt but constructive Dutch-market recruiter reviewing
a motivational letter (motivatiebrief) written for one specific vacancy.

Judge whether it answers "why this role, why this employer, why you", whether
it evidences its claims rather than asserting them, whether it repeats the CV
instead of adding to it, and whether the tone fits Dutch business culture --
neither American-style overselling nor apologetic understatement. Flag any
generic sentence that could be sent to any employer unchanged.

Be specific: quote the weak sentence and give a replacement. Do not invent
achievements. Order points with the most damaging first. At most {_MAX_POINTS}
points. In missing_keywords, list things the vacancy clearly asks about that
the letter never addresses.

{_job_context(job)}

THE CANDIDATE'S BACKGROUND (for checking claims are grounded):
{_profile_context(profile)}

THE LETTER:
{letter_text[:_MAX_DOC_CHARS]}

Respond ONLY with JSON in exactly this shape:
{_SCHEMA}"""


def _run(prompt: str, client: LLMClient, target: str) -> DocumentFeedback:
    """Send a review prompt and parse the response.

    Args:
        prompt: The prompt to send.
        client: LLM client.
        target: What the document was judged against, for display.

    Returns:
        Parsed feedback. On any LLM or parsing failure an empty review is
        returned with the reason in ``summary``, so the dashboard shows a
        message rather than an error page.
    """
    try:
        raw = client.complete(prompt, purpose="evaluation")
        data: dict[str, Any] = _extract_json(raw)
    except (LLMError, json.JSONDecodeError, ValueError) as exc:
        logger.error(f"Document review failed: {exc}")
        return DocumentFeedback(
            summary="Could not generate feedback. Check the LLM settings.",
            target=target,
        )

    points = [
        FeedbackPoint(**p)
        for p in (data.get("points") or [])
        if isinstance(p, dict) and p.get("issue")
    ][:_MAX_POINTS]

    return DocumentFeedback(
        score=data.get("score") if isinstance(data.get("score"), int) else None,
        summary=str(data.get("summary") or ""),
        strengths=[str(s) for s in (data.get("strengths") or []) if s][:6],
        points=points,
        missing_keywords=[str(k) for k in (data.get("missing_keywords") or []) if k][
            :12
        ],
        target=target,
    )


def review_cv(
    cv_text: str,
    *,
    profile: CvProfile | None = None,
    job: JobListing | None = None,
    client: LLMClient,
) -> DocumentFeedback:
    """Review a CV, generally or against one vacancy.

    Args:
        cv_text: Raw extracted CV text.
        profile: Parsed profile for extra context.
        job: Vacancy to judge against; None reviews the CV on its own merits.
        client: LLM client.

    Returns:
        The review.

    Raises:
        ValueError: If the CV text is empty.
    """
    if not cv_text.strip():
        raise ValueError("No CV text to review.")
    target = f"{job.title} at {job.company}" if job else "General review"
    return _run(_build_cv_prompt(cv_text, profile, job), client, target)


def review_cover_letter(
    letter_text: str,
    job: JobListing,
    *,
    profile: CvProfile | None = None,
    client: LLMClient,
) -> DocumentFeedback:
    """Review a motivational letter against the vacancy it was written for.

    Args:
        letter_text: The letter to review.
        job: The vacancy the letter targets.
        profile: Parsed CV profile for cross-checking claims.
        client: LLM client.

    Returns:
        The review.

    Raises:
        ValueError: If the letter is empty.
    """
    if not letter_text.strip():
        raise ValueError("No letter text to review.")
    return _run(
        _build_letter_prompt(letter_text, job, profile),
        client,
        f"{job.title} at {job.company}",
    )
