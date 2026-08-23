"""
Document Parser Base

Abstract base class every document parser implements. A parser takes the raw
bytes of an uploaded file and returns a ``ParsedDocument`` — flat text plus the
structure behind it.

Parsers never touch the database or the storage backend; they are handed bytes
and a filename and hand back a DTO.
"""

import logging
from abc import ABC, abstractmethod

from core.services.dtos.parsed_document_dto import ParsedDocument

logger = logging.getLogger(__name__)


class BaseDocumentParser(ABC):
    """Abstract base class for document parsers."""

    name: str = "base"

    @abstractmethod
    def supports(self, filename: str) -> bool:
        """Whether this parser can handle the given filename."""

    @abstractmethod
    def parse(self, data: bytes, filename: str) -> ParsedDocument:
        """Parse raw bytes into a ParsedDocument.

        Args:
            data: Raw file content
            filename: Original filename, used for format detection

        Returns:
            ParsedDocument with the recovered text and document model

        Raises:
            Exception: If the file cannot be parsed at all. The caller is
                expected to fall back to another parser.
        """
