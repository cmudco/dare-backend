"""USER.md — the sticky layer.

One short document per user, always injected, never searched. Deliberately
plain: no ids, no dates, no validity — a line is either true of the person or
it does not belong in a file read on every single turn. Everything that needs
a timeline lives in the archive.

The budget is enforced rather than suggested. A ceiling with no eviction rule
gets crossed on an ordinary Tuesday and then it is decoration.
"""

import math
import re
from dataclasses import dataclass
from typing import Dict, List, Optional

from memory.constants import TOKEN_BUDGET

# The headings, keyed. A key is what the writer returns and a heading is what
# gets written, so this table is the only place either is defined. Defaults,
# not an ontology: empty headings are dropped on render, and a heading someone
# adds by hand survives untouched.
PROFILE_HEADINGS: Dict[str, str] = {
    "identity": "Identity",
    "background": "Background",
    "communication": "Communication",
    "working-preferences": "Working preferences",
    "constraints": "Constraints",
    "boundaries": "Boundaries",
}

# Canonical keys, in render order.
PROFILE_KEYS = list(PROFILE_HEADINGS)

# Keys older writers used, and where they belong now.
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
    """Characters per token, near enough. A real tokenizer is a dependency;
    this is within a few percent for English prose, and the budget is a soft
    target with a hard edge rather than an accounting figure."""
    return math.ceil(len(text.strip()) / 4)


def normalize_line(text: str) -> str:
    """Normalize a bullet: no leading dash, one trailing period, single spaces."""
    body = re.sub(r"^[-*+]\s*", "", text)
    body = re.sub(r"\s+", " ", body)
    body = re.sub(r"[.\s]+$", "", body).strip()
    return f"{body}." if body else ""


def parse_user_doc(markdown: str) -> Dict[str, List[str]]:
    """The parsed file: one ordered map from key to lines.

    Canonical keys are seeded first so they render in a stable order; anything
    a human added lands after them, in the order it was found.
    """
    doc: Dict[str, List[str]] = {key: [] for key in PROFILE_KEYS}
    current: Optional[List[str]] = None

    for raw_line in markdown.split("\n"):
        line = raw_line.strip()
        if not line:
            continue

        heading_match = _HEADING_RE.match(line)
        if heading_match:
            key = key_of(heading_match.group(1))
            # `# User` is the document title, not a section.
            if re.match(r"^#\s", line) and key not in PROFILE_HEADINGS:
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


def normalize_user_doc(markdown: str) -> str:
    """Drop empty headings and fold legacy names into the canonical keys.

    Hand edits go through this exact normalizer too, so a machine write and a
    human write can never disagree about what the same line looks like.
    """
    return render_user_doc(parse_user_doc(markdown))


@dataclass
class PatchResult:
    ok: bool
    markdown: Optional[str] = None
    note: Optional[str] = None
    reason: Optional[str] = None


def patch_user_doc(
    markdown: str,
    key: str,
    line: str,
    replaces_line: Optional[str] = None,
    force: bool = False,
) -> PatchResult:
    """Append one line under one heading, creating the heading if it is new.

    Refuses rather than silently overflowing. ``replaces_line`` is how a write
    gets in when the document is already full: swap a line, do not add one.
    ``force`` writes past the ceiling and is reserved for safety facts, where
    the budget is the cheaper thing to break.
    """
    normalized = normalize_line(line)
    if not normalized:
        return PatchResult(ok=False, reason="The proposed line was empty.")

    canonical_key = key_of(key)
    if not canonical_key:
        return PatchResult(ok=False, reason="The proposed heading was empty.")

    doc = parse_user_doc(markdown)
    doc.setdefault(canonical_key, [])

    note: Optional[str] = None

    replacement = normalize_line(replaces_line) if replaces_line else None
    if replacement:
        for lines in doc.values():
            for index, existing in enumerate(lines):
                if existing.lower() == replacement.lower():
                    lines.pop(index)
                    note = f'Replaced "{replacement}"'
                    break
            if note:
                break

    # A line that already says this — under any heading — is not worth a copy.
    duplicate = any(
        existing.lower() == normalized.lower()
        for lines in doc.values()
        for existing in lines
    )
    if duplicate:
        return PatchResult(ok=False, reason="USER.md already says this.")

    doc[canonical_key].append(normalized)
    rendered = render_user_doc(doc)
    tokens = estimate_tokens(rendered)

    # Over the ceiling is normally a refusal. The exceptions are a safety fact,
    # and a swap that leaves the file no larger than it already was — a
    # hand-edited file can end up over budget, and refusing every repair would
    # strand it there.
    if (
        tokens > TOKEN_BUDGET
        and not force
        and not (note and tokens <= estimate_tokens(markdown))
    ):
        return PatchResult(
            ok=False,
            reason=(
                f"USER.md would reach {tokens} tokens, past the {TOKEN_BUDGET} "
                f"ceiling. Replace an existing line with a shorter one, or "
                f"leave this to the archive."
            ),
        )

    return PatchResult(ok=True, markdown=rendered, note=note)


def user_doc_lines(markdown: str) -> List[Dict[str, str]]:
    """Every bullet in the file, for showing the writer what it may replace."""
    out: List[Dict[str, str]] = []
    for key, lines in parse_user_doc(markdown).items():
        for line in lines:
            out.append({"key": key, "heading": heading_for(key), "line": line})
    return out
