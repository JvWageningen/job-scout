"""Laying a :class:`~job_scout.letters.models.Letter` out as a print-ready PDF.

The letter is designed to travel with the CV from the CV builder, so it borrows
that CV's identity: a band in the sidebar colour carrying the name in the sidebar
text colour, an accent stripe, the page background, the font family and the body
and heading colours. Everything below the band is ordinary flowing text laid out
by ReportLab Platypus, which handles wrapping, bulleted lists and the rare letter
long enough to need a second page.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, BinaryIO
from xml.sax.saxutils import escape

from loguru import logger
from reportlab.lib.colors import Color, HexColor
from reportlab.lib.enums import TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4, LETTER
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    ListFlowable,
    ListItem,
    NextPageTemplate,
    PageTemplate,
    Paragraph,
    Spacer,
)

from job_scout.cv.fonts import face
from job_scout.cv.models import Theme
from job_scout.letters.models import Letter

PAGE_SIZES: dict[str, tuple[float, float]] = {"A4": A4, "LETTER": LETTER}

BULLET_PREFIX = "- "
"""A paragraph line starting with this is one item of a bulleted list."""

BULLET_MARKER = "•"
CONTACT_SEPARATOR = " · "

BODY_SCALE = 1.2
"""Letter body size as a multiple of the CV's base size: a CV is dense, a letter
is read line by line, so the same theme yields a slightly larger body."""

BODY_SIZE_RANGE = (9.5, 13.0)
"""Floor and ceiling for the body size, whatever the theme's base size."""

NAME_SCALE = 2.1
"""Header name size as a multiple of the body size."""

_FIRST_PAGE = "first"
_LATER_PAGES = "later"


@dataclass(frozen=True)
class LetterGeometry:
    """Page dimensions and margins, all in points."""

    width: float
    height: float
    margin_x: float
    margin_top: float
    margin_bottom: float

    @property
    def text_width(self) -> float:
        """Width available to the body text."""
        return self.width - 2 * self.margin_x


@dataclass(frozen=True)
class LetterStyles:
    """Paragraph styles and colours derived from a theme."""

    name: ParagraphStyle
    contact: ParagraphStyle
    date: ParagraphStyle
    subject: ParagraphStyle
    body: ParagraphStyle
    body_tight: ParagraphStyle
    band: Color
    stripe: Color
    page: Color
    marker: Color
    size: float
    gap: float


@dataclass(frozen=True)
class Header:
    """The name and contact paragraphs of the band, already wrapped.

    Attributes:
        paragraphs: Paragraphs in draw order, each wrapped to the text width.
        heights: Height of each paragraph after wrapping.
        height: Total band height, padding and accent stripe included.
    """

    paragraphs: list[Any]
    heights: list[float]
    height: float


def _markup(text: str) -> str:
    """Escape user text for ReportLab's paragraph mini-markup.

    Args:
        text: Raw text, possibly containing ``&``, ``<`` or ``>``.

    Returns:
        Text that Platypus renders literally, so "R&D <team>" cannot break parsing.
    """
    return escape(" ".join(text.split()))


def build_letter_geometry(theme: Theme) -> LetterGeometry:
    """Derive page size and margins from the theme.

    Args:
        theme: Theme supplying the page size.

    Returns:
        The resolved geometry.
    """
    width, height = PAGE_SIZES[theme.page_size]
    return LetterGeometry(
        width=width,
        height=height,
        margin_x=width * 0.105,
        margin_top=height * 0.075,
        margin_bottom=height * 0.07,
    )


def _body_size(theme: Theme) -> float:
    """Pick the body font size for a theme.

    Args:
        theme: Theme supplying the base font size.

    Returns:
        The body size in points, clamped to :data:`BODY_SIZE_RANGE`.
    """
    low, high = BODY_SIZE_RANGE
    return min(max(theme.base_font_size * BODY_SCALE, low), high)


def _header_styles(theme: Theme, size: float) -> tuple[ParagraphStyle, ParagraphStyle]:
    """Build the name and contact styles drawn on the coloured band.

    The name is not letter-spaced the way the CV's is: tracked glyphs come back
    from PDF text extraction as "S A M", which hurts copy-paste and ATS parsing.

    Args:
        theme: Source of the family and band text colours.
        size: Body font size in points.

    Returns:
        A ``(name, contact)`` pair.
    """
    name_size = size * NAME_SCALE
    name = ParagraphStyle(
        "LetterName",
        fontName=face(theme.font_family, "light"),
        fontSize=name_size,
        leading=name_size * 1.15,
        textColor=HexColor(theme.sidebar_text),
    )
    contact_size = size * 0.92
    contact = ParagraphStyle(
        "LetterContact",
        fontName=face(theme.font_family, "regular"),
        fontSize=contact_size,
        leading=contact_size * theme.line_spacing,
        textColor=HexColor(theme.sidebar_muted),
    )
    return name, contact


def build_letter_styles(theme: Theme) -> LetterStyles:
    """Build every style the letter uses from one theme.

    Args:
        theme: Source of colours and typography.

    Returns:
        The assembled styles.
    """
    size = _body_size(theme)
    gap = size * theme.line_spacing * 0.8
    body = ParagraphStyle(
        "LetterBody",
        fontName=face(theme.font_family, "regular"),
        fontSize=size,
        leading=size * theme.line_spacing,
        textColor=HexColor(theme.body_text),
        alignment=TA_LEFT,
        spaceAfter=gap,
    )
    name, contact = _header_styles(theme, size)
    return LetterStyles(
        name=name,
        contact=contact,
        date=ParagraphStyle("LetterDate", parent=body, alignment=TA_RIGHT),
        subject=ParagraphStyle(
            "LetterSubject",
            parent=body,
            fontName=face(theme.font_family, "bold"),
            textColor=HexColor(theme.heading_text),
        ),
        body=body,
        body_tight=ParagraphStyle("LetterBodyTight", parent=body, spaceAfter=gap / 3),
        band=HexColor(theme.sidebar_bg),
        stripe=HexColor(theme.accent),
        page=HexColor(theme.page_bg),
        marker=HexColor(theme.accent),
        size=size,
        gap=gap,
    )


# --------------------------------------------------------------------------
# Header band
# --------------------------------------------------------------------------


def _band_parts(
    letter: Letter, contact_lines: list[str], styles: LetterStyles
) -> list[Any]:
    """Build the unwrapped paragraphs that go on the band.

    Args:
        letter: Source of the sender's name.
        contact_lines: Email, phone, LinkedIn and the like; blanks are dropped.
        styles: Letter styles.

    Returns:
        Zero, one or two paragraphs: the name and the joined contact line.
    """
    parts: list[Any] = []
    if letter.signature.strip():
        parts.append(Paragraph(_markup(letter.signature.upper()), styles.name))
    contacts = [_markup(line) for line in contact_lines if line.strip()]
    if contacts:
        parts.append(Paragraph(CONTACT_SEPARATOR.join(contacts), styles.contact))
    return parts


def build_header(
    letter: Letter,
    contact_lines: list[str],
    styles: LetterStyles,
    geometry: LetterGeometry,
) -> Header | None:
    """Wrap the band's paragraphs and measure the band.

    Args:
        letter: Source of the sender's name.
        contact_lines: Contact details shown under the name.
        styles: Letter styles.
        geometry: Page geometry, for the text width.

    Returns:
        The measured header, or None when there is nothing to put on a band.
    """
    parts = _band_parts(letter, contact_lines, styles)
    if not parts:
        return None
    heights = [part.wrap(geometry.text_width, geometry.height)[1] for part in parts]
    padding = styles.size * 2.0
    between = styles.size * 0.5 * (len(parts) - 1)
    stripe = styles.size * 0.3
    height = 2 * padding + sum(heights) + between + stripe
    return Header(paragraphs=parts, heights=heights, height=height)


def _draw_header(
    canvas: Canvas, header: Header, styles: LetterStyles, geometry: LetterGeometry
) -> None:
    """Paint the band, its accent stripe and the name and contact paragraphs.

    Args:
        canvas: Target canvas.
        header: Measured header.
        styles: Source of the band colours.
        geometry: Page geometry.
    """
    stripe = styles.size * 0.3
    bottom = geometry.height - header.height
    canvas.setFillColor(styles.band)
    canvas.rect(
        0, bottom + stripe, geometry.width, header.height - stripe, stroke=0, fill=1
    )
    canvas.setFillColor(styles.stripe)
    canvas.rect(0, bottom, geometry.width, stripe, stroke=0, fill=1)

    cursor = geometry.height - styles.size * 2.0
    for paragraph, height in zip(header.paragraphs, header.heights, strict=True):
        cursor -= height
        paragraph.drawOn(canvas, geometry.margin_x, cursor)
        cursor -= styles.size * 0.5


# --------------------------------------------------------------------------
# Body
# --------------------------------------------------------------------------


def split_segments(paragraph: str) -> list[tuple[bool, list[str]]]:
    """Split one letter paragraph into runs of prose lines and runs of bullets.

    Args:
        paragraph: A paragraph whose lines may start with ``"- "``.

    Returns:
        ``(is_bullet_run, lines)`` pairs in order, bullet prefixes removed and
        blank lines dropped.
    """
    segments: list[tuple[bool, list[str]]] = []
    for raw in paragraph.splitlines():
        line = raw.strip()
        is_bullet = line.startswith(BULLET_PREFIX)
        item = line[len(BULLET_PREFIX) :].strip() if is_bullet else line
        if not item:
            continue
        if segments and segments[-1][0] is is_bullet:
            segments[-1][1].append(item)
            continue
        segments.append((is_bullet, [item]))
    return segments


def _bullet_list(items: list[str], styles: LetterStyles, space_after: float) -> Any:
    """Build a bulleted list with a hanging indent.

    Args:
        items: Item texts, prefixes already removed.
        styles: Letter styles.
        space_after: Gap below the list.

    Returns:
        A ListFlowable.
    """
    item_style = ParagraphStyle(
        "LetterBullet", parent=styles.body, spaceAfter=styles.gap / 4
    )
    return ListFlowable(
        [ListItem(Paragraph(_markup(item), item_style)) for item in items],
        bulletType="bullet",
        start=BULLET_MARKER,
        leftIndent=styles.size * 1.5,
        bulletFontName=styles.body.fontName,
        bulletFontSize=styles.size,
        bulletColor=styles.marker,
        spaceAfter=space_after,
    )


def paragraph_flowables(paragraph: str, styles: LetterStyles) -> list[Any]:
    """Lay out one letter paragraph, which may mix prose and bullet lines.

    Consecutive prose lines are joined into one flowing paragraph; consecutive
    bullet lines become one list. Parts of the same paragraph sit closer together
    than separate paragraphs do.

    Args:
        paragraph: The paragraph text.
        styles: Letter styles.

    Returns:
        Flowables in order; empty for a blank paragraph.
    """
    segments = split_segments(paragraph)
    flowables: list[Any] = []
    for index, (is_bullet, lines) in enumerate(segments):
        last = index == len(segments) - 1
        if is_bullet:
            gap = styles.gap if last else styles.gap / 3
            flowables.append(_bullet_list(lines, styles, gap))
            continue
        style = styles.body if last else styles.body_tight
        flowables.append(Paragraph(_markup(" ".join(lines)), style))
    return flowables


def _opening(letter: Letter, styles: LetterStyles) -> list[Any]:
    """Build the place-and-date line, the subject and the salutation.

    Args:
        letter: Source letter.
        styles: Letter styles.

    Returns:
        Flowables for whichever of the three are non-blank.
    """
    fields = (
        (letter.place_date, styles.date, 2.0),
        (letter.subject, styles.subject, 1.5),
        (letter.salutation, styles.body, 1.0),
    )
    flowables: list[Any] = []
    for text, style, spacing in fields:
        if not text.strip():
            continue
        spaced = ParagraphStyle(
            f"{style.name}Spaced", parent=style, spaceAfter=styles.gap * spacing
        )
        flowables.append(Paragraph(_markup(text), spaced))
    return flowables


def _sign_off(letter: Letter, styles: LetterStyles) -> list[Any]:
    """Build the closing and the signature name, kept on the same page.

    Args:
        letter: Source letter.
        styles: Letter styles.

    Returns:
        A single KeepTogether, or nothing when both fields are blank.
    """
    parts: list[Any] = []
    if letter.closing.strip():
        parts.append(Paragraph(_markup(letter.closing), styles.body))
    if letter.signature.strip():
        if parts:
            parts.append(Spacer(1, styles.body.leading * 1.5))
        parts.append(Paragraph(_markup(letter.signature), styles.body))
    return [KeepTogether(parts)] if parts else []


def build_story(letter: Letter, styles: LetterStyles) -> list[Any]:
    """Build every flowable below the band, in reading order.

    Args:
        letter: Source letter.
        styles: Letter styles.

    Returns:
        The Platypus story, starting with a switch to the later-page template.
    """
    story: list[Any] = [NextPageTemplate(_LATER_PAGES)]
    story.extend(_opening(letter, styles))
    for paragraph in letter.paragraphs:
        story.extend(paragraph_flowables(paragraph, styles))
    story.extend(_sign_off(letter, styles))
    return story


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def _templates(
    header: Header | None, styles: LetterStyles, geometry: LetterGeometry
) -> list[Any]:
    """Build the first-page template (band) and the later-page template.

    Args:
        header: Measured header, or None for a letter without a band.
        styles: Letter styles.
        geometry: Page geometry.

    Returns:
        The two page templates.
    """

    def paint_page(canvas: Canvas, _doc: Any) -> None:
        """Fill the page with the theme's page background."""
        canvas.saveState()
        canvas.setFillColor(styles.page)
        canvas.rect(0, 0, geometry.width, geometry.height, stroke=0, fill=1)
        canvas.restoreState()

    def paint_first_page(canvas: Canvas, doc: Any) -> None:
        """Paint the background, then the band with the name and contacts."""
        paint_page(canvas, doc)
        if header is not None:
            canvas.saveState()
            _draw_header(canvas, header, styles, geometry)
            canvas.restoreState()

    top = geometry.margin_top
    if header is not None:
        top = header.height + styles.size * 2.6
    frames = [
        Frame(
            geometry.margin_x,
            geometry.margin_bottom,
            geometry.text_width,
            geometry.height - margin - geometry.margin_bottom,
            leftPadding=0,
            rightPadding=0,
            topPadding=0,
            bottomPadding=0,
            id=f"{name}-body",
        )
        for name, margin in ((_FIRST_PAGE, top), (_LATER_PAGES, geometry.margin_top))
    ]
    return [
        PageTemplate(id=_FIRST_PAGE, frames=[frames[0]], onPage=paint_first_page),
        PageTemplate(id=_LATER_PAGES, frames=[frames[1]], onPage=paint_page),
    ]


def render_letter_pdf(
    letter: Letter,
    stream: BinaryIO,
    *,
    theme: Theme | None = None,
    contact_lines: list[str] | None = None,
) -> None:
    """Render a letter as a PDF that pairs visually with the user's CV.

    Args:
        letter: The letter to lay out.
        stream: Binary destination, written and flushed but not closed.
        theme: The CV's theme; None uses the CV builder's default theme, whose
            Lato family falls back to Helvetica if the fonts cannot be loaded.
        contact_lines: Email, phone, LinkedIn and the like, shown under the name
            separated by middle dots.
    """
    theme = theme if theme is not None else Theme()
    geometry = build_letter_geometry(theme)
    styles = build_letter_styles(theme)
    header = build_header(letter, contact_lines or [], styles, geometry)

    signature = letter.signature.strip()
    doc = BaseDocTemplate(
        stream,
        pagesize=(geometry.width, geometry.height),
        pageTemplates=_templates(header, styles, geometry),
        title=f"{signature} - {letter.subject}" if signature else letter.subject,
        author=signature or None,
        subject=letter.subject,
        creator="job-scout",
        lang=letter.language.value,
    )
    doc.build(build_story(letter, styles))
    stream.flush()
    logger.debug("Rendered letter for job {} on {} page(s)", letter.job_id, doc.page)
