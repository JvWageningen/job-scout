"""Lorem ipsum for the parts of a CV nobody has filled in yet.

A new profile starts empty rather than as somebody else's example career, but an
empty preview shows nothing of the layout. So the preview fills each empty field
with lorem ipsum. That happens on a copy made for the preview only: placeholder
text is never saved, never downloaded, and so can never reach a letter or an
interview answer.

A field counts as empty only when nothing at all has been entered in it, and an
entry is filled only when every field in it is empty. A half-finished entry is
shown as it is, so the preview never mixes placeholder text into real content.
"""

from __future__ import annotations

from typing import Any

from job_scout.cv.models import (
    ContactItem,
    ContactSection,
    CVDocument,
    DetailItem,
    DetailsSection,
    EducationEntry,
    EducationSection,
    ExperienceEntry,
    ExperienceSection,
    ListSection,
    SkillItem,
    SkillsSection,
    TextSection,
)

LOREM_NAME = "Lorem Ipsum"
LOREM_HEADLINE = "Lorem ipsum dolor sit amet"
LOREM_PARAGRAPH = (
    "Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod "
    "tempor incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, "
    "quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo."
)
LOREM_SENTENCE = "Duis aute irure dolor in reprehenderit in voluptate velit esse."
LOREM_PERIOD = "20XX - 20XX"

_CONTACT_PLACEHOLDERS: dict[str, str] = {
    "envelope": "lorem@ipsum.com",
    "phone": "+00 0 0000 0000",
    "linkedin": "linkedin.com/in/lorem-ipsum",
    "github": "github.com/lorem-ipsum",
    "globe": "lorem-ipsum.com",
    "location": "Lorem Ipsum",
}


def with_placeholders(doc: CVDocument) -> CVDocument:
    """Return a copy of a CV with lorem ipsum in everything left empty.

    Args:
        doc: The CV as the editor holds it.

    Returns:
        A new document for rendering the preview; ``doc`` is not modified.
    """
    copy = doc.model_copy(deep=True)
    if not copy.full_name.strip():
        copy.full_name = LOREM_NAME
    if not copy.headline.strip():
        copy.headline = LOREM_HEADLINE
    for section in copy.all_sections():
        _fill_section(section)
    return copy


def _fill_section(section: Any) -> None:
    """Fill one section's empty parts in place.

    Args:
        section: A section of the copied document.
    """
    if isinstance(section, TextSection) and not section.body.strip():
        section.body = LOREM_PARAGRAPH
    elif isinstance(section, ExperienceSection):
        for entry in section.entries:
            _fill_experience(entry)
    elif isinstance(section, EducationSection):
        for study in section.entries:
            _fill_education(study)
    elif isinstance(section, SkillsSection):
        for skill in section.items:
            _fill_skill(skill)
    elif isinstance(section, DetailsSection):
        for detail in section.items:
            _fill_detail(detail)
    elif isinstance(section, ContactSection):
        for contact in section.items:
            _fill_contact(contact)
    elif isinstance(section, ListSection) and not any(i.strip() for i in section.items):
        section.items = ["Lorem ipsum dolor sit amet", "Consectetur adipiscing elit"]


def _is_empty(item: Any) -> bool:
    """Tell whether nothing has been entered in any field of an entry or item.

    Args:
        item: An entry or item model.

    Returns:
        True when every text field is blank and every list is empty.
    """
    for value in item.model_dump(exclude={"id", "icon", "level"}).values():
        if isinstance(value, str) and value.strip():
            return False
        if isinstance(value, list) and any(str(v).strip() for v in value):
            return False
    return True


def _fill_experience(entry: ExperienceEntry) -> None:
    if _is_empty(entry):
        entry.title = "Lorem ipsum dolor"
        entry.organisation = "Consectetur Adipiscing"
        entry.period = LOREM_PERIOD
        entry.description = LOREM_SENTENCE


def _fill_education(entry: EducationEntry) -> None:
    if _is_empty(entry):
        entry.degree = "Lorem ipsum dolor"
        entry.school = "Universitas Ipsum"
        entry.period = LOREM_PERIOD


def _fill_skill(item: SkillItem) -> None:
    if not item.name.strip():
        item.name = "Lorem ipsum"


def _fill_detail(item: DetailItem) -> None:
    if _is_empty(item):
        item.label = "Lorem"
        item.value = "Ipsum dolor"


def _fill_contact(item: ContactItem) -> None:
    if not item.value.strip():
        item.value = _CONTACT_PLACEHOLDERS.get(item.icon, "Lorem ipsum")
