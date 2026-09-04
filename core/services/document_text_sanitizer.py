"""Remove transport-invalid control characters from extracted document text."""

import re
from dataclasses import replace
from typing import Any

from core.services.dtos.parsed_document_dto import ParsedDocument

# PostgreSQL rejects NUL outright. The remaining C0 controls are extraction
# artifacts too: keeping them gives embedding providers invisible garbage while
# adding no document meaning. Preserve tabs and line breaks because they carry
# real layout information for prose and Markdown tables.
INVALID_DOCUMENT_CONTROL_PATTERN = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize_document_text(value: str) -> str:
    """Replace invalid invisible controls without joining adjacent words."""
    return INVALID_DOCUMENT_CONTROL_PATTERN.sub(" ", value)


def sanitize_document_payload(value: Any) -> Any:
    """Recursively clean text values before a document model reaches JSONB."""
    if isinstance(value, str):
        return sanitize_document_text(value)
    if isinstance(value, dict):
        return {key: sanitize_document_payload(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_document_payload(item) for item in value]
    if isinstance(value, tuple):
        return tuple(sanitize_document_payload(item) for item in value)
    return value


def sanitize_parsed_document(parsed: ParsedDocument) -> ParsedDocument:
    """Clean every parser-owned string while preserving document structure."""
    elements = tuple(
        replace(
            element,
            text=sanitize_document_text(element.text),
            section=(
                sanitize_document_text(element.section) if element.section else None
            ),
            caption=(
                sanitize_document_text(element.caption) if element.caption else None
            ),
            table_markdown=(
                sanitize_document_text(element.table_markdown)
                if element.table_markdown
                else None
            ),
            heading_context=sanitize_document_payload(element.heading_context),
            classifications=sanitize_document_payload(element.classifications),
            number=(sanitize_document_text(element.number) if element.number else None),
        )
        for element in parsed.elements
    )
    return replace(
        parsed,
        text=sanitize_document_text(parsed.text),
        recovery_text=sanitize_document_text(parsed.recovery_text),
        fallback_from=(
            sanitize_document_text(parsed.fallback_from)
            if parsed.fallback_from
            else None
        ),
        fallback_reason=(
            sanitize_document_text(parsed.fallback_reason)
            if parsed.fallback_reason
            else None
        ),
        elements=elements,
    )
