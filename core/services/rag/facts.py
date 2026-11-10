"""Stored connections about the names a question asks about, as model context.

Pure: the rows come from an injected loader, so nothing here touches the ORM.
"""

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

FACT_CHAR_BUDGET = 6000
MAX_SOURCES_PER_FACT = 2

_HEADER = (
    "[stored connections] A model read these from the files, and each was checked "
    "against the passage that states it. Use them to answer who is connected to "
    "whom, including across files, and follow one connection to the next when the "
    "question chains. Name the file a connection comes from, and cite a passage "
    "below with its [S#] tag when one states the same thing. The list is "
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


def facts_block(facts: Sequence[StoredFact], budget: int = FACT_CHAR_BUDGET) -> str:
    """One line per distinct connection, most-stated first, nearest names first."""
    grouped: Dict[Tuple[str, str, str], List[StoredFact]] = {}
    for fact in facts:
        grouped.setdefault((fact.subject, fact.relation, fact.object), []).append(fact)
    ordered = sorted(
        grouped.items(),
        key=lambda item: (min(f.step for f in item[1]), -len(item[1]), item[0]),
    )
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
