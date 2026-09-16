"""Drawable layout blocks.

A block knows its own height because its width is bound at construction time, which
means text is wrapped exactly once. Blocks are deliberately small - roughly one
paragraph or one row each - so the column packer in :mod:`job_scout.cv.render.frame`
can paginate at sensible boundaries without ever having to split a block.

Coordinates follow ReportLab's bottom-up convention, but every block is positioned
by its *top* edge and draws downwards from there.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass

from reportlab.lib.colors import Color, white
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

from job_scout.cv.models import IconName
from job_scout.cv.render.icons import draw_icon
from job_scout.cv.render.text import (
    Align,
    TextStyle,
    baseline_offset,
    draw_line,
    text_width,
    wrap,
)


class Block(ABC):
    """A unit of content with a known height and a pre-bound width."""

    keep_with_next: bool = False
    """When True the packer will not leave this block as the last one on a page."""

    @property
    @abstractmethod
    def height(self) -> float:
        """Total vertical space the block occupies, in points."""

    @abstractmethod
    def draw(self, canvas: Canvas, x: float, top: float) -> None:
        """Render the block.

        Args:
            canvas: Target canvas.
            x: Left edge of the block.
            top: Top edge of the block, in ReportLab (bottom-up) space.
        """


# --------------------------------------------------------------------------
# Whitespace and rules
# --------------------------------------------------------------------------


class Spacer(Block):
    """Vertical whitespace."""

    def __init__(self, amount: float) -> None:
        """Initialise the spacer.

        Args:
            amount: Height in points.
        """
        self._amount = amount

    @property
    def height(self) -> float:
        """Height in points."""
        return self._amount

    def draw(self, canvas: Canvas, x: float, top: float) -> None:
        """Draw nothing."""


class Rule(Block):
    """A horizontal divider line."""

    def __init__(
        self,
        width: float,
        fill: Color,
        thickness: float = 0.6,
        space_before: float = 0.0,
        space_after: float = 0.0,
    ) -> None:
        """Initialise the rule.

        Args:
            width: Line width in points.
            fill: Line colour.
            thickness: Stroke thickness in points.
            space_before: Gap above the line.
            space_after: Gap below the line.
        """
        self._width = width
        self._fill = fill
        self._thickness = thickness
        self._space_before = space_before
        self._space_after = space_after

    @property
    def height(self) -> float:
        """Height in points."""
        return self._space_before + self._thickness + self._space_after

    def draw(self, canvas: Canvas, x: float, top: float) -> None:
        """Stroke the divider."""
        y = top - self._space_before - self._thickness / 2.0
        canvas.saveState()
        canvas.setStrokeColor(self._fill)
        canvas.setLineWidth(self._thickness)
        canvas.line(x, y, x + self._width, y)
        canvas.restoreState()


# --------------------------------------------------------------------------
# Plain and mixed-style text
# --------------------------------------------------------------------------


class Paragraph(Block):
    """Word-wrapped text in a single style."""

    def __init__(
        self,
        text: str,
        style: TextStyle,
        width: float,
        align: Align = "left",
        keep_with_next: bool = False,
    ) -> None:
        """Wrap ``text`` to ``width`` immediately.

        Args:
            text: Text to render.
            style: Style to draw with.
            width: Column width in points.
            align: Horizontal alignment.
            keep_with_next: Whether to bind this block to the one after it.
        """
        self._lines = wrap(text, style, width)
        self._style = style
        self._width = width
        self._align = align
        self.keep_with_next = keep_with_next

    @property
    def lines(self) -> list[str]:
        """The wrapped lines, mainly useful for tests."""
        return list(self._lines)

    @property
    def height(self) -> float:
        """Height in points, zero when there is no text."""
        if not self._lines:
            return 0.0
        style = self._style
        return style.space_before + len(self._lines) * style.leading + style.space_after

    def draw(self, canvas: Canvas, x: float, top: float) -> None:
        """Draw every wrapped line."""
        if not self._lines:
            return
        style = self._style
        offset = baseline_offset(style)
        cursor = top - style.space_before
        for line in self._lines:
            draw_line(canvas, x, cursor - offset, line, style, self._width, self._align)
            cursor -= style.leading


@dataclass(frozen=True)
class Run:
    """A styled fragment inside a :class:`RichParagraph`."""

    text: str
    style: TextStyle


class RichParagraph(Block):
    """Word-wrapped text that mixes several styles on the same line.

    Used for rows such as a bold ``School:`` label followed by a regular value.
    """

    def __init__(
        self,
        runs: Sequence[Run],
        width: float,
        keep_with_next: bool = False,
        space_before: float = 0.0,
        space_after: float = 0.0,
    ) -> None:
        """Lay the runs out into lines immediately.

        Args:
            runs: Styled fragments, in reading order.
            width: Column width in points.
            keep_with_next: Whether to bind this block to the one after it.
            space_before: Gap above the paragraph.
            space_after: Gap below the paragraph.
        """
        self._width = width
        self._space_before = space_before
        self._space_after = space_after
        self.keep_with_next = keep_with_next
        self._lines = self._layout(runs, width)

    @staticmethod
    def _tokenise(runs: Sequence[Run]) -> list[tuple[str, TextStyle] | None]:
        """Split runs into word tokens, using ``None`` for a forced line break.

        Args:
            runs: Styled fragments.

        Returns:
            A flat token list.
        """
        tokens: list[tuple[str, TextStyle] | None] = []
        for run in runs:
            normalised = run.text.replace("\r\n", "\n").replace("\r", "\n")
            for index, segment in enumerate(normalised.split("\n")):
                if index:
                    tokens.append(None)
                tokens.extend((word, run.style) for word in segment.split())
        return tokens

    def _layout(
        self, runs: Sequence[Run], width: float
    ) -> list[list[tuple[str, TextStyle]]]:
        """Greedily pack tokens into lines.

        Args:
            runs: Styled fragments.
            width: Column width in points.

        Returns:
            One list of styled words per line.
        """
        lines: list[list[tuple[str, TextStyle]]] = []
        current: list[tuple[str, TextStyle]] = []
        used = 0.0

        for token in self._tokenise(runs):
            if token is None:
                lines.append(current)
                current, used = [], 0.0
                continue

            word, style = token
            gap = text_width(" ", style) if current else 0.0
            advance = gap + text_width(word, style)
            if current and used + advance > width:
                lines.append(current)
                current, used = [], 0.0
                advance = text_width(word, style)
            current.append((word, style))
            used += advance

        if current:
            lines.append(current)
        return lines

    @staticmethod
    def _line_leading(line: Sequence[tuple[str, TextStyle]]) -> float:
        """Return the tallest leading used on a line.

        Args:
            line: Styled words on the line.

        Returns:
            Leading in points.
        """
        return max((style.leading for _, style in line), default=0.0)

    @property
    def height(self) -> float:
        """Height in points, zero when there is no text."""
        if not self._lines:
            return 0.0
        body = sum(self._line_leading(line) for line in self._lines)
        return self._space_before + body + self._space_after

    def draw(self, canvas: Canvas, x: float, top: float) -> None:
        """Draw each line word by word."""
        cursor = top - self._space_before
        for line in self._lines:
            leading = self._line_leading(line)
            if not line:
                cursor -= leading
                continue
            tallest = max(line, key=lambda item: item[1].size)[1]
            baseline = cursor - baseline_offset(tallest)
            pen = x
            for index, (word, style) in enumerate(line):
                if index:
                    pen += text_width(" ", style)
                draw_line(canvas, pen, baseline, word, style)
                pen += text_width(word, style)
            cursor -= leading


class Bullet(Block):
    """A wrapped list item with a leading dot and hanging indent."""

    def __init__(
        self,
        text: str,
        style: TextStyle,
        width: float,
        indent: float = 9.0,
        marker: str = "•",
    ) -> None:
        """Wrap the item text to the indented width.

        Args:
            text: Item text.
            style: Style to draw with.
            width: Full column width in points.
            indent: Hanging indent applied to the text.
            marker: Bullet glyph.
        """
        self._style = style
        self._indent = indent
        self._marker = marker
        self._width = width
        self._lines = wrap(text, style, max(width - indent, 1.0))

    @property
    def height(self) -> float:
        """Height in points, zero when there is no text."""
        if not self._lines:
            return 0.0
        style = self._style
        return style.space_before + len(self._lines) * style.leading + style.space_after

    def draw(self, canvas: Canvas, x: float, top: float) -> None:
        """Draw the marker then the wrapped text."""
        if not self._lines:
            return
        style = self._style
        offset = baseline_offset(style)
        cursor = top - style.space_before
        draw_line(canvas, x, cursor - offset, self._marker, style)
        for line in self._lines:
            draw_line(canvas, x + self._indent, cursor - offset, line, style)
            cursor -= style.leading


# --------------------------------------------------------------------------
# Structured rows
# --------------------------------------------------------------------------


class SectionBar(Block):
    """A solid colour bar carrying a section title and an optional icon."""

    def __init__(
        self,
        title: str,
        width: float,
        background: Color,
        style: TextStyle,
        icon: IconName | None = None,
        bar_height: float = 22.0,
        padding: float = 10.0,
        space_before: float = 0.0,
        space_after: float = 0.0,
    ) -> None:
        """Initialise the bar.

        Args:
            title: Section title, drawn in ``style``.
            width: Bar width in points.
            background: Bar fill colour.
            style: Title text style.
            icon: Optional icon drawn at the right-hand end.
            bar_height: Bar height in points.
            padding: Horizontal inset for the title and icon.
            space_before: Gap above the bar.
            space_after: Gap below the bar.
        """
        self._title = title
        self._width = width
        self._background = background
        self._style = style
        self._icon = icon
        self._bar_height = bar_height
        self._padding = padding
        self._space_before = space_before
        self._space_after = space_after
        self.keep_with_next = True

    @property
    def height(self) -> float:
        """Height in points."""
        return self._space_before + self._bar_height + self._space_after

    def draw(self, canvas: Canvas, x: float, top: float) -> None:
        """Fill the bar, then draw the title and icon over it."""
        bar_top = top - self._space_before
        bar_bottom = bar_top - self._bar_height

        canvas.saveState()
        canvas.setFillColor(self._background)
        canvas.rect(x, bar_bottom, self._width, self._bar_height, stroke=0, fill=1)
        canvas.restoreState()

        centre = bar_bottom + self._bar_height / 2.0
        style = self._style
        baseline = centre - (style.size * 0.34)
        draw_line(canvas, x + self._padding, baseline, self._title, style)

        if self._icon is None:
            return
        size = self._bar_height * 0.62
        draw_icon(
            canvas,
            self._icon,
            x + self._width - self._padding - size,
            centre - size / 2.0,
            size,
            style.fill,
            self._background,
        )


class IconRow(Block):
    """An icon followed by wrapped text, used for contact lines."""

    def __init__(
        self,
        icon: IconName,
        text: str,
        style: TextStyle,
        width: float,
        icon_size: float = 13.0,
        gap: float = 9.0,
        url: str = "",
        backdrop: Color | None = None,
        space_before: float = 0.0,
        space_after: float = 0.0,
    ) -> None:
        """Wrap the text into the space left of the icon.

        Args:
            icon: Icon drawn at the left.
            text: Contact text.
            style: Text style.
            width: Full row width in points.
            icon_size: Icon box size in points.
            gap: Space between the icon and the text.
            url: Optional link target applied to the text area.
            backdrop: Colour behind the row. Icons with punched-out details (the
                LinkedIn tile, for one) need it, or they render as solid blocks.
            space_before: Gap above the row.
            space_after: Gap below the row.
        """
        self._icon = icon
        self._style = style
        self._width = width
        self._icon_size = icon_size
        self._gap = gap
        self._url = url
        self._backdrop = backdrop if backdrop is not None else white
        self._space_before = space_before
        self._space_after = space_after
        self._text_left = icon_size + gap
        self._lines = wrap(text, style, max(width - self._text_left, 1.0))

    @property
    def height(self) -> float:
        """Height in points, never less than the icon itself."""
        text_height = len(self._lines) * self._style.leading
        return (
            self._space_before + max(text_height, self._icon_size) + self._space_after
        )

    def draw(self, canvas: Canvas, x: float, top: float) -> None:
        """Draw the icon, the text, and an optional link annotation."""
        style = self._style
        content_top = top - self._space_before
        text_height = len(self._lines) * style.leading
        block_height = max(text_height, self._icon_size)

        icon_y = content_top - (block_height + self._icon_size) / 2.0
        draw_icon(
            canvas, self._icon, x, icon_y, self._icon_size, style.fill, self._backdrop
        )

        cursor = content_top - max((block_height - text_height) / 2.0, 0.0)
        offset = baseline_offset(style)
        text_x = x + self._text_left
        text_width_avail = self._width - self._text_left
        for line in self._lines:
            draw_line(canvas, text_x, cursor - offset, line, style, text_width_avail)
            cursor -= style.leading

        if self._url:
            canvas.linkURL(
                self._url,
                (x, content_top - block_height, x + self._width, content_top),
                relative=0,
                thickness=0,
            )


class DetailRow(Block):
    """A label/value pair laid out in two sub-columns."""

    def __init__(
        self,
        label: str,
        value: str,
        label_style: TextStyle,
        value_style: TextStyle,
        width: float,
        label_ratio: float = 0.52,
        space_before: float = 0.0,
        space_after: float = 0.0,
    ) -> None:
        """Wrap both halves of the row.

        Args:
            label: Left-hand label.
            value: Right-hand value.
            label_style: Style for the label.
            value_style: Style for the value.
            width: Full row width in points.
            label_ratio: Fraction of the width given to the label column.
            space_before: Gap above the row.
            space_after: Gap below the row.
        """
        self._label_style = label_style
        self._value_style = value_style
        self._space_before = space_before
        self._space_after = space_after
        self._label_width = max(width * label_ratio, 1.0)
        self._value_left = self._label_width
        self._value_width = max(width - self._value_left, 1.0)
        self._label_lines = wrap(label, label_style, self._label_width)
        self._value_lines = wrap(value, value_style, self._value_width)

    @property
    def height(self) -> float:
        """Height of the taller of the two columns."""
        label_height = len(self._label_lines) * self._label_style.leading
        value_height = len(self._value_lines) * self._value_style.leading
        return self._space_before + max(label_height, value_height) + self._space_after

    def draw(self, canvas: Canvas, x: float, top: float) -> None:
        """Draw the label and the value side by side."""
        content_top = top - self._space_before
        for lines, style, left in (
            (self._label_lines, self._label_style, 0.0),
            (self._value_lines, self._value_style, self._value_left),
        ):
            cursor = content_top
            offset = baseline_offset(style)
            for line in lines:
                draw_line(canvas, x + left, cursor - offset, line, style)
                cursor -= style.leading


class SkillGrid(Block):
    """A fixed-column grid of skill names with optional rating bars."""

    def __init__(
        self,
        names: Sequence[str],
        style: TextStyle,
        width: float,
        columns: int = 2,
        levels: Sequence[int | None] = (),
        accent: Color | None = None,
        track: Color | None = None,
        row_gap: float = 4.0,
        column_gap: float = 8.0,
    ) -> None:
        """Pre-compute the grid geometry.

        Args:
            names: Skill names in reading order.
            style: Text style for the names.
            width: Full grid width in points.
            columns: Number of columns.
            levels: Optional 0-5 ratings aligned with ``names``.
            accent: Fill colour for the filled part of a rating bar.
            track: Fill colour for the empty part of a rating bar.
            row_gap: Vertical gap between rows.
            column_gap: Horizontal gap between columns.
        """
        self._names = list(names)
        self._style = style
        self._width = width
        self._columns = max(columns, 1)
        self._levels = list(levels)
        self._accent = accent
        self._track = track
        self._row_gap = row_gap
        self._column_gap = column_gap
        self._show_levels = accent is not None and any(
            level is not None for level in self._levels
        )
        self._bar_height = 3.0
        self._bar_gap = 3.0

    @property
    def _cell_width(self) -> float:
        """Width of a single grid cell."""
        total_gap = self._column_gap * (self._columns - 1)
        return max((self._width - total_gap) / self._columns, 1.0)

    @property
    def _row_height(self) -> float:
        """Height of a single grid row."""
        base = self._style.leading
        if self._show_levels:
            base += self._bar_gap + self._bar_height
        return base + self._row_gap

    @property
    def _rows(self) -> int:
        """Number of grid rows."""
        return math.ceil(len(self._names) / self._columns) if self._names else 0

    @property
    def height(self) -> float:
        """Height in points, zero when there are no skills."""
        if not self._names:
            return 0.0
        return self._rows * self._row_height - self._row_gap

    def _level_for(self, index: int) -> int | None:
        """Return the rating for ``index`` if one was supplied.

        Args:
            index: Position in the name list.

        Returns:
            The rating, or ``None``.
        """
        if index >= len(self._levels):
            return None
        return self._levels[index]

    def draw(self, canvas: Canvas, x: float, top: float) -> None:
        """Draw every skill cell, row by row."""
        style = self._style
        offset = baseline_offset(style)
        cell = self._cell_width

        for index, name in enumerate(self._names):
            row, column = divmod(index, self._columns)
            cell_x = x + column * (cell + self._column_gap)
            cell_top = top - row * self._row_height
            draw_line(canvas, cell_x, cell_top - offset, name, style, cell)

            level = self._level_for(index)
            if not self._show_levels or level is None or self._accent is None:
                continue
            bar_y = cell_top - style.leading - self._bar_gap - self._bar_height
            canvas.saveState()
            if self._track is not None:
                canvas.setFillColor(self._track)
                canvas.rect(cell_x, bar_y, cell, self._bar_height, stroke=0, fill=1)
            canvas.setFillColor(self._accent)
            filled = cell * (max(0, min(level, 5)) / 5.0)
            if filled > 0:
                canvas.rect(cell_x, bar_y, filled, self._bar_height, stroke=0, fill=1)
            canvas.restoreState()


class Portrait(Block):
    """A circular portrait, horizontally centred in its column."""

    def __init__(
        self,
        reader: ImageReader,
        diameter: float,
        width: float,
        space_before: float = 0.0,
        space_after: float = 0.0,
    ) -> None:
        """Initialise the portrait.

        Args:
            reader: Image to draw, already square.
            diameter: Circle diameter in points.
            width: Column width used for centring.
            space_before: Gap above the portrait.
            space_after: Gap below the portrait.
        """
        self._reader = reader
        self._diameter = diameter
        self._width = width
        self._space_before = space_before
        self._space_after = space_after

    @property
    def height(self) -> float:
        """Height in points."""
        return self._space_before + self._diameter + self._space_after

    def draw(self, canvas: Canvas, x: float, top: float) -> None:
        """Clip to a circle and draw the image inside it."""
        left = x + (self._width - self._diameter) / 2.0
        bottom = top - self._space_before - self._diameter
        radius = self._diameter / 2.0

        canvas.saveState()
        path = canvas.beginPath()
        path.circle(left + radius, bottom + radius, radius)
        canvas.clipPath(path, stroke=0, fill=0)
        canvas.drawImage(
            self._reader,
            left,
            bottom,
            self._diameter,
            self._diameter,
            preserveAspectRatio=True,
            anchor="c",
            mask="auto",
        )
        canvas.restoreState()
