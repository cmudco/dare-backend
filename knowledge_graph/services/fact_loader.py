"""Loads the stored connections around the names a question asks about."""

from typing import Dict, Iterable, List, Optional, Sequence, Set

from core.services.rag.facts import StoredFact
from knowledge_graph.models import EntityCluster, EntityRelation
from knowledge_graph.services.walk_loader import match_names


def load_facts(
    user_id: Optional[int], file_ids: Sequence[int], names: Iterable[str]
) -> List[StoredFact]:
    """Connections of the named entities, then of the entities those reach."""
    rows = list(
        EntityRelation.objects.filter(graph__user_id=user_id, file_id__in=file_ids)
        .values_list(
            "chunk_id",
            "subject_id",
            "label__name",
            "object_id",
            "file__name",
            "chunk__page_start",
        )
        .order_by("id")
    )
    connected = {row[1] for row in rows} | {row[3] for row in rows}
    named = set(match_names(user_id, names, connected).values())
    if not named:
        return []
    near = set(named)
    reached_from: Dict[int, Set[int]] = {}
    for _, subject, _, target, _, _ in rows:
        if subject in named or target in named:
            near.update((subject, target))
        if subject in named:
            reached_from.setdefault(target, set()).add(subject)
        if target in named:
            reached_from.setdefault(subject, set()).add(target)
    shared = {entity for entity, origins in reached_from.items() if len(origins) > 1}

    def step(subject: int, target: int) -> int:
        if subject in named and target in named:
            return 0
        if (subject in named and target in shared) or (
            target in named and subject in shared
        ):
            return 0
        return 1 if subject in named or target in named else 2

    # Two graphs over the same file both store the passage's connection.
    kept = list(dict.fromkeys(row for row in rows if row[1] in near or row[3] in near))
    labels = dict(
        EntityCluster.objects.filter(
            user_id=user_id,
            id__in={row[1] for row in kept} | {row[3] for row in kept},
        ).values_list("id", "label")
    )
    return [
        StoredFact(
            subject=labels[subject],
            relation=relation,
            object=labels[target],
            file_name=file_name or "",
            page=page,
            step=step(subject, target),
        )
        for _, subject, relation, target, file_name, page in kept
    ]
