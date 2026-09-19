"""Import private style examples without extracting uploaded archives to disk."""

from __future__ import annotations

import io
import re
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree

from loguru import logger
from PyPDF2 import PdfReader
from PyPDF2.errors import PdfReadError

from job_scout.config import user_letters_dir
from job_scout.letters.language import detect_language
from job_scout.letters.models import ExampleLetter, ExampleSummary

SUPPORTED_SUFFIXES = frozenset({".txt", ".md", ".pdf", ".docx", ".odt"})
MAX_BYTES = 8 * 1024 * 1024
MAX_TEXT = 80000


class ExampleError(ValueError):
    """An example cannot be safely read or stored."""


def examples_dir(user: str) -> Path:
    """Return the private example directory for a user."""
    return user_letters_dir(user) / "examples"


def _name(filename: str) -> str:
    """Validate a single filename, including on Windows."""
    if (
        not filename
        or len(filename) > 200
        or filename.startswith(".")
        or Path(filename).stem.upper()
        in {
            "CON",
            "PRN",
            "AUX",
            "NUL",
            *(f"COM{i}" for i in range(1, 10)),
            *(f"LPT{i}" for i in range(1, 10)),
        }
        or re.search(r'[\\/:*?"<>|\x00-\x1f]', filename)
        or Path(filename).suffix.lower() not in SUPPORTED_SUFFIXES
    ):
        raise ExampleError("Use a filename ending in PDF, DOCX, ODT, TXT or MD.")
    return filename


def _office_text(data: bytes, member: str) -> str:
    """Read only bounded XML from an office archive, never filesystem paths."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        info = archive.getinfo(member)
        if info.file_size > MAX_BYTES:
            raise ExampleError("The document expands beyond the size limit.")
        xml = archive.read(info)
    if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
        raise ExampleError("XML entities are not supported.")
    root = ElementTree.fromstring(xml)
    paragraphs = []
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1] in {"p", "h"}:
            paragraphs.append("".join(node.itertext()))
    return "\n\n".join(paragraphs)


def _decode_text(data: bytes) -> str:
    """Decode a plain text file in the encodings Windows editors save.

    Args:
        data: The file's bytes.

    Returns:
        The text: UTF-16 when the file starts with its byte order mark,
        otherwise UTF-8 (with or without a mark), and Windows-1252 (Notepad
        "ANSI" and Word "Plain Text" on a Dutch Windows) when it is not
        UTF-8.

    Raises:
        UnicodeError: If the bytes fit none of these.
    """
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252")


def _read_document(suffix: str, data: bytes) -> str:
    """Read the text of a document by its format.

    Args:
        suffix: The file's suffix, lower case.
        data: The file's bytes.

    Returns:
        The document's text, not yet stripped.

    Raises:
        ExampleError: For a locked or overlong PDF, or an office file that
            is too large or uses XML entities.
    """
    if suffix == ".pdf":
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted or len(reader.pages) > 20:
            raise ExampleError("Use an unlocked PDF of at most 20 pages.")
        return "\n\n".join(page.extract_text() or "" for page in reader.pages)
    if suffix in {".docx", ".odt"}:
        member = "word/document.xml" if suffix == ".docx" else "content.xml"
        return _office_text(data, member)
    return _decode_text(data)


def extract_text(
    filename: str, data: bytes, *, kind: str = "letter", min_chars: int = 40
) -> str:
    """Extract text from a supported, bounded upload; reject empty scans.

    A file that cannot be read gets one plain message, and the cause goes to
    the log: codec or archive errors mean nothing to the applicant.

    Args:
        filename: The uploaded file's name; its suffix decides the format.
        data: The uploaded bytes.
        kind: What the document is, for the error messages ("letter", "CV").
        min_chars: The fewest characters a usable text has. A letter example
            needs 40; a note about oneself may be one line.

    Returns:
        The document's text.

    Raises:
        ExampleError: If the file is empty, too large, unreadable, a scan, or
            its text is shorter than ``min_chars`` or longer than
            :data:`MAX_TEXT` characters.
    """
    suffix = Path(_name(filename)).suffix.lower()
    if not data or len(data) > MAX_BYTES:
        raise ExampleError("The file must be nonempty and at most 8 MB.")
    try:
        text = _read_document(suffix, data).strip()
    except ExampleError:
        raise
    except (
        UnicodeError,
        zipfile.BadZipFile,
        KeyError,
        ElementTree.ParseError,
        PdfReadError,
        ValueError,
        OSError,
    ) as exc:
        logger.warning(f"Could not read {filename}: {type(exc).__name__}: {exc}")
        raise ExampleError(
            f"This {kind} could not be read. Save it again as a text-based PDF, "
            "a DOCX or a TXT file, or paste its text."
        ) from exc
    _check_length(text, kind, min_chars)
    return text


def _check_length(text: str, kind: str, min_chars: int) -> None:
    """Refuse a text too short or too long to use, saying which.

    Args:
        text: The document's text, stripped.
        kind: What the document is, for the message.
        min_chars: The fewest characters a usable text has.

    Raises:
        ExampleError: If the text is empty, shorter than ``min_chars`` or
            longer than :data:`MAX_TEXT` characters.
    """
    if not text:
        raise ExampleError(
            f"No text was found in this {kind}. A scanned PDF holds none; use a "
            "text-based file or paste the text."
        )
    if len(text) < min_chars:
        raise ExampleError(
            f"This {kind} holds only {len(text)} characters. Use a text of at "
            f"least {min_chars} characters."
        )
    if len(text) > MAX_TEXT:
        raise ExampleError(
            f"This {kind} holds {len(text):,} characters and at most "
            f"{MAX_TEXT:,} can be read. Split it into smaller files, or paste "
            "one part at a time."
        )


def list_examples(user: str) -> list[ExampleLetter]:
    """Load accepted examples, newest first; originals remain private."""
    result = []
    root = examples_dir(user)
    for path in root.glob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        if path.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        text = extract_text(path.name, path.read_bytes())
        result.append(
            ExampleLetter(
                name=path.name,
                text=text,
                words=len(text.split()),
                language=detect_language(text),
                modified=datetime.fromtimestamp(path.stat().st_mtime, UTC),
            )
        )
    return sorted(result, key=lambda item: item.modified, reverse=True)


def list_example_summaries(user: str) -> list[ExampleSummary]:
    """List metadata without disclosing the contents in the list response."""
    return [
        ExampleSummary(**e.model_dump(exclude={"text"})) for e in list_examples(user)
    ]


def add_example(user: str, filename: str, data: bytes) -> ExampleSummary:
    """Validate and add an original example without overwriting another."""
    name = _name(filename)
    text = extract_text(name, data)
    root = examples_dir(user)
    root.mkdir(parents=True, exist_ok=True)
    path = root / name
    try:
        with path.open("xb") as stream:
            stream.write(data)
    except FileExistsError as exc:
        raise ExampleError("An example with that filename already exists.") from exc
    return ExampleSummary(
        name=name,
        words=len(text.split()),
        language=detect_language(text),
        modified=datetime.now(UTC),
    )


def delete_example(user: str, name: str) -> None:
    """Remove exactly one original example."""
    path = examples_dir(user) / _name(name)
    try:
        path.unlink()
    except FileNotFoundError as exc:
        raise ExampleError("Example not found.") from exc
