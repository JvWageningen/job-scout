"""Resume tailoring and PDF generation for job applications."""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from typing import Any

from loguru import logger
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from job_scout.llm.base import LLMClient
from job_scout.models import CvProfile


def extract_resume_keywords(
    job_description: str,
    *,
    client: LLMClient | None = None,
) -> list[str]:
    """Extract high-value keywords from a job description for resume tailoring.

    Uses LLM to identify keywords and skills that should be highlighted in a
    tailored resume.

    Args:
        job_description: The full job description text.
        client: LLM client to use; if None, one is built from config.

    Returns:
        List of keywords relevant for resume tailoring.

    Raises:
        ValueError: If LLM response cannot be parsed.
    """
    from job_scout.config import load_llm_config  # noqa: PLC0415
    from job_scout.llm.factory import get_llm_client  # noqa: PLC0415

    if client is None:
        client = get_llm_client(load_llm_config())

    prompt = (
        "Extract the most important keywords and phrases from this job "
        "description that should be highlighted in a tailored resume. "
        "Focus on technical skills, frameworks, methodologies, and "
        "business-relevant terms. Respond ONLY with valid JSON.\n\n"
        f"JOB DESCRIPTION:\n{job_description[:2000]}\n\n"
        'Return valid JSON with format: {"keywords": ["keyword1", '
        '"keyword2", ...]}'
    )

    try:
        response = client.complete(prompt, purpose="resume_tailoring")
        data = _parse_json_response(response)
        keywords = data.get("keywords", [])
        if not isinstance(keywords, list):
            logger.warning(f"Expected keywords list, got {type(keywords)}: {keywords}")
            return []
        return [str(k).strip() for k in keywords if k]
    except ValueError as e:
        logger.error(f"Failed to extract keywords from job description: {e}")
        return []


def tailor_resume_text(
    cv_text: str,
    cv_profile: CvProfile,
    job_description: str,
    keywords: list[str] | None = None,
    *,
    client: LLMClient | None = None,
) -> str:
    """Tailor resume text to a specific job by highlighting relevant keywords.

    Uses LLM to intelligently inject job-relevant keywords into the resume
    while maintaining readability and ATS compatibility.

    Args:
        cv_text: Original CV/resume text content.
        cv_profile: Structured profile extracted from CV.
        job_description: The target job description.
        keywords: Pre-extracted keywords; if None, will be extracted.
        client: LLM client to use; if None, one is built from config.

    Returns:
        Tailored resume text with job-relevant keywords integrated.

    Raises:
        ValueError: If LLM response cannot be parsed.
    """
    from job_scout.config import load_llm_config  # noqa: PLC0415
    from job_scout.llm.factory import get_llm_client  # noqa: PLC0415

    if client is None:
        client = get_llm_client(load_llm_config())

    if not keywords:
        keywords = extract_resume_keywords(job_description, client=client)

    keywords_str = ", ".join(keywords[:15])  # Limit to top 15 for token efficiency

    # Build a concise profile summary from CvProfile
    profile_summary = _format_cv_profile_summary(cv_profile)

    prompt = (
        "You are an expert resume writer. Tailor the following CV/resume to "
        "better match the target job description by strategically highlighting "
        "relevant experience and skills. Maintain the original structure and "
        "professional tone. Do NOT add false experience or exaggerate. "
        "Naturally integrate these keywords where they truthfully apply:\n\n"
        f"KEY TERMS TO HIGHLIGHT:\n{keywords_str}\n\n"
        f"PROFILE SUMMARY:\n{profile_summary}\n\n"
        f"ORIGINAL CV/RESUME:\n{cv_text[:2500]}\n\n"
        f"TARGET JOB DESCRIPTION:\n{job_description[:1500]}\n\n"
        "Provide the tailored resume as plain text. Keep formatting simple "
        "(no special characters, plain text only for ATS compatibility)."
    )

    try:
        response = client.complete(prompt, purpose="resume_tailoring")
        tailored = response.strip()
        logger.debug(
            f"Tailored resume: {len(tailored)} chars from original {len(cv_text)}"
        )
        return tailored
    except Exception as e:
        logger.error(f"Failed to tailor resume: {e}")
        return cv_text  # Return original on failure


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
                f"{role.title} at {role.company}"
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
    if brace_start != -1 and brace_end > brace_start:
        try:
            return json.loads(text[brace_start:brace_end])  # type: ignore[no-any-return]
        except json.JSONDecodeError as e:
            raise ValueError(f"Could not parse JSON from response: {e}") from e

    raise ValueError("No JSON object found in LLM response")


def generate_resume_pdf(
    resume_text: str,
    output_path: str | Path | None = None,
) -> bytes | None:
    """Generate an ATS-safe PDF from resume text.

    Creates a clean, simple PDF suitable for ATS parsing with
    minimal formatting to ensure compatibility.

    Args:
        resume_text: The resume content as plain text.
        output_path: Optional path to save the PDF. If None, returns bytes.

    Returns:
        PDF file bytes if output_path is None, otherwise None.

    Raises:
        OSError: If PDF generation fails.
    """
    try:
        # Use in-memory buffer if no output path specified
        if output_path is None:
            pdf_buffer = BytesIO()
            buffer_path: str | BytesIO = pdf_buffer
        else:
            output_path = Path(output_path)
            buffer_path = str(output_path)

        # Create PDF with standard letter size
        doc = SimpleDocTemplate(
            buffer_path,
            pagesize=letter,
            rightMargin=0.75 * inch,
            leftMargin=0.75 * inch,
            topMargin=0.75 * inch,
            bottomMargin=0.75 * inch,
        )

        # Build story (content)
        story = []

        # Parse resume into lines and paragraphs
        lines = resume_text.split("\n")

        # Get default styles and create a simple ATS-safe style
        styles = getSampleStyleSheet()
        normal_style = ParagraphStyle(
            "ATSSafe",
            parent=styles["Normal"],
            fontSize=10,
            leading=12,
            fontName="Helvetica",  # Standard font for ATS compatibility
            leftIndent=0,
            rightIndent=0,
        )

        heading_style = ParagraphStyle(
            "ATSHeading",
            parent=styles["Heading1"],
            fontSize=12,
            leading=14,
            fontName="Helvetica-Bold",
            spaceAfter=6,
            leftIndent=0,
            rightIndent=0,
        )

        current_section: list[str] = []
        for line in lines:
            stripped = line.strip()

            if not stripped:
                # Empty line: add accumulated section and spacer
                if current_section:
                    for text in current_section:
                        story.append(Paragraph(text, normal_style))
                    story.append(Spacer(1, 0.1 * inch))
                    current_section = []
            elif stripped.isupper() and len(stripped.split()) <= 4:
                # Likely a section heading (all caps, short)
                if current_section:
                    for text in current_section:
                        story.append(Paragraph(text, normal_style))
                    story.append(Spacer(1, 0.1 * inch))
                    current_section = []
                story.append(Paragraph(stripped, heading_style))
            else:
                # Regular content line
                current_section.append(stripped)

        # Add any remaining content
        if current_section:
            for text in current_section:
                story.append(Paragraph(text, normal_style))

        # Build the PDF
        doc.build(story)

        if output_path is None:
            if isinstance(pdf_buffer, BytesIO):
                pdf_bytes = pdf_buffer.getvalue()
                logger.debug(f"Generated PDF: {len(pdf_bytes)} bytes")
                return pdf_bytes
        else:
            logger.debug(f"PDF saved to {output_path}")

        return None

    except Exception as e:
        logger.error(f"Failed to generate PDF: {e}")
        raise OSError(f"PDF generation failed: {e}") from e
