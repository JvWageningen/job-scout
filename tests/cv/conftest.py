"""Shared fixtures."""

from __future__ import annotations

import io
from collections.abc import Iterator
from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from job_scout.cv.app import create_app
from job_scout.cv.models import CVDocument
from job_scout.cv.render import render_pdf
from job_scout.cv.sample import sample_cv
from job_scout.cv.storage import ProfileStore


@pytest.fixture
def store(tmp_path: Path) -> ProfileStore:
    """A profile store rooted in a throwaway directory."""
    return ProfileStore(tmp_path / "data")


@pytest.fixture
def client(store: ProfileStore) -> Iterator[TestClient]:
    """A test client wired to the temporary store."""
    with TestClient(create_app(store)) as test_client:
        yield test_client


@pytest.fixture
def cv() -> CVDocument:
    """The bundled sample CV, without a portrait."""
    doc = sample_cv()
    doc.photo = ""
    return doc


def render_bytes(doc: CVDocument, photo: Path | None = None) -> bytes:
    """Render a document and return the PDF payload.

    Args:
        doc: Document to render.
        photo: Optional portrait path.

    Returns:
        PDF bytes.
    """
    buffer = io.BytesIO()
    render_pdf(doc, buffer, photo)
    return buffer.getvalue()


def open_pdf(payload: bytes) -> pymupdf.Document:
    """Open PDF bytes with PyMuPDF.

    Args:
        payload: PDF bytes.

    Returns:
        The opened document.
    """
    return pymupdf.open(stream=payload, filetype="pdf")


def page_text(payload: bytes, index: int = 0) -> str:
    """Extract the text of one page.

    Args:
        payload: PDF bytes.
        index: Zero-based page number.

    Returns:
        The page's text.
    """
    with open_pdf(payload) as document:
        return str(document[index].get_text())


def assert_colour(
    actual: tuple[int, ...], expected: tuple[int, int, int], tolerance: int = 3
) -> None:
    """Assert two RGB triples match, allowing for PDF colour rounding.

    ReportLab stores colours as floats, so a rasterised ``#008037`` can come back
    as ``(0, 128, 54)``. Exact equality is too strict to be useful here.

    Args:
        actual: Sampled pixel.
        expected: Expected RGB triple.
        tolerance: Permitted per-channel difference.
    """
    assert len(actual) >= 3, f"expected an RGB pixel, got {actual!r}"
    for index, channel in enumerate(expected):
        assert abs(actual[index] - channel) <= tolerance, (
            f"channel {index}: {actual[:3]} is not within {tolerance} of {expected}"
        )


def make_image(width: int, height: int, colour: tuple[int, int, int]) -> bytes:
    """Build a solid-colour PNG.

    Args:
        width: Image width in pixels.
        height: Image height in pixels.
        colour: RGB fill.

    Returns:
        PNG bytes.
    """
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buffer, format="PNG")
    return buffer.getvalue()
