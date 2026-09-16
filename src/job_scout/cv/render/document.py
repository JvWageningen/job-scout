"""Turning a :class:`~job_scout.cv.models.CVDocument` into a PDF.

The page is two independent columns: a solid colour sidebar bleeding to the page
edge, and a main column on the page background. Each is paginated separately by
:mod:`job_scout.cv.render.frame`, then both are drawn onto the same canvas.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from loguru import logger
from reportlab.lib.colors import Color
from reportlab.lib.pagesizes import A4, LETTER
from reportlab.pdfgen.canvas import Canvas

from job_scout.cv.fonts import face
from job_scout.cv.images import reader_from_path
from job_scout.cv.models import (
    ContactSection,
    CVDocument,
    DetailsSection,
    EducationSection,
    ExperienceSection,
    ListSection,
    Section,
    SkillsSection,
    TextSection,
    Theme,
)
from job_scout.cv.render.blocks import (
    Block,
    Bullet,
    DetailRow,
    IconRow,
    Paragraph,
    Portrait,
    RichParagraph,
    Run,
    SectionBar,
    SkillGrid,
    Spacer,
)
from job_scout.cv.render.frame import Column, draw_page, paginate
from job_scout.cv.render.text import TextStyle, colour, text_width

PAGE_SIZES = {"A4": A4, "LETTER": LETTER}


@dataclass(frozen=True)
class ColumnStyle:
    """Styles and colours shared by every section in one column.

    Bundling these means a single set of section builders can render both the
    sidebar and the main column.
    """

    width: float
    bar_bg: Color
    bar_text: TextStyle
    bar_height: float
    bar_padding: float
    title: TextStyle
    meta: TextStyle
    label: TextStyle
    body: TextStyle
    muted: TextStyle
    accent: Color
    track: Color
    backdrop: Color
    gap: float


@dataclass(frozen=True)
class Geometry:
    """Page dimensions and the two column rectangles."""

    page_width: float
    page_height: float
    sidebar_width: float
    sidebar_pad: float
    gutter: float
    top_margin: float
    bottom_margin: float

    @property
    def sidebar_column(self) -> Column:
        """Region the sidebar blocks flow into."""
        return Column(
            x=self.sidebar_pad,
            top=self.page_height - self.top_margin,
            width=self.sidebar_width - 2 * self.sidebar_pad,
            bottom=self.bottom_margin,
        )

    @property
    def main_column(self) -> Column:
        """Region the main-column blocks flow into."""
        left = self.sidebar_width + self.gutter
        return Column(
            x=left,
            top=self.page_height - self.top_margin,
            width=self.page_width - left - self.gutter,
            bottom=self.bottom_margin,
        )


def build_geometry(theme: Theme) -> Geometry:
    """Derive page geometry from the theme.

    Args:
        theme: Theme supplying the page size and sidebar proportion.

    Returns:
        The resolved geometry.
    """
    width, height = PAGE_SIZES[theme.page_size]
    return Geometry(
        page_width=width,
        page_height=height,
        sidebar_width=width * theme.sidebar_width_pct,
        sidebar_pad=width * 0.031,
        gutter=width * 0.046,
        top_margin=height * 0.030,
        bottom_margin=height * 0.030,
    )


def _style(
    theme: Theme,
    weight: str,
    scale: float,
    fill: Color,
    *,
    char_space: float = 0.0,
    upper: bool = False,
    leading_scale: float = 1.0,
    space_before: float = 0.0,
    space_after: float = 0.0,
) -> TextStyle:
    """Build a text style relative to the theme's base size.

    Args:
        theme: Theme supplying the family, base size and line spacing.
        weight: Face weight name.
        scale: Multiplier applied to the base font size.
        fill: Text colour.
        char_space: Extra letter spacing in points.
        upper: Whether to upper-case the text.
        leading_scale: Multiplier applied to the derived leading.
        space_before: Gap above the block.
        space_after: Gap below the block.

    Returns:
        The assembled style.
    """
    size = theme.base_font_size * scale
    return TextStyle(
        font=face(theme.font_family, weight),  # type: ignore[arg-type]
        size=size,
        fill=fill,
        leading=size * theme.line_spacing * leading_scale,
        char_space=char_space,
        upper=upper,
        space_before=space_before,
        space_after=space_after,
    )


def build_columns(theme: Theme, geometry: Geometry) -> tuple[ColumnStyle, ColumnStyle]:
    """Build the style bundles for the sidebar and the main column.

    Args:
        theme: Source of colours and typography.
        geometry: Source of the two column widths.

    Returns:
        A ``(sidebar, main)`` pair.
    """
    upper = theme.uppercase_headings
    sidebar_text = colour(theme.sidebar_text)
    sidebar_muted = colour(theme.sidebar_muted)
    accent = colour(theme.accent)
    body_text = colour(theme.body_text)
    heading_text = colour(theme.heading_text)
    bar_text = colour(theme.bar_text)

    sidebar = ColumnStyle(
        width=geometry.sidebar_column.width,
        bar_bg=accent,
        bar_text=_style(theme, "bold", 1.02, bar_text, char_space=1.45, upper=True),
        bar_height=theme.base_font_size * 3.0,
        bar_padding=theme.base_font_size * 1.15,
        title=_style(theme, "bold", 1.0, sidebar_text, char_space=0.4, upper=upper),
        meta=_style(theme, "regular", 0.92, sidebar_muted),
        label=_style(theme, "regular", 0.92, sidebar_text, char_space=0.3, upper=upper),
        body=_style(theme, "regular", 0.95, sidebar_text, char_space=0.3, upper=upper),
        muted=_style(theme, "regular", 0.9, sidebar_muted),
        accent=sidebar_text,
        track=Color(1, 1, 1, alpha=0.28),
        backdrop=colour(theme.sidebar_bg),
        gap=theme.base_font_size * 2.1,
    )

    main = ColumnStyle(
        width=geometry.main_column.width,
        bar_bg=accent,
        bar_text=_style(theme, "bold", 1.14, bar_text, char_space=1.6, upper=True),
        bar_height=theme.base_font_size * 3.35,
        bar_padding=theme.base_font_size * 1.3,
        title=_style(theme, "bold", 1.02, heading_text, char_space=0.35, upper=upper),
        meta=_style(theme, "regular", 0.95, body_text, char_space=0.3, upper=upper),
        label=_style(theme, "bold", 0.95, heading_text),
        body=_style(theme, "regular", 0.95, body_text),
        muted=_style(theme, "regular", 0.9, body_text),
        accent=accent,
        track=Color(0, 0, 0, alpha=0.12),
        backdrop=colour(theme.page_bg),
        gap=theme.base_font_size * 1.9,
    )
    return sidebar, main


# --------------------------------------------------------------------------
# Section builders
# --------------------------------------------------------------------------


def _text_blocks(section: TextSection, style: ColumnStyle) -> list[Block]:
    """Blocks for a free-prose section."""
    return [Paragraph(section.body, style.body, style.width)]


def _experience_blocks(section: ExperienceSection, style: ColumnStyle) -> list[Block]:
    """Blocks for a list of roles."""
    blocks: list[Block] = []
    gap = style.body.size * 0.55

    for index, entry in enumerate(section.entries):
        if index:
            blocks.append(Spacer(style.body.size * 1.15))

        blocks.append(
            Paragraph(entry.title, style.title, style.width, keep_with_next=True)
        )
        meta = " | ".join(part for part in (entry.organisation, entry.period) if part)
        if meta:
            blocks.append(
                Paragraph(
                    meta,
                    style.meta,
                    style.width,
                    keep_with_next=bool(entry.description),
                )
            )
        if entry.description:
            blocks.append(Spacer(gap))
            if section.description_label:
                blocks.append(
                    Paragraph(
                        section.description_label,
                        style.label,
                        style.width,
                        keep_with_next=True,
                    )
                )
            blocks.append(Paragraph(entry.description, style.body, style.width))
        for bullet in entry.bullets:
            blocks.append(Bullet(bullet, style.body, style.width))

    return blocks


def _education_blocks(section: EducationSection, style: ColumnStyle) -> list[Block]:
    """Blocks for a list of qualifications."""
    blocks: list[Block] = []

    for index, entry in enumerate(section.entries):
        if index:
            blocks.append(Spacer(style.body.size * 1.15))

        blocks.append(
            Paragraph(entry.degree, style.title, style.width, keep_with_next=True)
        )
        rows = (
            (section.school_label, entry.school),
            (section.period_label, entry.period),
            (section.courses_label, entry.courses),
        )
        present = [(label, value) for label, value in rows if value]
        for position, (label, value) in enumerate(present):
            runs = [Run(value, style.body)]
            if label:
                runs.insert(0, Run(f"{label} ", style.label))
            blocks.append(
                RichParagraph(
                    runs,
                    style.width,
                    keep_with_next=position < len(present) - 1,
                )
            )
        if entry.note:
            blocks.append(Paragraph(entry.note, style.muted, style.width))

    return blocks


def _skills_blocks(section: SkillsSection, style: ColumnStyle) -> list[Block]:
    """Blocks for a grid of skills."""
    names = [item.name for item in section.items]
    levels = [item.level for item in section.items]
    return [
        SkillGrid(
            names,
            style.body,
            style.width,
            columns=section.columns,
            levels=levels if section.show_levels else [],
            accent=style.accent if section.show_levels else None,
            track=style.track,
            row_gap=style.body.size * 0.5,
        )
    ]


def _details_blocks(section: DetailsSection, style: ColumnStyle) -> list[Block]:
    """Blocks for a table of label/value pairs."""
    return [
        DetailRow(
            f"{item.label}{section.label_suffix}" if item.label else "",
            item.value,
            style.label,
            style.body,
            style.width,
            space_after=style.body.size * 0.62,
        )
        for item in section.items
    ]


def _contact_blocks(section: ContactSection, style: ColumnStyle) -> list[Block]:
    """Blocks for icon-prefixed contact lines."""
    return [
        IconRow(
            item.icon,
            item.value,
            style.body,
            style.width,
            icon_size=style.body.size * 1.5,
            gap=style.body.size * 0.95,
            url=item.url,
            backdrop=style.backdrop,
            space_after=style.body.size * 0.72,
        )
        for item in section.items
    ]


def _list_blocks(section: ListSection, style: ColumnStyle) -> list[Block]:
    """Blocks for a plain or bulleted list."""
    if section.bulleted:
        return [Bullet(item, style.body, style.width) for item in section.items]
    return [
        Paragraph(item, style.body, style.width, keep_with_next=False)
        for item in section.items
    ]


def section_blocks(section: Section, style: ColumnStyle) -> list[Block]:
    """Build the blocks for one section, including its title bar.

    Args:
        section: The section to render.
        style: Column styling to draw it with.

    Returns:
        Blocks in draw order, empty if the section is disabled or has no content.
    """
    if not section.enabled:
        return []

    builders = {
        "text": _text_blocks,
        "experience": _experience_blocks,
        "education": _education_blocks,
        "skills": _skills_blocks,
        "details": _details_blocks,
        "contact": _contact_blocks,
        "list": _list_blocks,
    }
    body = builders[section.kind](section, style)  # type: ignore[operator]
    body = [block for block in body if block.height > 0]
    if not body and not section.title:
        return []

    blocks: list[Block] = []
    if section.title:
        blocks.append(
            SectionBar(
                section.title,
                style.width,
                style.bar_bg,
                style.bar_text,
                icon=section.icon,
                bar_height=style.bar_height,
                padding=style.bar_padding,
                space_after=style.bar_text.size * 1.15,
            )
        )
    blocks.extend(body)
    blocks.append(Spacer(style.gap))
    return blocks


# --------------------------------------------------------------------------
# Column assembly
# --------------------------------------------------------------------------


NAME_SCALE = 2.35
"""Name size as a multiple of the base font size, before any shrink-to-fit."""

NAME_MIN_SCALE = 0.95
"""Floor for shrink-to-fit, so a very long name stays legible rather than tiny."""

NAME_TRACKING = 0.26
"""Name letter spacing as a fraction of the name's own font size."""


def _fit_name(name: str, theme: Theme, width: float) -> TextStyle:
    """Build a name style shrunk until its longest word fits on one line.

    The name is the most size-sensitive item on the page: at a fixed size a long
    surname wraps mid-word, which looks broken. Shrinking instead keeps the name
    on whole-word lines whatever its length.

    Args:
        name: The full name to be drawn.
        theme: Theme supplying the family and base size.
        width: Sidebar column width in points.

    Returns:
        A style whose longest word fits ``width``, floored at
        :data:`NAME_MIN_SCALE`.
    """
    words = name.upper().split() or [name.upper()]
    scale = NAME_SCALE
    while scale > NAME_MIN_SCALE:
        candidate = _style(
            theme,
            "light",
            scale,
            colour(theme.sidebar_text),
            char_space=theme.base_font_size * scale * NAME_TRACKING,
            upper=True,
            leading_scale=1.05,
        )
        if all(text_width(word, candidate) <= width for word in words):
            return candidate
        scale *= 0.96

    return _style(
        theme,
        "light",
        NAME_MIN_SCALE,
        colour(theme.sidebar_text),
        char_space=theme.base_font_size * NAME_MIN_SCALE * NAME_TRACKING,
        upper=True,
        leading_scale=1.05,
    )


def _header_blocks(
    doc: CVDocument, style: ColumnStyle, geometry: Geometry, photo: Path | None
) -> list[Block]:
    """Portrait, name and headline at the top of the sidebar.

    Args:
        doc: Source document.
        style: Sidebar styling.
        geometry: Page geometry, for the portrait diameter.
        photo: Resolved path to the portrait, if any.

    Returns:
        The header blocks.
    """
    theme = doc.theme
    blocks: list[Block] = []

    if theme.show_photo and photo is not None:
        reader = reader_from_path(photo)
        if reader is not None:
            diameter = geometry.sidebar_width * theme.photo_diameter_pct
            blocks.append(
                Portrait(
                    reader,
                    diameter,
                    style.width,
                    space_after=theme.base_font_size * 2.4,
                )
            )

    if doc.full_name:
        name_style = _fit_name(doc.full_name, theme, style.width)
        blocks.append(Paragraph(doc.full_name, name_style, style.width, align="center"))

    if doc.headline:
        headline_style = _style(
            theme,
            "regular",
            1.0,
            colour(theme.sidebar_muted),
            char_space=0.8,
            upper=True,
            space_before=theme.base_font_size * 0.6,
        )
        blocks.append(
            Paragraph(doc.headline, headline_style, style.width, align="center")
        )

    if blocks:
        blocks.append(Spacer(theme.base_font_size * 3.0))
    return blocks


def build_blocks(
    doc: CVDocument, photo: Path | None = None
) -> tuple[list[Block], list[Block], Geometry]:
    """Build both columns' block lists.

    Args:
        doc: Source document.
        photo: Resolved path to the portrait, if any.

    Returns:
        A ``(sidebar_blocks, main_blocks, geometry)`` triple.
    """
    geometry = build_geometry(doc.theme)
    sidebar_style, main_style = build_columns(doc.theme, geometry)

    sidebar: list[Block] = _header_blocks(doc, sidebar_style, geometry, photo)
    for section in doc.sidebar:
        sidebar.extend(section_blocks(section, sidebar_style))

    main: list[Block] = []
    for section in doc.main:
        main.extend(section_blocks(section, main_style))

    return sidebar, main, geometry


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def _draw_background(canvas: Canvas, theme: Theme, geometry: Geometry) -> None:
    """Paint the page background and the sidebar panel.

    Args:
        canvas: Target canvas.
        theme: Source of the two background colours.
        geometry: Page and sidebar dimensions.
    """
    canvas.saveState()
    canvas.setFillColor(colour(theme.page_bg))
    canvas.rect(0, 0, geometry.page_width, geometry.page_height, stroke=0, fill=1)
    canvas.setFillColor(colour(theme.sidebar_bg))
    canvas.rect(0, 0, geometry.sidebar_width, geometry.page_height, stroke=0, fill=1)
    canvas.restoreState()


def render_pdf(doc: CVDocument, stream: BinaryIO, photo: Path | None = None) -> int:
    """Render ``doc`` to ``stream`` as a PDF.

    Args:
        doc: The CV to render.
        stream: Binary destination, written and flushed but not closed.
        photo: Resolved path to the portrait, if any.

    Returns:
        The number of pages written.
    """
    sidebar_blocks, main_blocks, geometry = build_blocks(doc, photo)
    sidebar_pages = paginate(sidebar_blocks, geometry.sidebar_column)
    main_pages = paginate(main_blocks, geometry.main_column)
    page_count = max(len(sidebar_pages), len(main_pages), 1)

    canvas = Canvas(stream, pagesize=(geometry.page_width, geometry.page_height))
    canvas.setTitle(f"{doc.full_name} - CV" if doc.full_name else "CV")
    if doc.full_name:
        canvas.setAuthor(doc.full_name)
    canvas.setSubject(doc.headline or "Curriculum Vitae")
    canvas.setCreator("cv-builder")

    for index in range(page_count):
        _draw_background(canvas, doc.theme, geometry)
        if index < len(sidebar_pages):
            draw_page(canvas, sidebar_pages[index], geometry.sidebar_column)
        if index < len(main_pages):
            draw_page(canvas, main_pages[index], geometry.main_column)
        canvas.showPage()

    canvas.save()
    stream.flush()
    logger.debug("Rendered {} page(s) for {!r}", page_count, doc.full_name)
    return page_count
