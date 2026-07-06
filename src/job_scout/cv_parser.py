"""CV PDF parsing utilities."""

from __future__ import annotations

from pathlib import Path

from loguru import logger


def parse_cv(cv_path: str | Path) -> str:
    """Parse a CV PDF file and return its text content.

    Args:
        cv_path: Path to the PDF file.

    Returns:
        Extracted text content, or empty string on failure.

    Raises:
        FileNotFoundError: If the CV file does not exist.
    """
    path = Path(cv_path)
    if not path.exists():
        raise FileNotFoundError(
            f"CV file not found: {cv_path}\nRun 'job-scout init' to set your CV path."
        )

    try:
        import PyPDF2

        with path.open("rb") as f:
            reader = PyPDF2.PdfReader(f)
            pages = [
                page.extract_text() for page in reader.pages if page.extract_text()
            ]
        text = "\n".join(pages)
        logger.debug(f"Parsed CV: {len(text)} characters from {path.name}")
        return text
    except Exception as e:
        logger.warning(f"Failed to parse CV '{cv_path}': {e}")
        return ""
