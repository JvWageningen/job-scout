"""Interview preparation: STAR story bank and behavioral question matching."""

from __future__ import annotations

import json
from typing import Any

from loguru import logger

from job_scout.llm.base import LLMClient
from job_scout.models import BehavioralQuestion, InterviewPrep, StarStory


def extract_behavioral_questions(
    job_description: str,
    *,
    client: LLMClient | None = None,
) -> list[BehavioralQuestion]:
    """Extract behavioral questions likely to be asked for this job.

    Uses LLM to identify behavioral and situational questions that hiring managers
    typically ask based on the job description and required skills.

    Args:
        job_description: The full job description text.
        client: LLM client to use; if None, one is built from config.

    Returns:
        List of behavioral questions extracted from the job description.

    Raises:
        ValueError: If LLM response cannot be parsed.
    """
    from job_scout.config import load_llm_config  # noqa: PLC0415
    from job_scout.llm.factory import get_llm_client  # noqa: PLC0415

    if client is None:
        client = get_llm_client(load_llm_config())

    prompt = (
        "Extract 4-8 behavioral and situational interview questions that a hiring "
        "manager would likely ask based on this job description. Focus on questions "
        "about:\n"
        "- Handling challenging situations related to the required skills\n"
        "- Experience with the technologies or methodologies mentioned\n"
        "- Teamwork and communication in relevant contexts\n"
        "- Problem-solving and decision-making\n\n"
        f"JOB DESCRIPTION:\n{job_description[:3000]}\n\n"
        "Return ONLY valid JSON with format:\n"
        '{"questions": [\n'
        '  {"question": "Tell me...", "keywords": ["skill1", "skill2"]},\n'
        "  ...\n"
        "]}"
    )

    try:
        response = client.complete(prompt, purpose="behavioral_questions")
        data = _parse_json_response(response)
        questions = data.get("questions", [])
        if not isinstance(questions, list):
            logger.warning(
                f"Expected questions list, got {type(questions)}: {questions}"
            )
            return []

        result = []
        for item in questions:
            if isinstance(item, dict):
                question_text = item.get("question", "").strip()
                keywords = item.get("keywords", [])
                if question_text:
                    result.append(
                        BehavioralQuestion(
                            question=question_text,
                            keywords=(keywords if isinstance(keywords, list) else []),
                        )
                    )
        return result
    except (ValueError, json.JSONDecodeError) as e:
        logger.error(f"Failed to extract behavioral questions: {e}")
        return []


def match_stories_to_questions(
    questions: list[BehavioralQuestion],
    stories: list[StarStory],
) -> dict[str, list[StarStory]]:
    """Match STAR stories to behavioral questions based on keywords and content.

    Args:
        questions: List of behavioral questions to match.
        stories: List of available STAR stories.

    Returns:
        Dictionary mapping from question text to list of matched stories.
    """
    matched: dict[str, list[StarStory]] = {}

    for question in questions:
        question_keywords = set(keyword.lower() for keyword in question.keywords)
        matches: list[StarStory] = []

        for story in stories:
            story_keywords = set(keyword.lower() for keyword in story.keywords)

            # Calculate overlap between question and story keywords
            keyword_overlap = len(question_keywords & story_keywords)

            # Also consider if story action or result mentions key concepts
            story_text = f"{story.action} {story.result}".lower()
            keyword_mentions = sum(
                1 for keyword in question_keywords if keyword in story_text
            )

            # Match if there's significant overlap or mention
            relevance_score = keyword_overlap + (keyword_mentions * 0.5)
            if relevance_score > 0:
                matches.append(story)

        # Sort matches by number of relevant keywords (most relevant first)
        matches.sort(
            key=lambda s: len(set(kw.lower() for kw in s.keywords) & question_keywords),
            reverse=True,
        )

        matched[question.question] = matches

    return matched


def generate_interview_prep(
    job_description: str,
    stories: list[StarStory],
    job_id: int | None = None,
    *,
    client: LLMClient | None = None,
) -> InterviewPrep:
    """Generate interview preparation data for a job.

    Extracts behavioral questions from the job description and matches them to
    the candidate's STAR stories.

    Args:
        job_description: The full job description text.
        stories: List of available STAR stories for the candidate.
        job_id: Optional job listing ID to associate with this prep.
        client: LLM client to use; if None, one is built from config.

    Returns:
        InterviewPrep containing questions and matched stories.
    """
    questions = extract_behavioral_questions(job_description, client=client)

    matched_stories = match_stories_to_questions(questions, stories)

    return InterviewPrep(
        job_id=job_id,
        behavioral_questions=questions,
        matched_stories=matched_stories,
    )


def _parse_json_response(text: str) -> dict[str, Any]:
    """Parse JSON from LLM response, stripping markdown fences.

    Args:
        text: Raw LLM response text.

    Returns:
        Parsed JSON dictionary.

    Raises:
        json.JSONDecodeError: If valid JSON cannot be found.
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
        text = text[brace_start:brace_end]
        return json.loads(text)  # type: ignore[no-any-return]

    raise json.JSONDecodeError("No valid JSON found in response", text, 0)
