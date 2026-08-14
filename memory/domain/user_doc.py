"""Parse and render the always-injected USER.md view."""

import math
import re
from typing import Dict, List, Optional, Tuple

# Canonical headings in render order. Custom headings are preserved.
PROFILE_HEADINGS: Dict[str, str] = {
    "identity": "Identity",
    "background": "Background",
    "communication": "Communication",
    "working-preferences": "Working preferences",
    "constraints": "Constraints",
    "boundaries": "Boundaries",
}

_RENAMED = {
    "user-profile": "identity",
    "durable-preferences": "working-preferences",
    "preferences": "working-preferences",
    "working-preference": "working-preferences",
}

_HEADING_RE = re.compile(r"^#{1,6}\s+(.*)$")


def key_of(heading: str) -> str:
    """``Working preferences`` → ``working-preferences``. The inverse of a heading."""
    slug = re.sub(r"[^a-z0-9]+", "-", heading.strip().lower()).strip("-")
    return _RENAMED.get(slug, slug)


def heading_for(key: str) -> str:
    """``working-preferences`` → ``Working preferences``. Unknown keys get title case."""
    canonical = PROFILE_HEADINGS.get(key)
    if canonical:
        return canonical
    words = [word for word in key.split("-") if word]
    if not words:
        return "Notes"
    joined = " ".join(words)
    return joined[0].upper() + joined[1:]


def estimate_tokens(text: str) -> int:
    """Estimate English tokens without adding a tokenizer dependency."""
    return math.ceil(len(text.strip()) / 4)


def normalize_line(text: str) -> str:
    """Normalize a bullet: no leading dash, one trailing period, single spaces."""
    body = text.strip()
    if body[:1] in "-*+":
        body = body[1:].lstrip()
    body = " ".join(body.split()).rstrip(". ")
    return f"{body}." if body else ""


def parse_user_doc(markdown: str) -> Dict[str, List[str]]:
    """The parsed file: one ordered map from key to lines.

    Canonical keys are seeded first so they render in a stable order; anything
    a human added lands after them, in the order it was found.
    """
    doc: Dict[str, List[str]] = {key: [] for key in PROFILE_HEADINGS}
    current: Optional[List[str]] = None

    for raw_line in markdown.split("\n"):
        line = raw_line.strip()
        if not line:
            continue

        heading_match = _HEADING_RE.match(line)
        if heading_match:
            key = key_of(heading_match.group(1))
            # `# User` is the document title, not a section.
            if line.startswith("# ") and key not in PROFILE_HEADINGS:
                current = None
                continue
            current = doc.setdefault(key, [])
            continue

        if current is None:
            continue
        bullet = normalize_line(line)
        if bullet and bullet not in current:
            current.append(bullet)

    return doc


def render_user_doc(doc: Dict[str, List[str]]) -> str:
    """Render back to Markdown, dropping every heading that ended up empty."""
    blocks = ["# User"]
    for key, lines in doc.items():
        if not lines:
            continue
        bullets = "\n".join(f"- {line}" for line in lines)
        blocks.append(f"## {heading_for(key)}\n{bullets}")
    return "\n\n".join(blocks) + "\n"


def merge_pinned(markdown: str, pinned: List[Tuple[str, str]]) -> str:
    """Render authored lines and pinned facts as one document."""
    if not markdown.strip() and not pinned:
        return ""

    doc = parse_user_doc(markdown)

    lines_by_heading: Dict[str, List[str]] = {}
    for key, text in pinned:
        line = normalize_line(text)
        if not line:
            continue
        heading = key_of(key) or "identity"
        lines = lines_by_heading.setdefault(heading, [])
        if line not in lines:
            lines.append(line)

    # The pinned copy is correctable, so it wins over an authored duplicate.
    pinned_lines = {
        line.lower() for lines in lines_by_heading.values() for line in lines
    }
    for key, lines in doc.items():
        doc[key] = [line for line in lines if line.lower() not in pinned_lines]

    for key, lines in lines_by_heading.items():
        doc.setdefault(key, []).extend(lines)

    return render_user_doc(doc)


def without_line(markdown: str, line: str) -> str:
    """Remove one rendered line while budgeting a pin replacement."""
    doc = parse_user_doc(markdown)
    target = normalize_line(line).lower()
    for key in doc:
        doc[key] = [existing for existing in doc[key] if existing.lower() != target]
    return render_user_doc(doc)


def normalize_user_doc(markdown: str) -> str:
    """Drop empty headings and fold legacy names into canonical keys."""
    return render_user_doc(parse_user_doc(markdown))


def user_doc_lines(markdown: str) -> List[Dict[str, str]]:
    """Every bullet in the file, for showing the writer what it may replace."""
    out: List[Dict[str, str]] = []
    for key, lines in parse_user_doc(markdown).items():
        for line in lines:
            out.append({"key": key, "heading": heading_for(key), "line": line})
    return out
