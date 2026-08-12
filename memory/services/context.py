"""One turn's memory context, assembled.

Three reads against one query embedding: USER.md (no query — always there),
facts (retrieved by the question, top-3, floor .30), and procedures (retrieved
by the task, top-5, floor .22 — deliberately wider, because one extra rule is
a line the model can ignore and one missing rule is repeating a corrected
mistake).

The block framing is ported from the prototype's chat route: each layer gets
its own preamble so rules never read as facts and retired facts never read as
current. DARE reserves the sole system message for the user's saved prompt,
so the block is injected as a user-role message — a deliberate deviation, and
the same convention every other context layer here follows.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from memory.constants import (
    PROCEDURE_FLOOR,
    PROCEDURE_RELEVANCE_FLOOR,
    PROCEDURE_SHORTLIST_LIMIT,
    PROCEDURE_TOP_K,
    MemoryKind,
    MemoryState,
)
from memory.domain.procedural import (
    TaskContext,
    format_procedures,
    task_query,
    trigger_of,
)
from memory.domain.user_doc import user_doc_lines
from memory.services.embeddings import embed_one
from memory.services.retrieval import Recall, retrieve
from memory.services.store import read_user_doc

_USER_MD_PREAMBLE = (
    "The complete curated USER.md for the active user is included below. Use "
    "it naturally to personalize your answer, and follow the communication "
    "preferences it states. Do not announce that you are reading a file "
    "unless the user asks about memory directly. Never invent facts that are "
    "not in the conversation or in USER.md.\n\n"
    "If the user asks you to remember something, acknowledge it in one short "
    "sentence. A separate memory writer decides where it goes once this reply "
    "finishes — do not claim you have written to a particular section, "
    "because you do not know which one it chose."
)

_MEMORIES_PREAMBLE = (
    "These memories were retrieved from the archive because they look "
    "relevant to this message. Use them only where they genuinely apply, and "
    'never list them back unless asked. A memory marked "no longer current" '
    "describes the past: mention it only if the question is about the past."
)

_PROCEDURES_PREAMBLE = (
    "Standing instructions this person has given you for situations like "
    "this one. Unlike the memories above, these are not information to use — "
    "they are rules to follow, and they were written down because you were "
    "corrected once. Follow them silently."
)


@dataclass
class ReadContext:
    user_doc: str
    facts: Recall
    procedures: Recall
    block: str
    # What the FE's per-message memory panel shows: {content, memory_type,
    # categories} per item, matching Message.memory_context_data's shape.
    items: List[Dict[str, Any]] = field(default_factory=list)


def read_context(user, question: str) -> ReadContext:
    """The three reads, sharing exactly one query embedding.

    ``embed_query=False`` on both funnels: this is the only place the question
    is embedded, so a failure here degrades the whole turn to lexical ranking
    once rather than retrying per funnel.
    """
    user_doc = read_user_doc(user)
    vector = embed_one(question)

    facts = retrieve(
        user,
        question,
        kind=MemoryKind.FACT,
        query_vector=vector,
        embed_query=False,
    )

    task = TaskContext(message=question)
    procedures = retrieve(
        user,
        task_query(task),
        kind=MemoryKind.PROCEDURE,
        top_k=PROCEDURE_TOP_K,
        floor=PROCEDURE_FLOOR,
        shortlist_limit=PROCEDURE_SHORTLIST_LIMIT,
        query_vector=vector,
        embed_query=False,
        relevance_floor=PROCEDURE_RELEVANCE_FLOOR,
    )

    procedure_block = format_procedures([item.record for item in procedures.chosen])

    parts: List[str] = []
    if user_doc.strip():
        parts.append(f"{_USER_MD_PREAMBLE}\n\n<user_md>\n{user_doc}\n</user_md>")
    if facts.context:
        parts.append(
            f"{_MEMORIES_PREAMBLE}\n\n<retrieved_memories>\n{facts.context}\n"
            f"</retrieved_memories>"
        )
    if procedure_block:
        parts.append(
            f"{_PROCEDURES_PREAMBLE}\n\n<procedures>\n{procedure_block}\n</procedures>"
        )

    return ReadContext(
        user_doc=user_doc,
        facts=facts,
        procedures=procedures,
        block="\n\n".join(parts),
        items=_display_items(user_doc, facts, procedures),
    )


def _display_items(user_doc: str, facts: Recall, procedures: Recall):
    """Everything this turn's prompt actually carried, for the message's
    memory panel — the always-injected profile lines included, because
    transparency about what the model saw is the panel's entire point."""
    items: List[Dict[str, Any]] = []
    for line in user_doc_lines(user_doc):
        items.append(
            {
                "content": line["line"],
                "memory_type": "profile",
                "categories": [line["key"]],
            }
        )
    for item in facts.chosen:
        categories = [part for part in item.record.key.split(":") if part] or [
            item.record.kind
        ]
        if item.record.state == MemoryState.SUPERSEDED:
            categories.append("no-longer-current")
        items.append(
            {
                "content": item.record.text,
                "memory_type": "knowledge",
                "categories": categories,
            }
        )
    for item in procedures.chosen:
        items.append(
            {
                "content": item.record.text,
                "memory_type": "behavior",
                "categories": [trigger_of(item.record.key)],
            }
        )
    return items
