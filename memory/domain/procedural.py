"""Procedural memory — how to do things, not what is true.

The other two layers are both retrieved by the QUESTION. USER.md is pasted in
whole; facts are searched against what the person just asked. Procedures break
that assumption: "never use npm in this repo, always pnpm" matters on the turn
where an install is about to happen, and that turn usually says nothing about
package managers. So a procedure is retrieved by the TASK: what is about to
happen, not what was asked.

    fact       → reached by the question    "where do I live?"
    procedure  → reached by the task        about to write a commit message

In plain chat the task signal is thin, so the task context is the user's
message plus whatever the caller knows about what it is about to do. The shape
is what matters: the moment the loop has tools, ``about`` is where tool names
and file paths go — and nothing else in this module changes.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List

from memory.constants import (
    PROCEDURE_FLOOR,
    PROCEDURE_SHORTLIST_LIMIT,
    PROCEDURE_TOP_K,
    MemoryKind,
)
from memory.domain.types import MemoryRow


@dataclass
class TaskContext:
    # What the person said. Always available, always a weak signal on its own.
    message: str
    # What the assistant is about to do, when anything knows: tool names, file
    # paths, technologies. Empty in plain chat.
    about: List[str] = field(default_factory=list)


def task_query(context: TaskContext) -> str:
    return " ".join(part for part in [context.message, *context.about] if part).strip()


def procedure_query(task: TaskContext) -> Dict[str, Any]:
    """The retrieval settings this layer wants.

    Returned rather than executed so this module stays pure — the funnel lives
    in the store, which is the module allowed to touch storage. What belongs
    here is the policy: what a procedure IS, how widely to cast for one, and
    how to write it into a prompt.
    """
    return {
        "query": task_query(task),
        "kind": MemoryKind.PROCEDURE,
        "top_k": PROCEDURE_TOP_K,
        "floor": PROCEDURE_FLOOR,
        "shortlist_limit": PROCEDURE_SHORTLIST_LIMIT,
    }


def format_procedures(records: List[MemoryRow]) -> str:
    """The block that goes in the prompt.

    Rendered as imperatives with their trigger attached, because a rule
    stripped of its trigger becomes a global instruction — "use pnpm" reads as
    always, everywhere, including in the repo where the person uses npm on
    purpose.
    """
    if not records:
        return ""
    return "\n".join(f"- When {trigger_of(row.key)}: {row.text}" for row in records)


def trigger_of(key: str) -> str:
    """The readable situation out of a key.

    ``when:<situation>:<which-rule>`` — the third segment exists only to keep
    two rules for one situation from colliding, and repeating it back to the
    model is noise.
    """
    if not key.startswith("when:"):
        return key
    situation = key[5:].split(":")[0]
    return situation.replace("-", " ")
