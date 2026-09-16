"""Portrait normalisation."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from job_scout.cv.images import MAX_EDGE, ImageError, load_square, reader_from_path
from tests.cv.conftest import make_image


def test_landscape_is_cropped_to_a_centred_square() -> None:
    image = load_square(make_image(400, 200, (10, 20, 30)))
    assert image.width == image.height == 200


def test_portrait_is_cropped_to_a_centred_square() -> None:
    image = load_square(make_image(120, 500, (10, 20, 30)))
    assert image.width == image.height == 120


def test_oversized_images_are_downscaled() -> None:
    image = load_square(make_image(MAX_EDGE + 600, MAX_EDGE + 600, (1, 2, 3)))
    assert image.width == image.height == MAX_EDGE


def test_small_images_are_not_upscaled() -> None:
    image = load_square(make_image(64, 64, (1, 2, 3)))
    assert image.width == 64


def test_result_is_always_rgb() -> None:
    buffer = io.BytesIO()
    Image.new("RGBA", (80, 80), (5, 6, 7, 128)).save(buffer, format="PNG")
    assert load_square(buffer.getvalue()).mode == "RGB"


def test_greyscale_is_converted() -> None:
    buffer = io.BytesIO()
    Image.new("L", (80, 80), 128).save(buffer, format="PNG")
    assert load_square(buffer.getvalue()).mode == "RGB"


def test_crop_keeps_the_centre() -> None:
    source = Image.new("RGB", (300, 100), (0, 0, 0))
    for x in range(100, 200):
        for y in range(100):
            source.putpixel((x, y), (255, 0, 0))
    buffer = io.BytesIO()
    source.save(buffer, format="PNG")

    cropped = load_square(buffer.getvalue())
    assert cropped.size == (100, 100)
    assert cropped.getpixel((50, 50)) == (255, 0, 0)


@pytest.mark.parametrize("payload", [b"", b"nope", b"\x89PNG\r\n\x1a\n truncated"])
def test_undecodable_bytes_raise(payload: bytes) -> None:
    with pytest.raises(ImageError):
        load_square(payload)


def test_reader_from_missing_path_is_none(tmp_path: Path) -> None:
    assert reader_from_path(tmp_path / "absent.png") is None


def test_reader_from_broken_file_is_none(tmp_path: Path) -> None:
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"still not a png")
    assert reader_from_path(broken) is None


def test_reader_from_valid_file_works(tmp_path: Path) -> None:
    good = tmp_path / "good.png"
    good.write_bytes(make_image(100, 100, (4, 5, 6)))
    assert reader_from_path(good) is not None


def test_exif_orientation_is_applied() -> None:
    source = Image.new("RGB", (200, 100), (0, 0, 0))
    buffer = io.BytesIO()
    exif = source.getexif()
    exif[274] = 6  # Rotate 90 degrees.
    source.save(buffer, format="JPEG", exif=exif)

    rotated = load_square(buffer.getvalue())
    # After transposing, the 200x100 landscape becomes 100x200 portrait, so the
    # square crop is bounded by the new 100px width.
    assert rotated.size == (100, 100)
