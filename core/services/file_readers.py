"""
File Readers

Byte-level text extraction, one function per format. These are the fallback
readers: flat text, no structure, no page numbers. Docling handles the
structured path (see ``core/services/document_parsers``); these run for the
formats it does not cover and whenever a Docling conversion fails.

Everything here takes bytes rather than a ``File`` model instance so the same
code serves both the parser layer and any caller that already has the content
in hand.
"""

import io
import logging
import zipfile
from typing import List
from xml.etree import ElementTree as ET

import PyPDF2

logger = logging.getLogger(__name__)

# Optional spreadsheet libraries
try:
    from openpyxl import load_workbook  # type: ignore

    OPENPYXL_AVAILABLE = True
except Exception:
    OPENPYXL_AVAILABLE = False

try:
    import xlrd  # type: ignore

    XLRD_AVAILABLE = True
except Exception:
    XLRD_AVAILABLE = False


DOCX_NAMESPACES = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}

# Tried in order; the first that decodes cleanly wins.
TEXT_ENCODINGS = (
    "utf-8",
    "utf-8-sig",
    "latin-1",
    "cp1252",
    "iso-8859-1",
    "ascii",
)


# ============================================================================
# Public API
# ============================================================================


def read_bytes_as_text(data: bytes, filename: str) -> str:
    """Extract flat text from raw bytes, dispatching on the filename extension.

    Args:
        data: Raw file content
        filename: Original filename, used only for format detection

    Returns:
        Extracted text, or an empty string when the format carries none.
    """
    name = (filename or "").lower()

    if name.endswith(".pdf"):
        return read_pdf(data)
    if name.endswith((".txt", ".md", ".json", ".csv")):
        return decode_text(data)
    if name.endswith(".docx"):
        return read_docx(data)
    if name.endswith(".xlsx"):
        return read_xlsx(data)
    if name.endswith(".xls"):
        return read_xls(data)
    return ""


def read_pdf(data: bytes) -> str:
    """Flat text extraction via PyPDF2."""
    reader = PyPDF2.PdfReader(io.BytesIO(data))
    return " ".join(page.extract_text() or "" for page in reader.pages)


def decode_text(data: bytes) -> str:
    """Decode text bytes, trying a handful of encodings before giving up."""
    for encoding in TEXT_ENCODINGS:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def read_docx(data: bytes) -> str:
    """Extract paragraph text from a DOCX by walking ``word/document.xml``."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        with archive.open("word/document.xml") as document_xml:
            root = ET.fromstring(document_xml.read())

    parts = [
        element.text
        for element in root.findall(".//w:t", DOCX_NAMESPACES)
        if element.text
    ]
    return "\n".join(parts)


def read_xlsx(data: bytes) -> str:
    """Extract cell values from an XLSX workbook using openpyxl."""
    if not OPENPYXL_AVAILABLE:
        logger.warning("XLSX content not extracted: openpyxl is not installed")
        return ""

    workbook = load_workbook(filename=io.BytesIO(data), data_only=True, read_only=True)
    lines: List[str] = []
    for sheet in workbook.worksheets:
        lines.append(f"Sheet: {sheet.title}")
        for row in sheet.iter_rows(values_only=True):
            values = [str(cell) for cell in row if cell is not None]
            if values:
                lines.append("\t".join(values))
    return "\n".join(lines)


def read_xls(data: bytes) -> str:
    """Extract cell values from a legacy XLS workbook using xlrd."""
    if not XLRD_AVAILABLE:
        logger.warning("XLS content not extracted: xlrd is not installed")
        return ""

    book = xlrd.open_workbook(file_contents=data)
    lines: List[str] = []
    for sheet in book.sheets():
        lines.append(f"Sheet: {sheet.name}")
        for row_index in range(sheet.nrows):
            values = [
                str(cell)
                for cell in sheet.row_values(row_index)
                if str(cell).strip() != ""
            ]
            if values:
                lines.append("\t".join(values))
    return "\n".join(lines)
