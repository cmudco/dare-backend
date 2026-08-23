"""
File Processor

Thin compatibility layer over the parsing pipeline. Callers that only want the
text of a file keep calling ``read_file_content``; underneath the file is
parsed once by Docling and the result cached on the row, so a second reference
is a column read rather than another PDF parse.

Callers that want the structure — pages, sections, tables, picture positions —
should use ``DocumentParsingService`` directly.
"""

import logging
from typing import Optional

from core.services.document_parsing_service import DocumentParsingService
from files.models import File

logger = logging.getLogger(__name__)


class FileProcessor:
    """Service for reading text out of uploaded files."""

    def __init__(self, parsing_service: Optional[DocumentParsingService] = None):
        self.parsing_service = parsing_service or DocumentParsingService()

    def read_file_content(self, file: File) -> str:
        """Read and extract text content from a file.

        Returns the cached extraction when the file has already been parsed,
        and parses on demand otherwise.

        Args:
            file: File to read

        Returns:
            Extracted text. Empty for files that carry none, such as an
            image-only PDF or a media file.
        """
        try:
            return self.parsing_service.get_text(file)
        except Exception as error:
            raise Exception(f"Error reading file content: {str(error)}")
