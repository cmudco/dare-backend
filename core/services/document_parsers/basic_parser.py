"""
Basic Document Parser

Wraps the pre-Docling extraction path (PyPDF2, zipfile/ElementTree for DOCX,
openpyxl and xlrd for spreadsheets, encoding-sniffing for text) behind the
``BaseDocumentParser`` interface.

This is the fallback: it runs for the formats Docling does not cover — plain
text, JSON, CSV — and whenever a Docling conversion fails outright,
so a parser problem degrades to the old behaviour instead of failing the
upload.
"""

import logging
import time

from core.services.document_parsers.base import BaseDocumentParser
from core.services.document_parsers.constants import PARSER_BASIC
from core.services.dtos.parsed_document_dto import ParsedDocument, text_only_document
from core.services.file_readers import read_bytes_as_text

logger = logging.getLogger(__name__)


class BasicDocumentParser(BaseDocumentParser):
    """Flat-text extraction with no document model."""

    name = PARSER_BASIC

    def supports(self, filename: str) -> bool:
        """Accepts anything — this is the last resort."""
        return True

    def parse(self, data: bytes, filename: str) -> ParsedDocument:
        started = time.time()
        text = read_bytes_as_text(data, filename)
        return text_only_document(
            text=text, parser=self.name, duration_seconds=time.time() - started
        )
