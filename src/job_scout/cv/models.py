"""Pydantic models describing a CV document.

The document is a pair of ordered section lists - one for the coloured sidebar and
one for the main column. Every section is a tagged variant of :data:`Section`, which
lets the dashboard add, remove and reorder sections without the renderer having to
guess what a payload means.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

HEX_COLOUR = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

type IconName = Literal[
    "person",
    "laptop",
    "graduation",
    "envelope",
    "phone",
    "linkedin",
    "github",
    "globe",
    "location",
    "briefcase",
    "book",
    "star",
    "wrench",
    "language",
    "certificate",
    "heart",
]
"""Icons the renderer can draw. Every name maps to a vector drawing in
:mod:`job_scout.cv.render.icons` - there is no icon font to install."""

type SectionKind = Literal[
    "text", "experience", "education", "skills", "details", "contact", "list"
]


def _new_id() -> str:
    """Return a short unique identifier for a section or entry.

    Returns:
        A 12-character hex string.
    """
    return uuid4().hex[:12]


class _Base(BaseModel):
    """Shared model configuration."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# --------------------------------------------------------------------------
# Theme
# --------------------------------------------------------------------------


class Theme(_Base):
    """Colours, typography and geometry for a rendered CV.

    The defaults are a deepened version of the palette sampled from the reference
    CV. The original bar colour (``#38CB78``) gave white heading text only 2.1:1
    contrast - below the WCAG AA minimum of 4.5:1, and visibly weak in greyscale
    print. ``#1B7A4B`` keeps the same green identity at 5.3:1.
    """

    sidebar_bg: str = "#0B3D2C"
    accent: str = "#1B7A4B"
    page_bg: str = "#FBFBF8"
    sidebar_text: str = "#FFFFFF"
    sidebar_muted: str = "#EAF7EE"
    body_text: str = "#1A1A1A"
    heading_text: str = "#111111"
    bar_text: str = "#FFFFFF"

    font_family: Literal["Lato", "Helvetica"] = "Lato"
    base_font_size: float = Field(default=9.0, ge=6.0, le=14.0)
    line_spacing: float = Field(default=1.32, ge=1.0, le=2.0)

    sidebar_width_pct: float = Field(default=0.362, ge=0.20, le=0.55)
    page_size: Literal["A4", "LETTER"] = "A4"
    uppercase_headings: bool = True
    show_photo: bool = True
    photo_diameter_pct: float = Field(
        default=0.72,
        ge=0.30,
        le=1.0,
        description="Portrait diameter as a fraction of the sidebar width.",
    )

    @field_validator(
        "sidebar_bg",
        "accent",
        "page_bg",
        "sidebar_text",
        "sidebar_muted",
        "body_text",
        "heading_text",
        "bar_text",
    )
    @classmethod
    def _check_colour(cls, value: str) -> str:
        """Reject anything that is not a ``#rgb`` or ``#rrggbb`` string.

        Args:
            value: Candidate colour.

        Returns:
            The validated colour, upper-cased.

        Raises:
            ValueError: If the string is not a hex colour.
        """
        if not HEX_COLOUR.match(value):
            raise ValueError(f"{value!r} is not a hex colour like '#008037'")
        return value.upper()


# --------------------------------------------------------------------------
# Section payloads
# --------------------------------------------------------------------------


class ExperienceEntry(_Base):
    """A single job, project or placement."""

    id: str = Field(default_factory=_new_id)
    title: str = ""
    organisation: str = ""
    period: str = ""
    description: str = ""
    bullets: list[str] = Field(default_factory=list)


class EducationEntry(_Base):
    """A single course, degree or certification."""

    id: str = Field(default_factory=_new_id)
    degree: str = ""
    school: str = ""
    period: str = ""
    courses: str = ""
    note: str = ""


class SkillItem(_Base):
    """A skill, optionally with a 0-5 proficiency rating."""

    id: str = Field(default_factory=_new_id)
    name: str = ""
    level: int | None = Field(default=None, ge=0, le=5)


class DetailItem(_Base):
    """A label/value pair, e.g. ``DATE OF BIRTH`` / ``01-01-1990``."""

    id: str = Field(default_factory=_new_id)
    label: str = ""
    value: str = ""


class ContactItem(_Base):
    """A contact line rendered with a leading icon."""

    id: str = Field(default_factory=_new_id)
    icon: IconName = "envelope"
    value: str = ""
    url: str = ""


# --------------------------------------------------------------------------
# Sections
# --------------------------------------------------------------------------


class _SectionBase(_Base):
    """Fields common to every section variant."""

    id: str = Field(default_factory=_new_id)
    title: str = ""
    icon: IconName | None = None
    enabled: bool = True


class TextSection(_SectionBase):
    """Free prose, e.g. a profile or personal statement."""

    kind: Literal["text"] = "text"
    body: str = ""


class ExperienceSection(_SectionBase):
    """An ordered list of roles."""

    kind: Literal["experience"] = "experience"
    entries: list[ExperienceEntry] = Field(default_factory=list)
    description_label: str = "Job description:"


class EducationSection(_SectionBase):
    """An ordered list of qualifications.

    The three row labels are configurable so the same section can carry
    certifications ("Issuer:", "Issued:") or courses without needing its own kind.
    A label left empty hides that row's prefix.
    """

    kind: Literal["education"] = "education"
    entries: list[EducationEntry] = Field(default_factory=list)
    school_label: str = "School:"
    period_label: str = "Period:"
    courses_label: str = "Main courses:"


class SkillsSection(_SectionBase):
    """A grid of skills, optionally with rating bars."""

    kind: Literal["skills"] = "skills"
    items: list[SkillItem] = Field(default_factory=list)
    columns: int = Field(default=2, ge=1, le=3)
    show_levels: bool = False


class DetailsSection(_SectionBase):
    """A two-column table of label/value pairs."""

    kind: Literal["details"] = "details"
    items: list[DetailItem] = Field(default_factory=list)
    label_suffix: str = Field(
        default=":",
        description="Appended to every label when rendering, e.g. 'RESIDENCE:'.",
    )


class ContactSection(_SectionBase):
    """Icon-prefixed contact lines."""

    kind: Literal["contact"] = "contact"
    items: list[ContactItem] = Field(default_factory=list)


class ListSection(_SectionBase):
    """A plain bulleted or unbulleted list of strings."""

    kind: Literal["list"] = "list"
    items: list[str] = Field(default_factory=list)
    bulleted: bool = False


type Section = Annotated[
    TextSection
    | ExperienceSection
    | EducationSection
    | SkillsSection
    | DetailsSection
    | ContactSection
    | ListSection,
    Field(discriminator="kind"),
]


# --------------------------------------------------------------------------
# Document
# --------------------------------------------------------------------------


class CVDocument(_Base):
    """A complete CV: identity, theme and the two section columns."""

    schema_version: int = 1
    full_name: str = ""
    headline: str = ""
    language: str = Field(
        default="EN",
        max_length=8,
        description="Short language tag for this version, used in the PDF filename.",
    )
    photo: str = Field(
        default="",
        description="Filename inside the profile's upload directory, or empty.",
    )
    theme: Theme = Field(default_factory=Theme)
    sidebar: list[Section] = Field(default_factory=list)
    main: list[Section] = Field(default_factory=list)

    def all_sections(self) -> list[Section]:
        """Return every section across both columns, sidebar first.

        Returns:
            The concatenated section lists.
        """
        return [*self.sidebar, *self.main]
