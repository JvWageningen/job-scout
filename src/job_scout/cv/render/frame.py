"""Packing blocks into paginated columns.

The sidebar and the main column are paginated independently - the CV is not a
single reflowing text stream, so a long work history must not push the sidebar
around. Blocks are never split; instead, runs of blocks joined by
``keep_with_next`` are kept together, which stops a section bar from being orphaned
at the foot of a page.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from reportlab.pdfgen.canvas import Canvas

from job_scout.cv.render.blocks import Block, Spacer

_EPSILON = 1e-6


@dataclass(frozen=True)
class Column:
    """A rectangular region that blocks flow into, top to bottom.

    Attributes:
        x: Left edge in points.
        top: Top edge in ReportLab (bottom-up) space.
        width: Usable width in points.
        bottom: Lowest y a block may occupy.
    """

    x: float
    top: float
    width: float
    bottom: float

    @property
    def height(self) -> float:
        """Usable vertical space in points."""
        return self.top - self.bottom


@dataclass(frozen=True)
class Placement:
    """A block together with the y coordinate of its top edge."""

    block: Block
    top: float


def _groups(blocks: Iterable[Block]) -> list[list[Block]]:
    """Collect blocks into runs that must stay on the same page.

    A run ends at the first block whose ``keep_with_next`` is False.

    Args:
        blocks: Blocks in document order.

    Returns:
        A list of runs, each with at least one block.
    """
    groups: list[list[Block]] = []
    current: list[Block] = []
    for block in blocks:
        current.append(block)
        if not block.keep_with_next:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def paginate(blocks: Sequence[Block], column: Column) -> list[list[Placement]]:
    """Lay ``blocks`` out down ``column``, starting a new page when it fills.

    Args:
        blocks: Blocks in document order.
        column: Geometry of the column on every page.

    Returns:
        One list of placements per page. Always at least one (possibly empty) page.
    """
    pages: list[list[Placement]] = [[]]
    cursor = column.top

    def at_page_start() -> bool:
        """Whether nothing has been placed on the current page yet."""
        return cursor >= column.top - _EPSILON

    def fits(amount: float) -> bool:
        """Whether ``amount`` of height still fits above the column floor."""
        return amount <= cursor - column.bottom + _EPSILON

    def start_page() -> None:
        """Open a fresh page and reset the cursor."""
        nonlocal cursor
        pages.append([])
        cursor = column.top

    def place(block: Block) -> None:
        """Put a block at the cursor and advance past it."""
        nonlocal cursor
        # A spacer that lands at the very top of a page would look like a stray
        # margin, so it is dropped instead.
        if isinstance(block, Spacer) and at_page_start():
            return
        pages[-1].append(Placement(block, cursor))
        cursor -= block.height

    for group in _groups(blocks):
        needed = sum(block.height for block in group)

        if needed > column.height + _EPSILON:
            # Taller than a whole column, so it can never be kept together;
            # fall back to flowing its blocks individually.
            for block in group:
                if not fits(block.height) and not at_page_start():
                    start_page()
                place(block)
            continue

        if not fits(needed) and not at_page_start():
            start_page()
        for block in group:
            place(block)

    return pages


def draw_page(canvas: Canvas, placements: Sequence[Placement], column: Column) -> None:
    """Draw one page worth of placements.

    Args:
        canvas: Target ReportLab canvas.
        placements: Blocks and their top edges.
        column: Column supplying the left edge.
    """
    for placement in placements:
        placement.block.draw(canvas, column.x, placement.top)
