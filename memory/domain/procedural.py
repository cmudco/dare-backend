"""Query and prompt helpers for procedural memories."""

from typing import List

from memory.domain.types import MemoryRow

_CODE_BLOCK_STARTS = (
    "class ",
    "def ",
    "function ",
    "import ",
    "#include ",
)


def _starts_code_block(line: str) -> bool:
    stripped = line.strip()
    lowered = stripped.lower()
    declaration = lowered.startswith(("const ", "let ", "var ")) and "=" in line
    sql = stripped.startswith(("SELECT ", "INSERT ", "UPDATE "))
    return (
        lowered.startswith(_CODE_BLOCK_STARTS)
        or declaration
        or sql
        or stripped in {"{", "}"}
    )


def task_query(message: str) -> str:
    """Remove pasted code so procedure retrieval focuses on the request."""
    original = message.strip()
    first_line = next(
        (line.strip() for line in original.splitlines() if line.strip()), ""
    )
    if first_line.startswith("```") or _starts_code_block(first_line):
        return original

    prose: List[str] = []
    in_fence = False
    after_blank = False
    in_code_block = False
    removed_code = False

    for line in original.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            removed_code = True
            continue
        if in_fence:
            removed_code = True
            continue
        if not stripped:
            after_blank = bool(prose)
            continue
        if after_blank and _starts_code_block(line):
            in_code_block = True
        if in_code_block:
            if line.startswith((" ", "\t")) or _starts_code_block(line):
                removed_code = True
                continue
            in_code_block = False
        prose.append(stripped)
        after_blank = False

    return (" ".join(prose) if removed_code else original) or original


def format_procedures(records: List[MemoryRow]) -> str:
    """Render each rule with the situation in which it applies."""
    return "\n".join(f"- When {trigger_of(row.key)}: {row.text}" for row in records)


def trigger_of(key: str) -> str:
    """Return the readable trigger from a procedure key."""
    if not key.startswith("when:"):
        return key
    situation = key[5:].split(":")[0]
    return situation.replace("-", " ")
