"""
Document Parsing Service

Owns the "parse a file once, keep the result" half of ingestion.

Before this existed, every full-file reference re-read the PDF off disk and
re-extracted its text. Now a file is parsed once at upload: the flat text lands
in ``File.extracted_text`` for embedding and prompt injection, and the document
model — elements in reading order with labels, pages, boxes and captions —
lands in ``File.document_model`` for the UI and for the vision layer that will
describe pictures.
"""

import logging
from typing import List, Optional

from core.services.document_parsers import get_document_parsers
from core.services.document_parsers.legacy_parser import LegacyDocumentParser
from core.services.dtos.parsed_document_dto import ParsedDocument
from files.models import File

logger = logging.getLogger(__name__)


class DocumentParsingService:
    """Parses uploaded files and persists the result on the File row."""

    def __init__(self, parsers: Optional[List] = None):
        self._parsers = parsers

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse(self, file: File) -> ParsedDocument:
        """Parse a file, trying each candidate parser until one succeeds.

        Args:
            file: File to parse

        Returns:
            ParsedDocument from the first parser that did not raise. A parser
            returning no text is still a success — an image-only PDF genuinely
            has no text, and the caller decides what that means.

        Raises:
            Exception: Only if every parser raised.
        """
        data = self._read_bytes(file)
        filename = self._filename(file)

        last_error: Optional[Exception] = None
        for parser in self._parsers_for(filename):
            try:
                parsed = parser.parse(data, filename)
                logger.info(
                    f"Parsed file {file.id} with {parser.name}: "
                    f"{len(parsed.text)} chars, {parsed.structure.pages} pages, "
                    f"{parsed.structure.tables} tables, "
                    f"{parsed.structure.pictures} pictures "
                    f"in {parsed.duration_seconds:.1f}s"
                )
                return parsed
            except Exception as error:
                last_error = error
                logger.warning(
                    f"Parser {parser.name} failed on file {file.id} "
                    f"({filename}): {error}"
                )

        raise Exception(f"Could not parse file {filename}: {last_error}")

    def parse_and_persist(self, file: File) -> ParsedDocument:
        """Parse a file and store the text and document model on the row."""
        parsed = self.parse(file)
        self.persist(file, parsed)
        return parsed

    @staticmethod
    def persist(file: File, parsed: ParsedDocument) -> None:
        """Write a parse result onto the File row.

        ``extracted_text`` gets the embeddable text rather than the raw
        markdown export: a scanned PDF exports as a run of image placeholders,
        and storing those would inject them into the prompt on every full-file
        reference. An empty string here means "parsed, and there was nothing",
        which is different from the NULL of a file never parsed at all.
        """
        file.extracted_text = parsed.embeddable_text
        file.document_model = parsed.to_dict()
        file.page_count = parsed.structure.pages or None
        file.pages_without_text = parsed.structure.pages_without_text
        file.parser_name = parsed.parser
        file.save(
            update_fields=[
                "extracted_text",
                "document_model",
                "page_count",
                "pages_without_text",
                "parser_name",
                "updated_at",
            ]
        )

    def get_text(self, file: File) -> str:
        """Text for a file, for callers that only want the content.

        Returns the cached extraction when there is one. Files uploaded before
        this pipeline existed have none, and those fall back to flat text
        rather than to Docling: this runs in the websocket request path, and
        loading a layout model there to answer one message is the wrong trade.
        Use ``backfill_document_models`` to give those files a real parse.

        Args:
            file: File to read

        Returns:
            Extracted text, or an empty string if nothing could be recovered.
        """
        # Not a truthiness check: an empty string is a parsed file that
        # genuinely has no text, and re-reading it every time would be
        # pointless work for the same empty answer.
        if file.extracted_text is not None:
            return file.extracted_text

        try:
            return (
                LegacyDocumentParser()
                .parse(self._read_bytes(file), self._filename(file))
                .text
            )
        except Exception as error:
            logger.error(f"Fallback read failed for file {file.id}: {error}")
            return ""

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _parsers_for(self, filename: str) -> List:
        return (
            self._parsers
            if self._parsers is not None
            else get_document_parsers(filename)
        )

    @staticmethod
    def _read_bytes(file: File) -> bytes:
        with file.file.open("rb") as handle:
            return handle.read()

    @staticmethod
    def _filename(file: File) -> str:
        return file.file.name or file.name or ""
