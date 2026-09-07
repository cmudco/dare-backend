"""Find in-document pointers ("see section 7.2") in chunk text. Pure regex."""

import re
from dataclasses import dataclass
from typing import List, Tuple

POINTER_PATTERNS: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    (
        "section",
        re.compile(r"(?:\b(?:section|sect\.|sec\.)|§)\s*(\d+(?:\.\d+)*)\b", re.I),
    ),
    ("figure", re.compile(r"\b(?:figure|fig\.)\s*(\d+(?:\.\d+)*)\b", re.I)),
    ("table", re.compile(r"\btable\s*(\d+(?:\.\d+)*)\b", re.I)),
    ("chapter", re.compile(r"\bchapter\s*(\d+)\b", re.I)),
    ("appendix", re.compile(r"\bappendix\s*([A-Za-z])\b", re.I)),
    ("page", re.compile(r"\b(?:page|pp?\.)\s*(\d+)\b", re.I)),
)

LEGAL_SECTION_CONTEXT = re.compile(
    r"(?:code\s+of\s+federal\s+regulations|united\s+states\s+code|"
    r"\bCFR\b|\bU\.?S\.?C\.?)\W{0,40}$",
    re.I,
)


@dataclass(frozen=True)
class PointerMatch:
    kind: str
    key: str
    raw_text: str
    position: int


def extract_pointers(text: str, limit: int = 20) -> List[PointerMatch]:
    """Every distinct (kind, key) pointer in the text, in order of appearance."""
    found: List[PointerMatch] = []
    for kind, pattern in POINTER_PATTERNS:
        for match in pattern.finditer(text or ""):
            if kind == "section" and LEGAL_SECTION_CONTEXT.search(
                (text or "")[max(0, match.start() - 100) : match.start()]
            ):
                # A statutory citation names a law, not a destination inside
                # this uploaded document. Keeping it as an unresolved document
                # edge makes reports look broken when the extractor is wrong.
                continue
            key = match.group(1)
            if kind == "appendix":
                key = key.upper()
            found.append(PointerMatch(kind, key, match.group(0).strip(), match.start()))
    found.sort(key=lambda pointer: pointer.position)
    seen = set()
    unique: List[PointerMatch] = []
    for pointer in found:
        dedupe_key = (pointer.kind, pointer.key)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        unique.append(pointer)
        if len(unique) >= limit:
            break
    return unique
