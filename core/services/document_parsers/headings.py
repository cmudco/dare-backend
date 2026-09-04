"""Heading helpers shared by the parsers.

A heading's number ("7.2") is the key an in-document pointer resolves
against, and the stack of open headings is how every element learns which
heading it sits under. Parsers push headings as they walk reading order.
"""

import re
from typing import Dict, List, Optional, Sequence, Tuple

HEADING_NUMBER_PATTERN = re.compile(r"^\s*(\d+(?:\.\d+)*)[.):]?\s+\S")
CHAPTER_PATTERN = re.compile(r"^\s*chapter\s+\d+\b", re.IGNORECASE)
ACTIVITY_PATTERN = re.compile(r"^\s*(?:activity|exercise)\s+\d", re.IGNORECASE)

HeadingRecord = Tuple[int, str, int, str]
HeadingHierarchy = Dict[int, Tuple[int, Optional[int]]]


def heading_number(text: str) -> Optional[str]:
    """The leading section number of a heading, or None when it has none."""
    match = HEADING_NUMBER_PATTERN.match(text or "")
    return match.group(1) if match else None


class HeadingStack:
    """Open headings by level, outermost first."""

    def __init__(self) -> None:
        self._open: List[Tuple[int, int, str]] = []

    def push(self, level: int, order: int, text: str) -> Optional[int]:
        """Register a heading and return the order of the heading it sits under."""
        while self._open and self._open[-1][0] >= level:
            self._open.pop()
        parent = self._open[-1][1] if self._open else None
        self._open.append((level, order, text))
        return parent

    @property
    def current_order(self) -> Optional[int]:
        return self._open[-1][1] if self._open else None

    @property
    def path(self) -> Tuple[str, ...]:
        return tuple(text for _, _, text in self._open)

    @property
    def entries(self) -> Tuple[Tuple[int, int, str], ...]:
        """Open heading records, outermost first."""
        return tuple(self._open)


def infer_flat_chapter_hierarchy(
    headings: Sequence[HeadingRecord],
) -> HeadingHierarchy:
    """Repair one narrow Docling failure: a numbered chapter flattened to level 1.

    Some PDFs visually encode hierarchy through text such as ``Chapter 1`` and
    ``1.4 Inheritance`` while Docling reports every section header at level 1.
    We only infer levels when that exact evidence is present. Documents with
    useful parser-provided levels are left untouched.
    """
    sections = [row for row in headings if row[3] == "section_header"]
    if (
        len(sections) < 2
        or {level for _, _, level, _ in sections} != {1}
        or not any(CHAPTER_PATTERN.match(text) for _, text, _, _ in sections)
        or not any(
            (number := heading_number(text)) is not None and "." in number
            for _, text, _, _ in sections
        )
    ):
        return {}

    hierarchy: HeadingHierarchy = {}
    stack = HeadingStack()
    last_numbered_level: Optional[int] = None
    activity_level: Optional[int] = None

    for order, text, raw_level, label in headings:
        if label == "title":
            level = raw_level
        elif CHAPTER_PATTERN.match(text):
            level = 1
            last_numbered_level = 1
            activity_level = None
        elif number := heading_number(text):
            level = len(number.split("."))
            last_numbered_level = level
            activity_level = None
        elif ACTIVITY_PATTERN.match(text):
            level = (last_numbered_level or 1) + 1
            activity_level = level
        elif text.lstrip().startswith(("•", "·")) and activity_level is not None:
            level = activity_level + 1
        else:
            # Consecutive unnumbered subtopics are siblings under the most
            # recent numbered section, not a chain nested under each other.
            level = (last_numbered_level or 1) + 1
            activity_level = None

        hierarchy[order] = (level, stack.push(level, order, text))

    return hierarchy
