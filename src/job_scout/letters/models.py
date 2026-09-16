"""Data models for the motivational letter writer."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints


class LetterLanguage(StrEnum):
    """Languages a letter can be written in."""

    NL = "nl"
    EN = "en"


class ExampleLetter(BaseModel):
    """One of the user's own past letters, used purely as a style reference."""

    name: str = Field(description="File name as stored, e.g. 'previous-letter.pdf'.")
    language: LetterLanguage
    text: str
    words: int
    modified: datetime


class ExampleSummary(BaseModel):
    """What gets listed about an example. Deliberately never the letter text."""

    name: str
    language: LetterLanguage
    words: int
    modified: datetime


class LetterRequest(BaseModel):
    """What the user asks the writer for."""

    job_id: int = Field(gt=0)
    language: LetterLanguage | Literal["auto"] = "auto"
    cv_slug: str | None = Field(
        default=None,
        description="CV builder profile to draw facts from; None picks the profile "
        "whose language matches the letter.",
    )
    recipient: str = Field(
        default="",
        max_length=120,
        description="Who the letter is addressed to, e.g. 'Alex Example' or "
        "'wervingsteam'. Empty means a generic salutation.",
    )
    notes: str = Field(
        default="",
        max_length=2000,
        description="Context only the user knows: a conversation they already had, "
        "what they want to ask about, why this employer.",
    )


class WarningKind(StrEnum):
    """Categories of problem the writer reports alongside a letter."""

    EXAMPLE_LEAK = "example_leak"
    LENGTH = "length"
    NO_STYLE_GUIDE = "no_style_guide"
    NO_EXAMPLES = "no_examples"
    CV_FALLBACK = "cv_fallback"


class LetterWarning(BaseModel):
    """Something the user should check before sending the letter."""

    kind: WarningKind
    message: str


class Letter(BaseModel):
    """A letter structured so it can be edited field by field and laid out as a PDF.

    A paragraph may hold a bulleted list: each line starting with ``- `` is one
    bullet, and a paragraph may mix a lead-in sentence with bullet lines.
    """

    job_id: int = Field(gt=0)
    language: LetterLanguage
    cv_slug: str | None = None
    place_date: str = Field(max_length=500)
    subject: str = Field(max_length=500)
    salutation: str = Field(max_length=500)
    paragraphs: list[
        Annotated[str, StringConstraints(min_length=1, max_length=6000)]
    ] = Field(min_length=1, max_length=12)
    closing: str = Field(max_length=500)
    signature: str = Field(max_length=500)
    warnings: list[LetterWarning] = Field(default_factory=list)
    examples_used: list[str] = Field(default_factory=list)
    generated_at: datetime
    edited: bool = False

    def body_text(self) -> str:
        """Return the paragraphs joined as plain text.

        Returns:
            The body, paragraphs separated by a blank line.
        """
        return "\n\n".join(p.strip() for p in self.paragraphs if p.strip())

    def word_count(self) -> int:
        """Count the words in the body, excluding header and signature.

        Returns:
            Number of whitespace-separated words in the paragraphs.
        """
        return len(self.body_text().split())

    def as_plain_text(self) -> str:
        """Render the complete letter as plain text.

        This is also what gets stored in the database's cover-letter column, so the
        pre-existing text-only consumers keep working.

        Returns:
            The letter from place-and-date line through to the signature.
        """
        parts = [
            self.place_date,
            self.subject,
            self.salutation,
            self.body_text(),
            self.closing,
            self.signature,
        ]
        return "\n\n".join(part.strip() for part in parts if part.strip())
