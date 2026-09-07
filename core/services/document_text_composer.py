"""Compose user-facing document text without sacrificing recovery coverage."""

from typing import Iterable

from core.services.document_text_coverage import (
    MIN_RECOVERED_TEXT_CHARACTERS,
    missing_text_blocks,
)
from core.services.dtos.parsed_document_dto import ParsedDocument


def compose_document_text(parsed: ParsedDocument, primary_parts: Iterable[str]) -> str:
    """Keep the structured representation and append only genuinely missing text."""
    parts = [part.strip() for part in primary_parts if part and part.strip()]
    source_parts = list(parts)
    for element in parsed.elements:
        source_parts.extend(
            value
            for value in (element.text, element.table_markdown, element.caption)
            if value
        )
    parts.extend(
        missing_text_blocks(
            parsed.recovery_text,
            source_parts,
            minimum_characters=MIN_RECOVERED_TEXT_CHARACTERS,
        )
    )
    return "\n\n".join(part.strip() for part in parts if part and part.strip())
