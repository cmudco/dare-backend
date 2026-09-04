"""Parse Jupyter notebooks into a ``ParsedDocument``.

A notebook is already JSON with its cells in reading order, so this needs no
converter and no new dependency: markdown cells pass through as prose and code
cells become fenced blocks, which keeps the prose-to-code pairing that makes a
notebook worth reading in the first place.

Recorded outputs are kept because DARE has no executor — the results saved in
the file are the only evidence of what the code does, and a traceback sitting
in an output cell is usually enough to answer "why did this break". Image
outputs are dropped: a base64 PNG is megabytes of text that answers nothing.
"""

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from core.config.document_parsing import (
    NOTEBOOK_OUTPUT_LIMIT,
    ElementKind,
    ElementLabel,
)
from core.services.document_parsers.base import BaseDocumentParser
from core.services.document_parsers.constants import (
    NOTEBOOK_EXTENSIONS,
    PARSER_NOTEBOOK,
)
from core.services.document_parsers.headings import HeadingStack, heading_number
from core.services.dtos.parsed_document_dto import (
    DocumentStructure,
    ParsedDocument,
    ParsedElement,
)

logger = logging.getLogger(__name__)

ANSI_PATTERN = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")
HEADING_LINE_PATTERN = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
FENCE_PATTERN = re.compile(r"^\s{0,3}(```|~~~)")
DEFAULT_LANGUAGE = "python"
TRUNCATION_MARKER = "\n... output truncated"


class NotebookDocumentParser(BaseDocumentParser):
    """Turns a .ipynb file into its markdown twin plus a cell-level model."""

    name = PARSER_NOTEBOOK

    def supports(self, filename: str) -> bool:
        return (filename or "").lower().rsplit(".", 1)[-1] in NOTEBOOK_EXTENSIONS

    def parse(self, data: bytes, filename: str) -> ParsedDocument:
        started = time.time()
        notebook = _load(data)
        elements, blocks, pictures = _walk(notebook["cells"], _language(notebook))

        return ParsedDocument(
            text="\n\n".join(blocks).strip(),
            elements=tuple(elements),
            structure=DocumentStructure(
                sections=sum(1 for element in elements if element.is_heading),
                pictures=pictures,
                content_chars=sum(len(element.text.strip()) for element in elements),
            ),
            parser=self.name,
            duration_seconds=time.time() - started,
        )


def notebook_markdown(data: bytes) -> str:
    """The markdown twin on its own, for callers that only want text."""
    notebook = _load(data)
    _, blocks, _ = _walk(notebook["cells"], _language(notebook))
    return "\n\n".join(blocks).strip()


# ----------------------------------------------------------------------
# Cell walk
# ----------------------------------------------------------------------


def _load(data: bytes) -> Dict[str, Any]:
    """Parse the notebook JSON, rejecting anything that is not nbformat 4.

    Raising here is deliberate: the parser registry falls through to the
    legacy reader, which is the right home for a file that only looks like a
    notebook.
    """
    notebook = json.loads(data)
    if not isinstance(notebook, dict) or not isinstance(notebook.get("cells"), list):
        raise ValueError("Not an nbformat 4 notebook: no cell list")
    return notebook


def _walk(
    cells: List[Any], language: str
) -> Tuple[List[ParsedElement], List[str], int]:
    elements: List[ParsedElement] = []
    blocks: List[str] = []
    pictures = 0
    section: Optional[str] = None
    order = 0
    stack = HeadingStack()

    for cell in cells:
        if not isinstance(cell, dict):
            continue

        cell_type = cell.get("cell_type")
        source = _source(cell).strip()
        if not source:
            continue

        if cell_type == "markdown":
            for heading, level, text in _split_markdown(source):
                section = heading or section
                order += 1
                parent_order = stack.current_order
                element_level: Optional[int] = None
                number: Optional[str] = None
                if heading:
                    element_level = level
                    number = heading_number(heading)
                    parent_order = stack.push(level, order, heading)
                elements.append(
                    ParsedElement(
                        order=order,
                        kind=ElementKind.TEXT,
                        label=(
                            ElementLabel.SECTION_HEADER
                            if heading
                            else ElementLabel.TEXT
                        ),
                        text=text,
                        section=section,
                        level=element_level,
                        parent_order=parent_order,
                        number=number,
                    )
                )
            blocks.append(source)
            continue

        if cell_type != "code":
            continue

        order += 1
        elements.append(
            ParsedElement(
                order=order,
                kind=ElementKind.TEXT,
                label=ElementLabel.CODE,
                text=source,
                section=section,
                parent_order=stack.current_order,
            )
        )
        blocks.append(f"```{language}\n{source}\n```")

        output, images = _outputs(cell)
        pictures += images
        if output:
            order += 1
            elements.append(
                ParsedElement(
                    order=order,
                    kind=ElementKind.TEXT,
                    label=ElementLabel.CODE_OUTPUT,
                    text=output,
                    section=section,
                    parent_order=stack.current_order,
                )
            )
            blocks.append(f"Output:\n\n```\n{output}\n```")

    return elements, blocks, pictures


def _outputs(cell: Dict[str, Any]) -> Tuple[str, int]:
    """Recorded output of one code cell, as text, plus its image count."""
    parts: List[str] = []
    pictures = 0

    for output in cell.get("outputs") or []:
        if not isinstance(output, dict):
            continue
        kind = output.get("output_type")

        if kind == "stream":
            parts.append(_join(output.get("text")))
        elif kind in ("execute_result", "display_data"):
            payload = output.get("data") or {}
            if not isinstance(payload, dict):
                continue
            if any(key.startswith("image/") for key in payload):
                pictures += 1
            parts.append(_join(payload.get("text/plain")))
        elif kind == "error":
            traceback = ANSI_PATTERN.sub("", _join(output.get("traceback"), sep="\n"))
            parts.append(
                traceback.strip()
                or f"{output.get('ename')}: {output.get('evalue')}".strip()
            )

    # Only newlines are trimmed: leading spaces are the column alignment of a
    # dataframe or a table, and stripping them changes what the output says.
    joined = "\n".join(part.strip("\n") for part in parts if part and part.strip())
    return _truncate(joined.strip("\n")), pictures


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _source(cell: Dict[str, Any]) -> str:
    return _join(cell.get("source"))


def _join(value: Any, sep: str = "") -> str:
    """Notebook string fields are either a string or a list of lines."""
    if isinstance(value, list):
        return sep.join(str(item) for item in value)
    return str(value) if value else ""


def _language(notebook: Dict[str, Any]) -> str:
    metadata = notebook.get("metadata")
    info = metadata.get("language_info") if isinstance(metadata, dict) else None
    name = info.get("name") if isinstance(info, dict) else None
    return str(name).lower() if name else DEFAULT_LANGUAGE


def _split_markdown(source: str) -> List[Tuple[Optional[str], int, str]]:
    """Split a markdown cell into heading and prose segments.

    A cell is not an outline entry: lab notebooks routinely open with a title
    followed by scoring notes and a rule, and returning the whole cell as one
    heading makes the document outline unreadable. Fenced code is skipped so a
    ``# comment`` line inside an example is not mistaken for a heading.
    """
    segments: List[Tuple[Optional[str], int, str]] = []
    prose: List[str] = []
    in_fence = False

    def flush() -> None:
        text = "\n".join(prose).strip()
        prose.clear()
        if text:
            segments.append((None, 0, text))

    for line in source.splitlines():
        if FENCE_PATTERN.match(line):
            in_fence = not in_fence
            prose.append(line)
            continue

        match = None if in_fence else HEADING_LINE_PATTERN.match(line)
        if match:
            flush()
            heading = match.group(2).strip()
            segments.append((heading, len(match.group(1)), heading))
        else:
            prose.append(line)

    flush()
    return segments


def _truncate(text: str) -> str:
    """Cap one cell's output so a dataframe dump cannot drown the prose."""
    if len(text) <= NOTEBOOK_OUTPUT_LIMIT:
        return text
    return text[:NOTEBOOK_OUTPUT_LIMIT].rstrip() + TRUNCATION_MARKER
