"""Import private style examples without extracting uploaded archives to disk."""

from __future__ import annotations

import io
import re
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree

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


def extract_text(filename: str, data: bytes) -> str:
    """Extract text from a supported, bounded upload; reject empty scans."""
    suffix = Path(_name(filename)).suffix.lower()
    if not data or len(data) > MAX_BYTES:
        raise ExampleError("Examples must be nonempty and at most 8 MB.")
    try:
        if suffix == ".pdf":
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted or len(reader.pages) > 20:
                raise ExampleError("Use an unlocked PDF of at most 20 pages.")
            text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
        elif suffix in {".docx", ".odt"}:
            member = "word/document.xml" if suffix == ".docx" else "content.xml"
            text = _office_text(data, member)
        else:
            text = data.decode("utf-8-sig")
    except (
        UnicodeError,
        zipfile.BadZipFile,
        KeyError,
        ElementTree.ParseError,
        PdfReadError,
        ValueError,
        OSError,
    ) as exc:
        raise ExampleError(f"Could not read example: {exc}") from exc
    text = text.strip()
    if len(text) < 40 or len(text) > MAX_TEXT:
        raise ExampleError("Use a text-based letter between 40 and 80,000 characters.")
    return text


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
