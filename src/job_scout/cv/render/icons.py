"""Vector icons drawn straight onto the canvas.

Every icon is defined inside a 100x100 unit box with the origin at its bottom-left
corner; :func:`draw_icon` handles translating and scaling to the requested size. No
icon font is needed, so a rendered CV has no external asset dependencies.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from reportlab.lib.colors import Color, white
from reportlab.pdfgen.canvas import Canvas

from job_scout.cv.models import IconName

UNIT = 100.0
_STROKE = 8.0


def _person(c: Canvas, fill: Color, bg: Color) -> None:
    """Draw a head-and-shoulders silhouette."""
    c.setFillColor(fill)
    c.circle(50, 72, 18, stroke=0, fill=1)
    path = c.beginPath()
    path.moveTo(14, 6)
    path.curveTo(14, 36, 30, 48, 50, 48)
    path.curveTo(70, 48, 86, 36, 86, 6)
    path.close()
    c.drawPath(path, stroke=0, fill=1)


def _laptop(c: Canvas, fill: Color, bg: Color) -> None:
    """Draw an open laptop."""
    c.setStrokeColor(fill)
    c.setFillColor(fill)
    c.setLineWidth(_STROKE)
    c.setLineJoin(1)
    c.rect(24, 36, 52, 42, stroke=1, fill=0)
    base = c.beginPath()
    base.moveTo(10, 22)
    base.lineTo(90, 22)
    base.lineTo(78, 36)
    base.lineTo(22, 36)
    base.close()
    c.drawPath(base, stroke=1, fill=1)


def _graduation(c: Canvas, fill: Color, bg: Color) -> None:
    """Draw a mortarboard with a tassel."""
    c.setFillColor(fill)
    c.setStrokeColor(fill)
    board = c.beginPath()
    board.moveTo(50, 84)
    board.lineTo(96, 64)
    board.lineTo(50, 44)
    board.lineTo(4, 64)
    board.close()
    c.drawPath(board, stroke=0, fill=1)

    body = c.beginPath()
    body.moveTo(24, 56)
    body.lineTo(24, 32)
    body.curveTo(24, 16, 76, 16, 76, 32)
    body.lineTo(76, 56)
    body.lineTo(50, 44)
    body.close()
    c.drawPath(body, stroke=0, fill=1)

    c.setLineWidth(5)
    c.line(90, 68, 90, 34)
    c.circle(90, 30, 6, stroke=0, fill=1)


def _envelope(c: Canvas, fill: Color, bg: Color) -> None:
    """Draw a sealed envelope."""
    c.setStrokeColor(fill)
    c.setLineWidth(_STROKE)
    c.setLineJoin(1)
    c.rect(8, 22, 84, 56, stroke=1, fill=0)
    flap = c.beginPath()
    flap.moveTo(8, 78)
    flap.lineTo(50, 44)
    flap.lineTo(92, 78)
    c.drawPath(flap, stroke=1, fill=0)


def _phone(c: Canvas, fill: Color, bg: Color) -> None:
    """Draw a handset with signal arcs."""
    c.setFillColor(fill)
    c.setStrokeColor(fill)
    c.setLineWidth(_STROKE)
    c.setLineCap(1)
    handset = c.beginPath()
    handset.moveTo(20, 74)
    handset.lineTo(38, 74)
    handset.lineTo(44, 56)
    handset.lineTo(32, 48)
    handset.curveTo(38, 32, 50, 20, 66, 14)
    handset.lineTo(74, 26)
    handset.lineTo(92, 20)
    handset.lineTo(92, 4)
    handset.curveTo(46, 4, 14, 34, 20, 74)
    handset.close()
    c.drawPath(handset, stroke=0, fill=1)


def _linkedin(c: Canvas, fill: Color, bg: Color) -> None:
    """Draw a filled LinkedIn tile with a punched-out ``in``."""
    c.setFillColor(fill)
    c.roundRect(6, 6, 88, 88, 16, stroke=0, fill=1)
    c.setFillColor(bg)
    c.setFont("Helvetica-Bold", 56)
    c.drawCentredString(52, 30, "in")


def _github(c: Canvas, fill: Color, bg: Color) -> None:
    """Draw a simplified GitHub mark."""
    c.setFillColor(fill)
    c.circle(50, 54, 42, stroke=0, fill=1)
    c.setFillColor(bg)
    c.circle(36, 62, 7, stroke=0, fill=1)
    c.circle(64, 62, 7, stroke=0, fill=1)
    c.setLineWidth(7)
    c.setStrokeColor(bg)
    c.line(34, 26, 34, 40)
    c.line(66, 26, 66, 40)


def _globe(c: Canvas, fill: Color, bg: Color) -> None:
    """Draw a globe with a meridian and the equator."""
    c.setStrokeColor(fill)
    c.setLineWidth(_STROKE)
    c.circle(50, 50, 42, stroke=1, fill=0)
    c.line(8, 50, 92, 50)
    c.setLineWidth(6)
    path = c.beginPath()
    path.moveTo(50, 92)
    path.curveTo(24, 66, 24, 34, 50, 8)
    path.curveTo(76, 34, 76, 66, 50, 92)
    c.drawPath(path, stroke=1, fill=0)


def _location(c: Canvas, fill: Color, bg: Color) -> None:
    """Draw a map pin."""
    c.setFillColor(fill)
    pin = c.beginPath()
    pin.moveTo(50, 4)
    pin.curveTo(22, 40, 14, 54, 14, 66)
    pin.curveTo(14, 86, 30, 96, 50, 96)
    pin.curveTo(70, 96, 86, 86, 86, 66)
    pin.curveTo(86, 54, 78, 40, 50, 4)
    pin.close()
    c.drawPath(pin, stroke=0, fill=1)
    c.setFillColor(bg)
    c.circle(50, 66, 15, stroke=0, fill=1)


def _briefcase(c: Canvas, fill: Color, bg: Color) -> None:
    """Draw a briefcase."""
    c.setStrokeColor(fill)
    c.setFillColor(fill)
    c.setLineWidth(_STROKE)
    c.setLineJoin(1)
    c.rect(8, 14, 84, 54, stroke=1, fill=0)
    handle = c.beginPath()
    handle.moveTo(36, 68)
    handle.lineTo(36, 84)
    handle.lineTo(64, 84)
    handle.lineTo(64, 68)
    c.drawPath(handle, stroke=1, fill=0)
    c.rect(8, 36, 84, 8, stroke=0, fill=1)


def _book(c: Canvas, fill: Color, bg: Color) -> None:
    """Draw an open book."""
    c.setStrokeColor(fill)
    c.setLineWidth(_STROKE)
    c.setLineJoin(1)
    path = c.beginPath()
    path.moveTo(50, 24)
    path.curveTo(36, 12, 20, 12, 8, 16)
    path.lineTo(8, 76)
    path.curveTo(20, 72, 36, 72, 50, 84)
    path.curveTo(64, 72, 80, 72, 92, 76)
    path.lineTo(92, 16)
    path.curveTo(80, 12, 64, 12, 50, 24)
    path.close()
    c.drawPath(path, stroke=1, fill=0)
    c.line(50, 24, 50, 84)


def _star(c: Canvas, fill: Color, bg: Color) -> None:
    """Draw a five-pointed star."""
    c.setFillColor(fill)
    path = c.beginPath()
    for index in range(10):
        radius = 46.0 if index % 2 == 0 else 19.0
        angle = math.pi / 2 + index * math.pi / 5
        point = (50 + radius * math.cos(angle), 50 + radius * math.sin(angle))
        if index == 0:
            path.moveTo(*point)
            continue
        path.lineTo(*point)
    path.close()
    c.drawPath(path, stroke=0, fill=1)


def _wrench(c: Canvas, fill: Color, bg: Color) -> None:
    """Draw a diagonal wrench."""
    c.setStrokeColor(fill)
    c.setLineWidth(20)
    c.setLineCap(1)
    c.line(30, 30, 74, 74)
    c.setFillColor(fill)
    c.circle(26, 26, 16, stroke=0, fill=1)
    c.setFillColor(bg)
    c.circle(26, 26, 6, stroke=0, fill=1)


def _language(c: Canvas, fill: Color, bg: Color) -> None:
    """Draw two overlapping speech bubbles."""
    c.setFillColor(fill)
    c.roundRect(6, 34, 58, 46, 12, stroke=0, fill=1)
    tail = c.beginPath()
    tail.moveTo(20, 36)
    tail.lineTo(20, 14)
    tail.lineTo(40, 36)
    tail.close()
    c.drawPath(tail, stroke=0, fill=1)
    c.setStrokeColor(fill)
    c.setFillColor(bg)
    c.setLineWidth(7)
    c.roundRect(44, 18, 50, 40, 12, stroke=1, fill=1)


def _certificate(c: Canvas, fill: Color, bg: Color) -> None:
    """Draw a certificate with a ribbon."""
    c.setStrokeColor(fill)
    c.setFillColor(fill)
    c.setLineWidth(_STROKE)
    c.rect(12, 34, 76, 58, stroke=1, fill=0)
    c.setLineWidth(6)
    c.line(26, 76, 74, 76)
    c.line(26, 60, 62, 60)
    ribbon = c.beginPath()
    ribbon.moveTo(38, 34)
    ribbon.lineTo(38, 6)
    ribbon.lineTo(50, 16)
    ribbon.lineTo(62, 6)
    ribbon.lineTo(62, 34)
    ribbon.close()
    c.drawPath(ribbon, stroke=0, fill=1)


def _heart(c: Canvas, fill: Color, bg: Color) -> None:
    """Draw a heart."""
    c.setFillColor(fill)
    path = c.beginPath()
    path.moveTo(50, 16)
    path.curveTo(14, 44, 6, 60, 6, 70)
    path.curveTo(6, 88, 32, 96, 50, 76)
    path.curveTo(68, 96, 94, 88, 94, 70)
    path.curveTo(94, 60, 86, 44, 50, 16)
    path.close()
    c.drawPath(path, stroke=0, fill=1)


_ICONS: dict[IconName, Callable[[Canvas, Color, Color], None]] = {
    "person": _person,
    "laptop": _laptop,
    "graduation": _graduation,
    "envelope": _envelope,
    "phone": _phone,
    "linkedin": _linkedin,
    "github": _github,
    "globe": _globe,
    "location": _location,
    "briefcase": _briefcase,
    "book": _book,
    "star": _star,
    "wrench": _wrench,
    "language": _language,
    "certificate": _certificate,
    "heart": _heart,
}


def icon_names() -> list[str]:
    """List every icon this module can draw.

    Returns:
        Sorted icon names.
    """
    return sorted(_ICONS)


def draw_icon(
    canvas: Canvas,
    name: IconName,
    x: float,
    y: float,
    size: float,
    fill: Color,
    bg: Color = white,
) -> None:
    """Draw ``name`` into a square box of ``size`` points.

    Unknown names are skipped rather than raising, so a document that references a
    retired icon still renders.

    Args:
        canvas: Target canvas.
        name: Icon to draw.
        x: Left edge of the box.
        y: Bottom edge of the box, in ReportLab (bottom-up) space.
        size: Box side length in points.
        fill: Colour of the icon strokes and fills.
        bg: Colour used for punched-out details, normally the backdrop colour.
    """
    painter = _ICONS.get(name)
    if painter is None:
        return

    canvas.saveState()
    canvas.translate(x, y)
    canvas.scale(size / UNIT, size / UNIT)
    painter(canvas, fill, bg)
    canvas.restoreState()
