"""A guided intake that turns a vague sense of "something else" into tracks.

Writing a good profile description from a blank textarea is hard, especially
for someone who does not yet know what they want. The coach asks a short
series of questions instead, grounds them in whatever the CV already shows,
and proposes a handful of concrete directions to react to -- rejecting a
suggestion is far easier than inventing one.

Output is a set of CareerTrack objects the pipeline can search directly,
including ``blend`` tracks for interests that belong inside another role
rather than being a job of their own.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from loguru import logger
from pydantic import BaseModel, Field

from job_scout.evaluator import _extract_json
from job_scout.llm.base import LLMError
from job_scout.models import CareerTrack, CvProfile
from job_scout.tracks import slugify_track_id

if TYPE_CHECKING:
    from job_scout.llm.base import LLMClient

_MAX_TRACKS = 4


class CoachQuestion(BaseModel):
    """One question put to the candidate."""

    id: str
    question: str
    hint: str = ""
    options: list[str] = Field(default_factory=list)
    allow_unsure: bool = True


class CoachAnswer(BaseModel):
    """A candidate's reply to one question."""

    id: str
    answer: str


class CoachProposal(BaseModel):
    """The coach's proposed directions, for the candidate to confirm or edit."""

    tracks: list[CareerTrack] = Field(default_factory=list)
    summary: str = ""
    negative_description: str = ""
    follow_up: str = ""


def baseline_questions(cv: CvProfile | None) -> list[CoachQuestion]:
    """Return the opening questions, grounded in the CV when available.

    Args:
        cv: Parsed CV profile, used to offer concrete options rather than
            asking the candidate to invent answers from nothing.

    Returns:
        Questions to ask, in order.
    """
    recent = [r.title for r in (cv.past_roles if cv else [])][-3:]
    skills = (cv.skills if cv else [])[:8]
    return [
        CoachQuestion(
            id="direction",
            question=(
                "Roughly what kind of work are you after next? A vague answer "
                "is fine -- pick whatever feels closest."
            ),
            hint="You can pick more than one, or say you're not sure.",
            options=[
                "More of what I do now",
                "Same field, different focus",
                "A clear change of direction",
                "I really don't know yet",
            ],
        ),
        CoachQuestion(
            id="liked",
            question=(
                "Think of work you've genuinely enjoyed. What were you actually doing?"
            ),
            hint=(
                f"Recent roles on your CV: {', '.join(recent)}"
                if recent
                else "Any example counts, including outside paid work."
            ),
        ),
        CoachQuestion(
            id="disliked",
            question="What do you want to avoid in your next job?",
            hint="Things that would make you turn a role down.",
            options=[
                "Managing people",
                "Tracking hours on client projects",
                "Being one of thousands at a big corporate",
                "Purely commercial or sales targets",
                "Long commute",
            ],
        ),
        CoachQuestion(
            id="strengths",
            question="Which of your skills do you want to keep using?",
            hint=(
                f"From your CV: {', '.join(skills)}"
                if skills
                else "Whatever you'd be happy doing daily."
            ),
            options=skills,
        ),
        CoachQuestion(
            id="blend",
            question=(
                "Anything you'd like woven into the job without it becoming "
                "the whole job?"
            ),
            hint=(
                "For example some coding or AI tooling alongside the main "
                "work -- useful, but not a job you'd want full-time."
            ),
        ),
        CoachQuestion(
            id="constraints",
            question="Anything practical that has to be true?",
            hint="Travel distance, salary floor, hours, hybrid or on-site.",
        ),
    ]


def _format_cv(cv: CvProfile | None) -> str:
    """Render the CV as compact context for the prompt.

    Args:
        cv: Parsed CV profile.

    Returns:
        A short plain-text summary, or a placeholder when unavailable.
    """
    if not cv:
        return "(no CV available)"
    roles = "; ".join(
        f"{r.title} at {r.company}" + (" (current)" if not r.end_date else "")
        for r in cv.past_roles[-5:]
    )
    return (
        f"Skills: {', '.join(cv.skills[:20]) or 'unknown'}\n"
        f"Education: {', '.join(cv.education[:4]) or 'unknown'}\n"
        f"Roles: {roles or 'unknown'}\n"
        f"Years of experience: {cv.years_experience or 'unknown'}"
    )


def _build_proposal_prompt(answers: list[CoachAnswer], cv: CvProfile | None) -> str:
    """Build the prompt that turns answers into concrete career tracks.

    Args:
        answers: The candidate's replies.
        cv: Parsed CV profile for grounding.

    Returns:
        Complete prompt string.
    """
    replies = "\n".join(f"- {a.id}: {a.answer}" for a in answers if a.answer.strip())
    return f"""You are a pragmatic Dutch-market career coach. Turn this person's
answers into a small set of concrete job-search directions. Respond ONLY with JSON.

THEIR CV:
{_format_cv(cv)}

THEIR ANSWERS:
{replies or "(they answered nothing useful -- rely on the CV)"}

Produce 2-{_MAX_TRACKS} directions. Rules:
- Each STANDALONE direction must be a real job advertised in the Netherlands,
  specific enough to search for -- "Quality & Process Engineering" not
  "something technical". If they were vague or said they don't know, infer
  plausible directions from the CV and say so in the summary.
- Make the directions genuinely DIFFERENT from each other. Do not split one
  job into near-duplicates.
- If they mentioned something they want inside a role but not as the whole job
  (for example some coding or AI tooling alongside other work), that is NOT a
  standalone direction: return it with "mode": "blend". Never make a blend the
  only entry.
- description: 1-3 sentences written for a matching engine -- what the role
  involves and what makes it a good fit for them.
- keywords: real Dutch and English job titles used on Dutch job boards.
- negative_description: what to rule out, drawn from what they want to avoid.

Respond with this exact JSON structure:
{{
  "summary": "<2-3 sentences: what you concluded and why, in plain language>",
  "negative_description": "<what to rule out across all directions>",
  "follow_up": "<one question worth asking next, or empty string>",
  "tracks": [
    {{
      "name": "<short label, e.g. Quality & Efficiency>",
      "description": "<1-3 sentences>",
      "mode": "standalone",
      "required": false,
      "keywords_dutch": ["<Dutch job title>"],
      "keywords_english": ["<English job title>"]
    }}
  ]
}}"""


def propose_tracks(
    answers: list[CoachAnswer],
    *,
    cv: CvProfile | None = None,
    client: LLMClient,
) -> CoachProposal:
    """Turn intake answers into concrete career tracks for review.

    Args:
        answers: The candidate's replies to the coach questions.
        cv: Parsed CV profile, used to ground the suggestions.
        client: LLM client used to synthesise the proposal.

    Returns:
        A CoachProposal. Tracks are empty when the model could not produce a
        usable answer; the caller should keep the existing profile in that
        case rather than wiping it.
    """
    prompt = _build_proposal_prompt(answers, cv)
    try:
        data = _extract_json(client.complete(prompt, purpose="evaluation"))
    except (LLMError, json.JSONDecodeError, ValueError) as exc:
        logger.warning(f"Coach proposal failed: {exc}")
        return CoachProposal(summary="Could not generate suggestions.")

    tracks = _coerce_tracks(data.get("tracks"))
    logger.info(
        f"Coach proposed {len(tracks)} tracks "
        f"({sum(t.mode == 'blend' for t in tracks)} blend)"
    )
    return CoachProposal(
        tracks=tracks,
        summary=str(data.get("summary") or ""),
        negative_description=str(data.get("negative_description") or ""),
        follow_up=str(data.get("follow_up") or ""),
    )


def _coerce_tracks(value: object) -> list[CareerTrack]:
    """Validate proposed tracks, dropping malformed ones.

    Guarantees the result is searchable: ids are unique, and a proposal made
    only of blend tracks is repaired since it would leave nothing to search.

    Args:
        value: Raw "tracks" value from the LLM response.

    Returns:
        Validated tracks, at most _MAX_TRACKS.
    """
    if not isinstance(value, list):
        return []
    tracks: list[CareerTrack] = []
    used: set[str] = set()
    for item in value[:_MAX_TRACKS]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        track_id = _unique_id(slugify_track_id(name), used)
        used.add(track_id)
        tracks.append(
            CareerTrack(
                id=track_id,
                name=name,
                description=str(item.get("description") or "").strip(),
                mode="blend" if item.get("mode") == "blend" else "standalone",
                required=bool(item.get("required")),
                keywords_dutch=_as_str_list(item.get("keywords_dutch")),
                keywords_english=_as_str_list(item.get("keywords_english")),
            )
        )
    if tracks and all(t.mode == "blend" for t in tracks):
        logger.warning("Coach returned only blend tracks; promoting the first")
        tracks[0] = tracks[0].model_copy(update={"mode": "standalone"})
    return tracks


def _unique_id(base: str, used: set[str]) -> str:
    """Return an id not already taken.

    Args:
        base: Preferred id.
        used: Ids already assigned.

    Returns:
        A unique id, suffixed if needed.
    """
    if base not in used:
        return base
    suffix = 2
    while f"{base}-{suffix}" in used:
        suffix += 1
    return f"{base}-{suffix}"


def _as_str_list(value: object) -> list[str]:
    """Coerce a value into a list of non-empty strings.

    Args:
        value: Raw value from the LLM response.

    Returns:
        List of non-empty strings.
    """
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()]
