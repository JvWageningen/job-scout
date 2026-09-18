"""Everything the applicant has told job-scout about themselves, from every source.

No single source is mandatory. One applicant keeps their CV in CV Builder,
another uploaded a CV they made themselves, a third imported LinkedIn into the
parsed profile, and most have some mix of these plus a profile description,
career tracks and STAR stories. Letters and interview material are written from
all of them together. Each source is labelled with where it came from so the
model can weigh overlapping or conflicting claims, and CV Builder's example CV
is never one of them.

Only reading happens here: no source is parsed with the LLM, so gathering is
cheap enough to run on every request. The structured profile is used when a
pipeline run or a LinkedIn import has already cached it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

from job_scout.config import (
    build_effective_config,
    list_users,
    user_cv_dir,
    user_db_path,
)
from job_scout.cv.models import ContactSection, CVDocument, DetailsSection
from job_scout.cv.sample import example_content, has_career_content
from job_scout.cv.storage import ProfileStore
from job_scout.cv_parser import compute_cv_hash, parse_cv
from job_scout.database import Database
from job_scout.letters.models import LetterLanguage
from job_scout.models import Config

_MAX_CV_TEXT = 12000
_MAX_NOTE = 4000
_MAX_TRACKS = 8
_MAX_TRACK_TEXT = 600
_MAX_STORIES = 8
_MAX_STORY_FIELD = 700

SOURCE_GUIDE = (
    "applicant_sources holds everything the applicant has provided, from several "
    "places that overlap: cv_builder_profile (the CV they maintain in CV Builder), "
    "own_cv_document (text extracted from a CV file they made themselves; the "
    "extraction can split words or lines, so read it for meaning, not layout), "
    "extra_experience_notes (experience they added by hand), parsed_profile (a "
    "structured summary of their CV, including anything imported from "
    "LinkedIn), self_description (how they describe themselves and what they "
    "are looking for), career_directions (the directions they are pursuing) and "
    "star_stories (examples of their work they wrote themselves). Any of these "
    "may be absent. Together they are the applicant's facts. The same job can "
    "appear in several sources: treat it as one job. When sources disagree "
    "about a date or about which role is current, follow the source with the "
    "most recent information and do not combine the conflicting claims. "
    "self_description and career_directions also express wishes: use those for "
    "motivation, never as evidence of experience."
)

_NAME_PARTICLES = "van|de|der|den|ten|ter|te|het|in|op|la|le|du|von|zu|di|da|dos|del|el"
_NOT_A_SURNAME = frozenset({"cv", "curriculum", "resume", "vitae", "nl", "en"})
_POSTCODE_CITY = re.compile(
    r"\b\d{4}[ \t]?[A-Z]{2}\b[ \t,]+([A-Z][\w'’-]+(?:[ \t]+[A-Za-z][\w'’/-]*){0,3})"
)
_CITY_POSTCODE = re.compile(
    r"([A-Z][\w'’-]+(?:[ \t]+[A-Za-z][\w'’/-]*){0,3})[ \t]*,[ \t]*\d{4}[ \t]?[A-Z]{2}\b"
)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# Dutch mobile numbers as CVs write them: 06-12345678, +31 6 1234 5678 and the
# common "+31 (0)6-12345678", which must be taken whole, not from the "0)6".
_PHONE = re.compile(
    r"(?<![\d+(])(?:\+31[ \t.-]*(?:\(0\)[ \t.-]*)?|0031[ \t.-]*|0)"
    r"6(?:[ \t.-]*\d){8}(?!\d)"
)
_LINKEDIN = re.compile(
    r"(?:https?://)?(?:[a-z]{2,3}\.)?linkedin\.com/in/[\w%-]+/?", re.IGNORECASE
)
_NOT_A_TOWN = frozenset({"manager", "consultant", "medewerker", "specialist"})
_PLACE_LABELS = frozenset({"residence", "city", "woonplaats", "plaats"})


class ApplicantError(ValueError):
    """The applicant has provided nothing a letter or interview can be built on."""


@dataclass
class ApplicantFacts:
    """The applicant's facts from every source, labelled by where they came from.

    Attributes:
        sources: Prompt material keyed by source label; see ``SOURCE_GUIDE``.
        used: Human-readable names of the sources that contributed.
        missing: Human-readable notes on sources that were absent or skipped.
        name: Full name for a signature, empty when no source states it.
        place: Place of residence for a letter heading, empty when unknown.
        contacts: Email, phone and LinkedIn for a letter heading.
        cv_slug: The CV Builder profile used, if any.
        cv_doc: That profile, for its theme and contact details.
    """

    sources: dict[str, Any]
    used: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    name: str = ""
    place: str = ""
    contacts: list[str] = field(default_factory=list)
    cv_slug: str | None = None
    cv_doc: CVDocument | None = None

    def evidence(self) -> str:
        """Return all source material as one string, for provenance checks.

        Returns:
            The sources serialised as JSON.
        """
        return json.dumps(self.sources, ensure_ascii=False)


def require_user(user: str) -> str:
    """Resolve an existing user before any private data path is built.

    Args:
        user: The user name from the request.

    Returns:
        The same name, once it is known to exist.

    Raises:
        ApplicantError: If the name is unsafe or not an existing user.
    """
    if not user or user in {".", "..", "all"} or re.search(r"[\\/\x00]", user):
        raise ApplicantError("Select a single existing user.")
    if user not in list_users():
        raise ApplicantError("User not found.")
    return user


def cv_facts(doc: CVDocument) -> dict[str, Any]:
    """Take the factual sections of a CV Builder profile.

    Contact and personal details are excluded: they are never prompt material.

    Args:
        doc: A CV Builder profile.

    Returns:
        The headline and the enabled factual sections.
    """
    sections = [
        s.model_dump(exclude={"id", "icon"})
        for s in doc.all_sections()
        if s.enabled and s.kind not in {"contact", "details"}
    ]
    return {"headline": doc.headline, "sections": sections}


def profile_choices(user: str) -> list[dict[str, object]]:
    """List the CV Builder profiles, marking the ones that are not usable.

    Args:
        user: Name of an existing user.

    Returns:
        One entry per profile with its slug, language and whether it is still
        the example CV or empty, so a dropdown can say why it is skipped.
    """
    store = ProfileStore(user_cv_dir(user))
    choices: list[dict[str, object]] = []
    for slug in store.list_profiles():
        doc = store.load(slug)
        choices.append(
            {
                "slug": slug,
                "language": doc.language,
                "example": bool(example_content(doc)),
                "empty": not has_career_content(doc),
            }
        )
    return choices


def _usable(doc: CVDocument) -> bool:
    return has_career_content(doc) and not example_content(doc)


def _pick_profile(
    store: ProfileStore, language: LetterLanguage | None, slug: str | None
) -> tuple[str, CVDocument] | None:
    """Choose the CV Builder profile to use: the requested one, else by language.

    Args:
        store: The user's CV Builder profiles.
        language: Target language; None accepts any.
        slug: An explicitly chosen profile, or None.

    Returns:
        The chosen profile, or None when no profile holds a real CV.
    """
    docs = {s: store.load(s) for s in store.list_profiles()}
    if slug and slug in docs and _usable(docs[slug]):
        return slug, docs[slug]
    real = {s: d for s, d in docs.items() if _usable(d)}
    if not real:
        return None
    if language is not None:
        preferred = "nederlands" if language is LetterLanguage.NL else "default"
        ordered = [preferred, *real]
        for candidate in ordered:
            doc = real.get(candidate)
            if doc is not None and doc.language.lower() == language.value:
                return candidate, doc
    first = next(iter(real))
    return first, real[first]


def _builder_note(
    store: ProfileStore, slug: str | None, chosen: tuple[str, CVDocument] | None
) -> str:
    """Explain a skipped CV Builder profile, when the reason is worth knowing.

    Not using CV Builder is a normal choice, so an empty or absent CV Builder
    says nothing. A profile the applicant chose, or one still holding the
    example CV, does get a note: otherwise they would wonder why it was ignored.

    Args:
        store: The user's CV Builder profiles.
        slug: The explicitly chosen profile, or None.
        chosen: The profile actually used.

    Returns:
        A note for the applicant, or an empty string when there is nothing to say.
    """
    if chosen is not None and (slug is None or chosen[0] == slug):
        return ""
    slugs = store.list_profiles()
    if slug and slug not in slugs:
        return f"CV Builder profile '{slug}' no longer exists"
    if slug and not example_content(store.load(slug)):
        return f"CV Builder profile '{slug}' is empty, so it was not used"
    for candidate in [slug] if slug else slugs:
        found = example_content(store.load(candidate))
        if found:
            return (
                f"CV Builder profile '{candidate}' still holds the example CV "
                f"({', '.join(found[:2])}), so it was not used"
            )
    return ""


def _add_builder(
    facts: ApplicantFacts,
    user: str,
    language: LetterLanguage | None,
    slug: str | None,
) -> None:
    """Add the CV Builder profile, when one holds a real CV.

    Args:
        facts: The facts being gathered.
        user: Name of an existing user.
        language: Target language; None accepts any.
        slug: An explicitly chosen profile, or None.
    """
    store = ProfileStore(user_cv_dir(user))
    chosen = _pick_profile(store, language, slug)
    note = _builder_note(store, slug, chosen)
    if note:
        facts.missing.append(note)
    if chosen is None:
        return
    facts.cv_slug, facts.cv_doc = chosen
    facts.sources["cv_builder_profile"] = cv_facts(chosen[1])
    facts.used.append(f"CV Builder profile '{chosen[0]}'")


def _own_cv_text(config: Config) -> tuple[str, str]:
    """Read the CV file the applicant uploaded themselves.

    Args:
        config: The applicant's effective configuration.

    Returns:
        The extracted text and the file name; both empty when there is none.
    """
    if not config.cv_path:
        return "", ""
    path = Path(config.cv_path)
    try:
        # Not stripped: the parsed-profile cache is keyed on this exact text.
        return parse_cv(path), path.name
    except (FileNotFoundError, OSError, ValueError) as exc:
        logger.warning(f"Own CV at {path.name} could not be read: {exc}")
        return "", path.name


def _add_own_cv(facts: ApplicantFacts, config: Config, text: str, name: str) -> None:
    """Add the applicant's own CV file and their hand-written experience notes.

    Args:
        facts: The facts being gathered.
        config: The applicant's effective configuration.
        text: Text extracted from the CV file.
        name: The CV file's name.
    """
    if text.strip():
        facts.sources["own_cv_document"] = text.strip()[:_MAX_CV_TEXT]
        facts.used.append(f"your own CV ({name})")
    elif name:
        facts.missing.append(f"your CV file {name} could not be read")
    notes = config.cv_notes.strip()
    if notes:
        facts.sources["extra_experience_notes"] = notes[:_MAX_NOTE]
        facts.used.append("your extra experience notes")


def cached_profile(db: Database, config: Config, text: str) -> dict[str, Any] | None:
    """Read the structured profile a pipeline run or LinkedIn import cached.

    The cache is keyed on the CV text. Some callers hash the file text alone and
    some append the manual notes first, so both keys are tried.

    Args:
        db: The applicant's database.
        config: The applicant's effective configuration.
        text: Text extracted from the CV file.

    Returns:
        The cached profile, or None when nothing is cached for this CV.
    """
    keys = [text]
    if config.cv_notes:
        keys.append(f"{text}\n\n--- Additional experience ---\n{config.cv_notes}")
    for key in keys:
        cached = db.get_cached_cv_profile(compute_cv_hash(key))
        if not cached:
            continue
        try:
            data = json.loads(cached)
        except json.JSONDecodeError:
            logger.warning("Cached CV profile is not valid JSON; ignored")
            continue
        if isinstance(data, dict) and any(data.values()):
            return data
    return None


def _add_profile(
    facts: ApplicantFacts, db: Database, config: Config, text: str
) -> None:
    """Add the structured profile, which includes any LinkedIn import.

    Args:
        facts: The facts being gathered.
        db: The applicant's database.
        config: The applicant's effective configuration.
        text: Text extracted from the CV file.
    """
    profile = cached_profile(db, config, text) if text else None
    if profile is None:
        return
    facts.sources["parsed_profile"] = profile
    facts.used.append("your parsed profile (including any LinkedIn import)")


def _add_self_description(facts: ApplicantFacts, config: Config) -> None:
    """Add the profile description and the enabled career tracks.

    Args:
        facts: The facts being gathered.
        config: The applicant's effective configuration.
    """
    description = config.profile_description.strip()
    if description:
        facts.sources["self_description"] = description[:_MAX_NOTE]
        facts.used.append("your profile description")
    tracks = [
        {"name": t.name, "description": t.description[:_MAX_TRACK_TEXT]}
        for t in config.career_tracks
        if t.enabled and t.name.strip()
    ][:_MAX_TRACKS]
    if tracks:
        facts.sources["career_directions"] = tracks
        facts.used.append(f"{len(tracks)} career track(s)")


def star_story_payload(db: Database) -> list[dict[str, Any]]:
    """Read the applicant's STAR stories, trimmed for a prompt.

    Args:
        db: The applicant's database.

    Returns:
        Up to eight stories, newest first, each numbered from 1.
    """
    stories: list[dict[str, Any]] = []
    for number, story in enumerate(db.get_star_stories()[:_MAX_STORIES], start=1):
        entry: dict[str, Any] = {"number": number}
        for part in ("situation", "task", "action", "result"):
            entry[part] = str(story.get(part) or "")[:_MAX_STORY_FIELD]
        stories.append(entry)
    return stories


def _add_stories(facts: ApplicantFacts, db: Database) -> None:
    """Add the applicant's STAR stories.

    Args:
        facts: The facts being gathered.
        db: The applicant's database.
    """
    stories = star_story_payload(db)
    if stories:
        facts.sources["star_stories"] = stories
        noun = "story" if len(stories) == 1 else "stories"
        facts.used.append(f"{len(stories)} STAR {noun}")


def gather_applicant_facts(
    user: str,
    language: LetterLanguage | None = None,
    *,
    cv_slug: str | None = None,
    stories: bool = True,
) -> ApplicantFacts:
    """Collect the applicant's facts from every source they have provided.

    Args:
        user: Name of the applicant.
        language: Target language, used to choose between CV Builder profiles;
            None accepts any.
        cv_slug: A CV Builder profile to prefer. It is skipped with a note when
            it is the example CV, empty or gone, never used regardless.
        stories: Whether to include the STAR stories. A caller that numbers and
            cites the stories itself passes False to avoid sending them twice.

    Returns:
        The labelled facts, with which sources were used and which were absent.

    Raises:
        ApplicantError: If the user does not exist, or no source holds any
            career facts at all.
    """
    require_user(user)
    config = build_effective_config(user)
    db = Database(user_db_path(user))
    facts = ApplicantFacts(sources={})
    _add_builder(facts, user, language, cv_slug)
    text, file_name = _own_cv_text(config)
    _add_own_cv(facts, config, text, file_name)
    _add_profile(facts, db, config, text)
    if not file_name and "cv_builder_profile" not in facts.sources:
        facts.missing.append("no CV uploaded under Profile & Filters")
    _add_self_description(facts, config)
    if stories:
        _add_stories(facts, db)
    _require_career_facts(facts)
    _fill_identity(facts, config, user, text, file_name)
    logger.debug(
        f"Applicant facts for {user}: {', '.join(facts.used)}; "
        f"missing: {', '.join(facts.missing) or 'nothing'}"
    )
    return facts


def _require_career_facts(facts: ApplicantFacts) -> None:
    """Refuse when nothing describes what the applicant has actually done.

    A profile description or career tracks alone say what someone wants, not
    what they did, so they are not enough to write about a career.

    Args:
        facts: The gathered facts.

    Raises:
        ApplicantError: If no CV, notes, parsed profile or story exists.
    """
    career = {
        "cv_builder_profile",
        "own_cv_document",
        "extra_experience_notes",
        "parsed_profile",
        "star_stories",
    }
    if career & facts.sources.keys():
        return
    raise ApplicantError(
        "No CV information found. Upload your own CV under Profile & Filters, "
        "fill in "
        "CV Builder, or add your experience notes: any one of them is enough."
    )


def _fill_identity(
    facts: ApplicantFacts, config: Config, user: str, text: str, file_name: str
) -> None:
    """Find the name, place and contact details for a letter heading.

    A real CV Builder profile states them in labelled fields, so it is read
    first. Otherwise they are taken from the applicant's own CV, and a detail
    no source states stays empty rather than guessed.

    Args:
        facts: The facts being gathered.
        config: The applicant's effective configuration.
        user: Name of the applicant, the anchor for finding their full name.
        text: Text extracted from the applicant's own CV file.
        file_name: That file's name, which often carries the full name.
    """
    doc = facts.cv_doc
    if doc is not None:
        facts.name = doc.full_name.strip()
        facts.place = _labelled_place(doc)
        facts.contacts = _builder_contacts(doc)
    anchors = [a for a in (config.name.strip(), user) if a]
    # The file name first: the applicant typed it, whereas PDF extraction can
    # split a name in the text itself ("W ittem an" for "Witteman").
    facts.name = (
        facts.name
        or _full_name(anchors, Path(file_name).stem.replace("_", " "))
        or _full_name(anchors, text)
    )
    facts.place = facts.place or _city(config.home_address) or _city(text, strict=True)
    facts.contacts = facts.contacts or _text_contacts(text)


def _labelled_place(doc: CVDocument) -> str:
    """Take residence only from an explicitly labelled CV Builder detail.

    Args:
        doc: A CV Builder profile.

    Returns:
        The place of residence, or an empty string.
    """
    for section in doc.all_sections():
        if not isinstance(section, DetailsSection) or not section.enabled:
            continue
        for item in section.items:
            if item.label.lower().strip(": ") in _PLACE_LABELS:
                return item.value
    return ""


def _builder_contacts(doc: CVDocument) -> list[str]:
    """Take the contact lines from a CV Builder profile.

    Args:
        doc: A CV Builder profile.

    Returns:
        The enabled contact values, in the order the CV shows them.
    """
    contacts: list[str] = []
    for section in doc.all_sections():
        if isinstance(section, ContactSection) and section.enabled:
            contacts.extend(item.value for item in section.items if item.value)
    return contacts


def _full_name(anchors: list[str], text: str) -> str:
    """Find the applicant's full name, starting from the first name they log in as.

    CV text extraction often splits a name over lines and capitalises it, as in
    "JEROEN VAN\\nWAGENINGEN", so the match crosses whitespace and the result is
    re-cased. Only one surname word is taken after any particles, which keeps a
    following word such as "CV" out of the name.

    Args:
        anchors: First names to look for, most specific first.
        text: CV text or a CV file name.

    Returns:
        The full name, or an empty string when no anchor is followed by one.
    """
    for anchor in anchors:
        pattern = re.compile(
            rf"(?<![\w.@-])(?i:{re.escape(anchor)})"
            rf"((?:\s+(?i:{_NAME_PARTICLES}))*)\s+([A-ZÀ-Ý][\w'’-]+)"
        )
        for match in pattern.finditer(text):
            surname = match.group(2)
            if surname.casefold() in _NOT_A_SURNAME:
                continue
            return _recase(match.group(0))
    return ""


def _recase(name: str) -> str:
    """Normalise a name's case: particles lower-case, other words capitalised.

    Args:
        name: A name as found, possibly all upper-case or split over lines.

    Returns:
        The name on one line with conventional Dutch capitalisation.
    """
    particles = set(_NAME_PARTICLES.split("|"))
    words = name.split()
    cased: list[str] = []
    for index, word in enumerate(words):
        if index and word.casefold() in particles:
            cased.append(word.lower())
        elif word.isupper() or word.islower():
            cased.append("-".join(part.capitalize() for part in word.split("-")))
        else:
            cased.append(word)
    return " ".join(cased)


def _city(text: str, *, strict: bool = False) -> str:
    """Take the town from a Dutch postcode line, such as "2152 KL Nieuw-Vennep".

    Args:
        text: A home address, or CV text when ``strict``.
        strict: Accept only a postcode line that ends with the town. In CV text
            a year followed by an abbreviation ("2018 HR Manager") looks like a
            postcode, and one on a line of its own is far more likely an address.

    Returns:
        The town, or an empty string when no postcode line is present.
    """
    for pattern in (_POSTCODE_CITY, _CITY_POSTCODE):
        for match in pattern.finditer(text or ""):
            if strict and not _ends_line(text, match.end()):
                continue
            town = match.group(1).strip()
            if town.split()[0].casefold() not in _NOT_A_TOWN:
                return town
    return ""


def _ends_line(text: str, end: int) -> bool:
    """Tell whether only whitespace follows a position on its line.

    Args:
        text: The text searched.
        end: Position just after a match.

    Returns:
        True when the match runs to the end of its line.
    """
    rest = text[end:].split("\n", 1)[0]
    return not rest.strip()


def _text_contacts(text: str) -> list[str]:
    """Take email, mobile number and LinkedIn from the applicant's own CV.

    Args:
        text: Text extracted from the applicant's own CV file.

    Returns:
        At most one of each, in the order a letter heading shows them.
    """
    contacts: list[str] = []
    for pattern in (_EMAIL, _PHONE, _LINKEDIN):
        match = pattern.search(text or "")
        if match:
            contacts.append(match.group(0).strip())
    return contacts


def describe_sources(user: str) -> dict[str, list[str]]:
    """Summarise which sources the applicant has, for display before generating.

    CV Builder is listed as a whole rather than as the one profile a particular
    language would pick.

    Args:
        user: Name of an existing user.

    Returns:
        ``used``: sources that will contribute; ``missing``: notes on absent or
        skipped sources, including why nothing can be generated yet.
    """
    try:
        facts = gather_applicant_facts(user)
    except ApplicantError as exc:
        return {"used": [], "missing": [str(exc)]}
    real = [
        str(c["slug"])
        for c in profile_choices(user)
        if not c["example"] and not c["empty"]
    ]
    used = [u for u in facts.used if not u.startswith("CV Builder profile")]
    if real:
        used.insert(0, "CV Builder (" + ", ".join(real) + ")")
    return {"used": used, "missing": facts.missing}
