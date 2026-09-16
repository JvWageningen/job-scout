"""Every icon must actually draw.

The dashboard lets any section pick any icon, so an icon whose path is never
exercised is a crash (or a blank box) waiting for the first user who selects it.
"""

from __future__ import annotations

import io

import pymupdf
import pytest
from reportlab.lib.colors import HexColor
from reportlab.pdfgen.canvas import Canvas

from job_scout.cv.models import ContactItem, ContactSection, CVDocument, IconName
from job_scout.cv.render.icons import draw_icon, icon_names
from tests.cv.conftest import open_pdf, render_bytes


def test_icon_names_are_not_empty() -> None:
    assert len(icon_names()) == 16


@pytest.mark.parametrize("name", icon_names())
def test_icon_draws_visible_ink(name: str) -> None:
    """Each icon paints something inside its box and nothing outside it."""
    buffer = io.BytesIO()
    canvas = Canvas(buffer, pagesize=(100, 100))
    canvas.setFillColor(HexColor("#FFFFFF"))
    canvas.rect(0, 0, 100, 100, stroke=0, fill=1)
    draw_icon(
        canvas,
        name,  # type: ignore[arg-type]
        20,
        20,
        60,
        HexColor("#000000"),
        HexColor("#FFFFFF"),
    )
    canvas.showPage()
    canvas.save()

    with pymupdf.open(stream=buffer.getvalue(), filetype="pdf") as document:
        pixmap = document[0].get_pixmap(dpi=144)

    scale = pixmap.width / 100.0
    dark_inside = 0
    for y in range(pixmap.height):
        for x in range(pixmap.width):
            pixel = pixmap.pixel(x, y)
            if sum(pixel[:3]) >= 3 * 200:
                continue
            # PDF y grows upwards; the pixmap's does not.
            box_x = x / scale
            box_y = (pixmap.height - y) / scale
            assert 18 <= box_x <= 82, f"{name} paints outside its box at x={box_x:.1f}"
            assert 18 <= box_y <= 82, f"{name} paints outside its box at y={box_y:.1f}"
            dark_inside += 1

    assert dark_inside > 40, f"{name} drew almost nothing"


def test_unknown_icon_is_skipped_not_raised() -> None:
    buffer = io.BytesIO()
    canvas = Canvas(buffer, pagesize=(50, 50))
    draw_icon(canvas, "not-an-icon", 0, 0, 20, HexColor("#000000"))  # type: ignore[arg-type]
    canvas.save()
    assert buffer.getvalue().startswith(b"%PDF")


@pytest.mark.parametrize("name", icon_names())
def test_every_icon_survives_a_full_render(name: str) -> None:
    """A contact row with any icon still produces a readable PDF."""
    icon: IconName = name  # type: ignore[assignment]
    doc = CVDocument(
        full_name="Icon Test",
        sidebar=[
            ContactSection(
                title="Contact",
                icon=icon,
                items=[ContactItem(icon=icon, value="value here")],
            )
        ],
    )
    with open_pdf(render_bytes(doc)) as document:
        assert "VALUE HERE" in document[0].get_text().upper()
