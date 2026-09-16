"""Loading and normalising portrait images.

Uploads arrive in whatever the user's phone or camera produced, so they are
EXIF-rotated, centre-cropped to a square and downscaled before they reach the PDF.
The circular mask itself is a vector clip applied by the renderer, which keeps the
edge crisp at any zoom level.
"""

from __future__ import annotations

import io
from pathlib import Path

from loguru import logger
from PIL import Image, ImageOps, UnidentifiedImageError
from reportlab.lib.utils import ImageReader

MAX_EDGE = 1400
"""Longest edge kept after downscaling, in pixels. 1400px across a ~150pt circle is
roughly 670 dpi - far beyond print need, but cheap and safe against upscaling."""

ALLOWED_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"})


class ImageError(ValueError):
    """Raised when an image cannot be decoded or is not usable as a portrait."""


def load_square(data: bytes) -> Image.Image:
    """Decode ``data`` and return an upright, centre-cropped square image.

    Args:
        data: Raw image bytes.

    Returns:
        An RGB :class:`PIL.Image.Image` whose width equals its height.

    Raises:
        ImageError: If the bytes are not a decodable image.
    """
    image: Image.Image
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImageError(f"Could not read image: {exc}") from exc

    image = ImageOps.exif_transpose(image)
    if image is None:  # pragma: no cover - defensive, exif_transpose kept the image
        raise ImageError("Could not normalise image orientation")

    if image.mode not in {"RGB", "RGBA"}:
        image = image.convert("RGBA" if "A" in image.getbands() else "RGB")

    edge = min(image.width, image.height)
    if edge <= 0:
        raise ImageError("Image has no pixels")

    left = (image.width - edge) // 2
    top = (image.height - edge) // 2
    image = image.crop((left, top, left + edge, top + edge))

    if edge > MAX_EDGE:
        image = image.resize((MAX_EDGE, MAX_EDGE), Image.Resampling.LANCZOS)

    return image.convert("RGB")


def to_reader(image: Image.Image) -> ImageReader:
    """Wrap a PIL image in something ReportLab can draw.

    Args:
        image: Source image.

    Returns:
        An :class:`~reportlab.lib.utils.ImageReader` over PNG bytes.
    """
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return ImageReader(buffer)


def reader_from_path(path: Path) -> ImageReader | None:
    """Load a portrait from disk, returning ``None`` when it is unusable.

    A broken or missing portrait must never abort a render - the CV is simply
    drawn without it.

    Args:
        path: Location of the image file.

    Returns:
        An image reader, or ``None`` if the file is missing or undecodable.
    """
    if not path.is_file():
        logger.warning("Portrait not found: {}", path)
        return None
    try:
        return to_reader(load_square(path.read_bytes()))
    except (ImageError, OSError) as exc:
        logger.warning("Ignoring unusable portrait {}: {}", path, exc)
        return None
