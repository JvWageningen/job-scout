"""Registration of the bundled TrueType faces with ReportLab.

Lato ships inside the package (SIL Open Font License), so a rendered CV looks
identical on every machine. Helvetica is kept as a zero-dependency fallback that
uses ReportLab's built-in Type 1 metrics.
"""

from __future__ import annotations

import threading
from importlib import resources
from pathlib import Path
from typing import Literal

from loguru import logger
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFError, TTFont

type Weight = Literal["light", "regular", "semibold", "bold", "italic"]

_LATO_FILES: dict[Weight, str] = {
    "light": "Lato-Light.ttf",
    "regular": "Lato-Regular.ttf",
    "semibold": "Lato-Semibold.ttf",
    "bold": "Lato-Bold.ttf",
    "italic": "Lato-Italic.ttf",
}

_LATO_FACES: dict[Weight, str] = {
    "light": "Lato-Light",
    "regular": "Lato",
    "semibold": "Lato-Semibold",
    "bold": "Lato-Bold",
    "italic": "Lato-Italic",
}

_HELVETICA_FACES: dict[Weight, str] = {
    "light": "Helvetica",
    "regular": "Helvetica",
    "semibold": "Helvetica-Bold",
    "bold": "Helvetica-Bold",
    "italic": "Helvetica-Oblique",
}

_lock = threading.Lock()
_registered = False


def font_dir() -> Path:
    """Return the directory holding the bundled TTF files.

    Returns:
        Path to ``job_scout/cv/assets/fonts``.
    """
    return Path(str(resources.files("job_scout.cv"))) / "assets" / "fonts"


def register_fonts() -> bool:
    """Register the bundled Lato faces with ReportLab exactly once.

    Safe to call from any thread and any number of times.

    Returns:
        True if the Lato family is usable, False if it could not be loaded and
        callers should fall back to Helvetica.
    """
    global _registered
    with _lock:
        if _registered:
            return True

        directory = font_dir()
        for weight, filename in _LATO_FILES.items():
            path = directory / filename
            if not path.is_file():
                logger.warning("Bundled font missing: {}", path)
                return False
            try:
                pdfmetrics.registerFont(TTFont(_LATO_FACES[weight], str(path)))
            except (TTFError, OSError) as exc:
                logger.warning("Could not register {}: {}", path.name, exc)
                return False

        pdfmetrics.registerFontFamily(
            "Lato",
            normal="Lato",
            bold="Lato-Bold",
            italic="Lato-Italic",
            boldItalic="Lato-Bold",
        )
        _registered = True
        logger.debug("Registered Lato family from {}", directory)
        return True


def face(family: str, weight: Weight) -> str:
    """Resolve a family plus weight to a registered ReportLab face name.

    Falls back to Helvetica when the requested family is unavailable, so
    rendering never fails because of a missing font.

    Args:
        family: Either ``"Lato"`` or ``"Helvetica"``.
        weight: Desired weight.

    Returns:
        A face name that ReportLab can set on a canvas.
    """
    if family == "Lato" and register_fonts():
        return _LATO_FACES[weight]
    return _HELVETICA_FACES[weight]
