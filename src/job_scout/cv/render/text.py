"""Text styling, measurement and word wrapping on top of ReportLab primitives.

The renderer draws every string itself rather than going through Platypus, so it
needs its own measuring and wrapping. Letter spacing is applied with the PDF ``Tc``
operator (``Canvas.setCharSpace``), which ReportLab's ``stringWidth`` does not know
about - :func:`text_width` compensates for that.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

from reportlab.lib.colors import Color, HexColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen.canvas import Canvas

type Align = Literal["left", "center", "right"]


def colour(value: str) -> Color:
    """Convert a ``#rrggbb`` string into a ReportLab colour.

    Args:
        value: Hex colour string.

    Returns:
        The corresponding :class:`~reportlab.lib.colors.Color`.
    """
    return HexColor(value)


@dataclass(frozen=True)
class TextStyle:
    """Everything needed to draw and measure a run of text.

    Attributes:
        font: Registered ReportLab face name.
        size: Font size in points.
        fill: Text colour.
        leading: Baseline-to-baseline distance in points.
        char_space: Extra spacing between characters, in points.
        upper: Whether to upper-case the text before drawing.
        space_before: Vertical gap inserted above the block, in points.
        space_after: Vertical gap inserted below the block, in points.
    """

    font: str
    size: float
    fill: Color
    leading: float
    char_space: float = 0.0
    upper: bool = False
    space_before: float = 0.0
    space_after: float = 0.0

    def scaled(self, factor: float) -> TextStyle:
        """Return a copy with the size and leading multiplied by ``factor``.

        Args:
            factor: Multiplier to apply.

        Returns:
            A new style.
        """
        return replace(self, size=self.size * factor, leading=self.leading * factor)


def prepare(text: str, style: TextStyle) -> str:
    """Apply case transforms from ``style`` to ``text``.

    Args:
        text: Raw text.
        style: Style whose ``upper`` flag is honoured.

    Returns:
        The transformed text.
    """
    return text.upper() if style.upper else text


def text_width(text: str, style: TextStyle) -> float:
    """Measure the drawn width of ``text``, including letter spacing.

    ``Tc`` adds spacing after every glyph, but the trailing gap leaves no ink, so
    it is excluded here to keep centring visually correct.

    Args:
        text: Text to measure (case transform is applied first).
        style: Style the text will be drawn with.

    Returns:
        Width in points.
    """
    shaped = prepare(text, style)
    if not shaped:
        return 0.0
    base = float(pdfmetrics.stringWidth(shaped, style.font, style.size))
    return base + style.char_space * (len(shaped) - 1)


def baseline_offset(style: TextStyle) -> float:
    """Distance from the top of a line box down to the text baseline.

    Uses the CSS line-box model: the leftover space between the leading and the
    font's own ascent-to-descent height is split evenly above and below.

    Args:
        style: Style whose font metrics and leading are used.

    Returns:
        Offset in points, measured downwards from the top of the line box.
    """
    ascent = float(pdfmetrics.getAscent(style.font, style.size))
    descent = float(pdfmetrics.getDescent(style.font, style.size))
    return (style.leading - (ascent - descent)) / 2.0 + ascent


def _split_long_word(word: str, style: TextStyle, width: float) -> list[str]:
    """Hard-break a single word that cannot fit on one line.

    Args:
        word: The oversized word.
        style: Style used for measurement.
        width: Available width in points.

    Returns:
        The word broken into pieces that each fit, never an empty list.
    """
    pieces: list[str] = []
    current = ""
    for char in word:
        candidate = current + char
        if current and text_width(candidate, style) > width:
            pieces.append(current)
            current = char
            continue
        current = candidate
    if current:
        pieces.append(current)
    return pieces or [word]


def _start_line(word: str, style: TextStyle, width: float, lines: list[str]) -> str:
    """Begin a fresh line with ``word``, hard-breaking it if it is oversized.

    Any complete pieces of a broken word are appended to ``lines`` directly.

    Args:
        word: The word opening the new line.
        style: Style used for measurement.
        width: Available width in points.
        lines: Output list, extended in place with any full pieces.

    Returns:
        The text that remains on the (still open) current line.
    """
    if text_width(word, style) <= width:
        return word
    pieces = _split_long_word(word, style, width)
    lines.extend(pieces[:-1])
    return pieces[-1]


def wrap(text: str, style: TextStyle, width: float) -> list[str]:
    """Greedily wrap ``text`` to ``width``, honouring explicit newlines.

    Args:
        text: Text to wrap. ``\\n`` forces a line break; blank lines are kept.
        style: Style used for measurement.
        width: Available width in points.

    Returns:
        One string per rendered line. Empty input yields an empty list.
    """
    if not text.strip():
        return []
    if width <= 0:
        return [text]

    lines: list[str] = []
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if not raw_line.strip():
            lines.append("")
            continue

        current = ""
        for word in raw_line.split():
            if current:
                candidate = f"{current} {word}"
                if text_width(candidate, style) <= width:
                    current = candidate
                    continue
                lines.append(current)
            current = _start_line(word, style, width, lines)
        if current:
            lines.append(current)
    return lines


def draw_line(
    canvas: Canvas,
    x: float,
    baseline: float,
    text: str,
    style: TextStyle,
    width: float = 0.0,
    align: Align = "left",
) -> None:
    """Draw one already-wrapped line of text.

    Args:
        canvas: Target canvas.
        x: Left edge of the text box.
        baseline: Baseline y coordinate, in ReportLab (bottom-up) space.
        text: The line to draw.
        style: Style to draw with.
        width: Width of the text box, used for centring and right alignment.
        align: Horizontal alignment within ``width``.
    """
    shaped = prepare(text, style)
    if not shaped:
        return

    if align == "center":
        x += (width - text_width(text, style)) / 2.0
    elif align == "right":
        x += width - text_width(text, style)

    # Letter spacing is only reachable through a text object in ReportLab 5, so
    # every line goes through one rather than Canvas.drawString.
    textobject = canvas.beginText()
    textobject.setTextOrigin(x, baseline)
    textobject.setFont(style.font, style.size)
    textobject.setFillColor(style.fill)
    # Tc lives in the PDF graphics state, not the text object, so it survives
    # BT/ET. It must be set every time - including back to zero - or a letter
    # spaced heading silently bleeds into the next run of text.
    textobject.setCharSpace(style.char_space)
    textobject.textOut(shaped)
    canvas.drawText(textobject)
