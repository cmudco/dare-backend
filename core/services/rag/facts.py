"""Stored connections about the names a question asks about, as model context.

Pure: the rows come from an injected loader, so nothing here touches the ORM.
"""

import re
from dataclasses import dataclass
from typing import Callable, Dict, FrozenSet, List, Optional, Sequence, Tuple

FACT_CHAR_BUDGET = 6000
MAX_SOURCES_PER_FACT = 2
# Letters of a relation's first word a question word must start with: "serv"
# lets "serve" and "service" ask about served_in.
STEM_CHARS = 4

_HEADER = (
    "[stored connections] Facts already read from the files; each was checked "
    "against the passage that states it. They are evidence equal to the passages: "
    "when a connection answers the question, state it and name its file, even if "
    "no passage repeats it. When the question chains, such as who did something "
    "and then a fact about them, follow the first connection to the next. Cite a "
    "passage with its [S#] tag when one states the same thing. The list is "
    "incomplete: a connection missing here may still be in the files, so never "
    "say something is false only because it is not listed."
)


@dataclass(frozen=True)
class StoredFact:
    """One passage stating one connection."""

    subject: str
    relation: str
    object: str
    file_name: str
    page: Optional[int] = None
    # 0 joins two names in the question, 1 touches one name, 2 is one further
    step: int = 1


FactLoader = Callable[[Optional[int], Sequence[int], Sequence[str]], List[StoredFact]]


def _source(fact: StoredFact) -> str:
    return f"{fact.file_name} p. {fact.page}" if fact.page else fact.file_name


def _asks_about(relation: str, question_words: FrozenSet[str]) -> bool:
    stem = relation.split("_")[0].lower()[:STEM_CHARS]
    return len(stem) == STEM_CHARS and any(
        word.startswith(stem) for word in question_words
    )


def facts_block(
    facts: Sequence[StoredFact], question: str = "", budget: int = FACT_CHAR_BUDGET
) -> str:
    """One line per distinct connection, nearest names first.

    A busy name has more connections than the budget holds, so the kinds the
    question asks about lead, and one step further the names those reach lead.
    The rest follow most-stated first.
    """
    grouped: Dict[Tuple[str, str, str], List[StoredFact]] = {}
    for fact in facts:
        grouped.setdefault((fact.subject, fact.relation, fact.object), []).append(fact)
    words = frozenset(re.findall(r"[a-z]+", question.lower()))
    asked = {key for key in grouped if _asks_about(key[1], words)}
    reached = {
        name
        for key in asked
        if min(fact.step for fact in grouped[key]) <= 1
        for name in (key[0], key[2])
    }

    def rank(item):
        key, rows = item
        step = min(fact.step for fact in rows)
        continues = key[0] in reached or key[2] in reached
        return (step, step > 1 and not continues, key not in asked, -len(rows), key)

    ordered = sorted(grouped.items(), key=rank)
    lines: List[str] = []
    used = len(_HEADER)
    further = False
    joining = bool(ordered) and min(f.step for f in ordered[0][1]) == 0
    after_joining = False
    for (subject, relation, target), rows in ordered:
        sources = list(dict.fromkeys(_source(row) for row in rows))
        shown = "; ".join(sources[:MAX_SOURCES_PER_FACT])
        count = f"; stated in {len(rows)} passages" if len(rows) > 1 else ""
        line = f"- {subject} | {relation.replace('_', ' ')} | {target} ({shown}{count})"
        nearest = min(row.step for row in rows)
        if joining and not lines:
            line = "These join the names asked about:\n" + line
        elif joining and nearest == 1 and not after_joining:
            line = "About one name only:\n" + line
            after_joining = True
        if nearest > 1 and not further:
            line = "One connection further:\n" + line
            further = True
        if used + len(line) > budget:
            break
        used += len(line) + 1
        lines.append(line)
    return "\n".join([_HEADER, *lines]) if lines else ""
