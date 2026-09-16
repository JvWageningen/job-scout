"""Column pagination."""

from __future__ import annotations

from reportlab.pdfgen.canvas import Canvas

from job_scout.cv.render.blocks import Block, Spacer
from job_scout.cv.render.frame import Column, paginate

COLUMN = Column(x=0.0, top=100.0, width=200.0, bottom=0.0)


class Fixed(Block):
    """A block of a fixed height, for exercising the packer."""

    def __init__(
        self, height: float, keep_with_next: bool = False, tag: str = ""
    ) -> None:
        self._height = height
        self.keep_with_next = keep_with_next
        self.tag = tag

    @property
    def height(self) -> float:
        return self._height

    def draw(self, canvas: Canvas, x: float, top: float) -> None:
        """Draw nothing; these blocks only exist to occupy space."""


def tags(pages: list[list[object]]) -> list[list[str]]:
    """Reduce placements to their block tags, page by page."""
    return [[placement.block.tag for placement in page] for page in pages]  # type: ignore[attr-defined]


def test_blocks_that_fit_stay_on_one_page() -> None:
    pages = paginate([Fixed(30, tag="a"), Fixed(30, tag="b")], COLUMN)
    assert tags(pages) == [["a", "b"]]


def test_overflow_starts_a_new_page() -> None:
    pages = paginate([Fixed(60, tag="a"), Fixed(60, tag="b")], COLUMN)
    assert tags(pages) == [["a"], ["b"]]


def test_placements_descend_from_the_column_top() -> None:
    pages = paginate([Fixed(30, tag="a"), Fixed(20, tag="b")], COLUMN)
    assert [placement.top for placement in pages[0]] == [100.0, 70.0]


def test_keep_with_next_moves_the_whole_group() -> None:
    blocks = [
        Fixed(50, tag="filler"),
        Fixed(30, keep_with_next=True, tag="bar"),
        Fixed(30, tag="body"),
    ]
    # bar+body need 60pt but only 50pt is left, so both move together.
    assert tags(paginate(blocks, COLUMN)) == [["filler"], ["bar", "body"]]


def test_a_group_taller_than_the_column_is_split() -> None:
    blocks = [
        Fixed(70, keep_with_next=True, tag="a"),
        Fixed(70, tag="b"),
    ]
    assert tags(paginate(blocks, COLUMN)) == [["a"], ["b"]]


def test_a_single_oversized_block_does_not_loop_forever() -> None:
    pages = paginate([Fixed(250, tag="huge"), Fixed(10, tag="after")], COLUMN)
    assert tags(pages) == [["huge"], ["after"]]


def test_spacers_are_dropped_at_the_top_of_a_page() -> None:
    blocks = [Fixed(80, tag="a"), Spacer(40), Fixed(30, tag="b")]
    pages = paginate(blocks, COLUMN)
    # The spacer would have opened page two with a stray margin.
    assert tags(pages) == [["a"], ["b"]]
    assert pages[1][0].top == COLUMN.top


def test_empty_input_yields_one_empty_page() -> None:
    assert paginate([], COLUMN) == [[]]
