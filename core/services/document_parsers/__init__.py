"""
Document Parsers

Parsers turn raw file bytes into a ``ParsedDocument``. ``get_document_parsers``
returns them in preference order for a given filename; the caller tries each in
turn so a Docling failure degrades to flat text rather than failing the upload.
"""

import logging
from typing import List, Optional

import sentry_sdk

from core.services.document_parsers.base import BaseDocumentParser
from core.services.document_parsers.constants import (
    DOCLING_EXTENSIONS,
    NOTEBOOK_EXTENSIONS,
    PARSER_DOCLING,
    PARSER_LEGACY,
    PARSER_NOTEBOOK,
)
from core.services.document_parsers.legacy_parser import LegacyDocumentParser
from core.services.document_parsers.notebook_parser import NotebookDocumentParser

logger = logging.getLogger(__name__)

# Docling is held per-process: constructing it loads a layout model, and the
# converter is reused across files.
_docling_parser: Optional[BaseDocumentParser] = None
_docling_unavailable = False
_docling_unavailable_reason: Optional[str] = None


def get_docling_parser() -> Optional[BaseDocumentParser]:
    """Return the Docling parser, or None if Docling is not installed.

    The import is deliberately deferred rather than living at module top:
    importing Docling pulls in torch and its model stack, which is worth
    paying in an RQ worker that is about to parse a document and wasteful in
    an ASGI process that only ever reads ``File.extracted_text``.
    """
    global _docling_parser, _docling_unavailable, _docling_unavailable_reason

    if _docling_parser is not None or _docling_unavailable:
        return _docling_parser

    try:
        from core.services.document_parsers.docling_parser import DoclingDocumentParser

        _docling_parser = DoclingDocumentParser()
    except Exception as error:
        _docling_unavailable = True
        _docling_unavailable_reason = str(error)
        sentry_sdk.capture_exception(error)
        logger.warning(
            f"Docling is unavailable, falling back to flat text extraction: {error}"
        )

    return _docling_parser


def get_docling_unavailable_reason() -> Optional[str]:
    """Why this worker could not construct Docling, if startup failed."""
    return _docling_unavailable_reason


def get_document_parsers(filename: str) -> List[BaseDocumentParser]:
    """Parsers to try for this filename, best first.

    The legacy parser is always last so every file has a path that produces
    *something*, even if it is only flat text.
    """
    parsers: List[BaseDocumentParser] = []

    docling = get_docling_parser()
    if docling is not None and docling.supports(filename):
        parsers.append(docling)

    notebook = NotebookDocumentParser()
    if notebook.supports(filename):
        parsers.append(notebook)

    parsers.append(LegacyDocumentParser())
    return parsers


def reset_parser_cache() -> None:
    """Drop the cached Docling parser. Used by tests and management commands."""
    global _docling_parser, _docling_unavailable, _docling_unavailable_reason
    _docling_parser = None
    _docling_unavailable = False
    _docling_unavailable_reason = None


__all__ = [
    "BaseDocumentParser",
    "LegacyDocumentParser",
    "NotebookDocumentParser",
    "DOCLING_EXTENSIONS",
    "NOTEBOOK_EXTENSIONS",
    "PARSER_DOCLING",
    "PARSER_LEGACY",
    "PARSER_NOTEBOOK",
    "get_docling_parser",
    "get_docling_unavailable_reason",
    "get_document_parsers",
    "reset_parser_cache",
]
