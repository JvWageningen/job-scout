"""Resolving career tracks into the searches the pipeline actually runs.

A user may be open to several genuinely different roles. Each *standalone*
track is its own search with its own description and keywords. A *blend*
track is not a job of its own -- it is a flavour wanted within another role
(for example "some coding and AI-tool building") -- so it is never searched
alone and is folded into every standalone track instead.

Users who never define tracks keep the original single-profile behaviour:
their profile_description becomes one implicit track, so nothing changes for
them and no migration is needed.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from job_scout.models import CareerTrack

if TYPE_CHECKING:
    from job_scout.models import Config

DEFAULT_TRACK_ID = "default"
_MAX_BLEND_CHARS = 600


def slugify_track_id(name: str) -> str:
    """Derive a stable, readable track id from a display name.

    Args:
        name: Human-readable track name.

    Returns:
        A lowercase hyphenated id, or "track" when nothing usable remains.
    """
    slug = re.sub(r"[^\w\s-]", "", name.lower()).strip()
    slug = re.sub(r"[\s_-]+", "-", slug).strip("-")
    return slug or "track"


def resolve_tracks(config: Config) -> list[CareerTrack]:
    """Return the tracks to search, falling back to the single profile.

    Args:
        config: Effective user configuration.

    Returns:
        Enabled tracks, or a single implicit track built from
        profile_description when no tracks are configured.
    """
    tracks = [t for t in config.career_tracks if t.enabled]
    if tracks:
        return tracks
    return [
        CareerTrack(
            id=DEFAULT_TRACK_ID,
            name="Default",
            description=config.profile_description,
            negative_description=config.negative_description,
            keywords_dutch=list(config.keywords_dutch),
            keywords_english=list(config.keywords_english),
        )
    ]


def standalone_tracks(config: Config) -> list[CareerTrack]:
    """Return the tracks that are searched in their own right.

    Args:
        config: Effective user configuration.

    Returns:
        Enabled standalone tracks. Falls back to all resolved tracks when a
        user has only ever defined blend tracks, so the pipeline never has
        nothing to search.
    """
    tracks = resolve_tracks(config)
    standalone = [t for t in tracks if t.mode == "standalone"]
    return standalone or tracks


def blend_tracks(config: Config) -> list[CareerTrack]:
    """Return the tracks that colour every standalone search.

    Args:
        config: Effective user configuration.

    Returns:
        Enabled blend tracks, in configured order.
    """
    return [t for t in resolve_tracks(config) if t.mode == "blend"]


def is_multi_track(config: Config) -> bool:
    """Report whether the user has actually configured multiple tracks.

    Args:
        config: Effective user configuration.

    Returns:
        True when more than one enabled track is configured.
    """
    return len([t for t in config.career_tracks if t.enabled]) > 1


def _blend_clause(blends: list[CareerTrack]) -> str:
    """Render blend tracks as a sentence appended to a track description.

    Args:
        blends: Enabled blend tracks.

    Returns:
        A description fragment, or "" when there are no blend tracks.
    """
    if not blends:
        return ""
    required = [b for b in blends if b.required]
    optional = [b for b in blends if not b.required]
    parts: list[str] = []
    if required:
        wanted = "; ".join(b.description or b.name for b in required)
        parts.append(
            f"The role MUST also involve: {wanted}. Treat this as a "
            "requirement, not a bonus -- score down roles without it."
        )
    if optional:
        wanted = "; ".join(b.description or b.name for b in optional)
        parts.append(
            f"The candidate would also like the role to involve: {wanted}. "
            "This is a bonus that raises the score, not a requirement, and a "
            "role dedicated solely to it is NOT what they want."
        )
    return " ".join(parts)[:_MAX_BLEND_CHARS]


def effective_description(track: CareerTrack, blends: list[CareerTrack]) -> str:
    """Build the profile text used to evaluate a job against one track.

    Args:
        track: The standalone track being evaluated.
        blends: Blend tracks to fold in.

    Returns:
        The track description with any blend clause appended.
    """
    clause = _blend_clause(blends)
    base = track.description.strip()
    if not clause:
        return base
    return f"{base}\n\n{clause}".strip()


def effective_negative(track: CareerTrack, config: Config) -> str:
    """Build the negative criteria for one track.

    A track's own negatives extend, rather than replace, the profile-wide
    negative description so shared dealbreakers still apply.

    Args:
        track: The track being evaluated.
        config: Effective user configuration.

    Returns:
        Combined negative description text.
    """
    parts = [config.negative_description.strip(), track.negative_description.strip()]
    return "\n".join(p for p in parts if p)


def merged_profile(config: Config) -> str:
    """Build one profile covering every track, for the cheap screening pass.

    Title screening only needs to know whether a job is plausibly interesting
    for *any* track, so it runs once against this union rather than per track.

    Args:
        config: Effective user configuration.

    Returns:
        A combined profile description.
    """
    tracks = standalone_tracks(config)
    if len(tracks) == 1 and tracks[0].id == DEFAULT_TRACK_ID:
        return config.profile_description
    lines = [f"- {t.name}: {t.description}".rstrip(": ") for t in tracks]
    blend = _blend_clause(blend_tracks(config))
    header = (
        "The candidate is open to several different kinds of role. A job is "
        "interesting if it fits ANY of these directions:"
    )
    return f"{header}\n" + "\n".join(lines) + (f"\n\n{blend}" if blend else "")


def merged_keywords(config: Config) -> tuple[list[str], list[str]]:
    """Union the search keywords across all standalone tracks.

    Scraping once per track would multiply request volume against the job
    boards, so keywords are merged and de-duplicated instead.

    Keywords are interleaved round-robin rather than concatenated: scraping
    truncates to jobspy_keyword_limit/nvb_keyword_limit, so concatenating
    would let the first track consume the whole budget and leave the other
    tracks never actually searched.

    Args:
        config: Effective user configuration.

    Returns:
        Tuple of (Dutch keywords, English keywords), de-duplicated
        case-insensitively.
    """
    tracks = standalone_tracks(config)
    if len(tracks) == 1 and tracks[0].id == DEFAULT_TRACK_ID:
        return list(config.keywords_dutch), list(config.keywords_english)
    return (
        _dedupe(_interleave([t.keywords_dutch for t in tracks])),
        _dedupe(_interleave([t.keywords_english for t in tracks])),
    )


def _interleave(groups: list[list[str]]) -> list[str]:
    """Round-robin several keyword lists into one fairly-ordered list.

    Args:
        groups: One keyword list per track.

    Returns:
        Interleaved keywords, so a truncated prefix still covers every track.
    """
    out: list[str] = []
    for idx in range(max((len(g) for g in groups), default=0)):
        for group in groups:
            if idx < len(group):
                out.append(group[idx])
    return out


def _dedupe(items: list[str]) -> list[str]:
    """De-duplicate strings case-insensitively, preserving first-seen order.

    Args:
        items: Strings to de-duplicate.

    Returns:
        De-duplicated list.
    """
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(item.strip())
    return out
