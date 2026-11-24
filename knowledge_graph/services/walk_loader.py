"""Loads one user's graph over the selected files for the retrieval walk."""

from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from core.services.rag.graph_walker import (
    WalkChunk,
    WalkEntityLink,
    WalkGraph,
    WalkPointer,
    WalkRelation,
)
from core.services.rag.structured_chunker import retrieval_text_for
from files.models import DocumentChunk, DocumentEntity, DocumentReference
from knowledge_graph.constants import LINKING_EXCLUDED_KINDS
from knowledge_graph.domain.alias_rules import blocking_key, generic_keys
from knowledge_graph.models import ClusterAlias, EntityCluster, EntityRelation

ChunkKey = Tuple[str, int]


def load_walk_graph(user_id: Optional[int], file_ids: Sequence[int]) -> WalkGraph:
    """Chunks, entity links, pointers and typed facts, all scoped to the user."""
    aliases = dict(
        ClusterAlias.objects.filter(user_id=user_id).values_list("key", "cluster_id")
    )
    links: List[WalkEntityLink] = []
    names: Dict[int, str] = {}
    rows = list(
        DocumentEntity.objects.filter(file__user_id=user_id, file_id__in=file_ids)
        .exclude(kind__in=LINKING_EXCLUDED_KINDS)
        .values("file_id", "chunk__chunk_index", "key", "text", "mentions")
    )
    # "Affiant" or "board" would join every form to every other one.
    generic = generic_keys(
        (row["key"], row["text"], row["mentions"], row["file_id"]) for row in rows
    )
    for row in rows:
        cluster_id = aliases.get(row["key"])
        if cluster_id is None or row["key"] in generic:
            continue
        links.append(
            WalkEntityLink(
                chunk=(str(row["file_id"]), row["chunk__chunk_index"]),
                entity_id=cluster_id,
                text=row["text"],
                mentions=row["mentions"],
            )
        )
    for cluster_id, label in EntityCluster.objects.filter(
        user_id=user_id, id__in={link.entity_id for link in links}
    ).values_list("id", "label"):
        names[cluster_id] = label
    pointers = [
        WalkPointer(
            source=(str(row["file_id"]), row["source_chunk__chunk_index"]),
            target=(str(row["file_id"]), row["target_chunk__chunk_index"]),
            text=row["raw_text"],
        )
        for row in DocumentReference.objects.filter(
            file__user_id=user_id, file_id__in=file_ids, target_chunk__isnull=False
        ).values(
            "file_id",
            "source_chunk__chunk_index",
            "target_chunk__chunk_index",
            "raw_text",
        )
    ]
    relations = [
        WalkRelation(
            subject=row["subject_id"],
            object=row["object_id"],
            label=row["label__name"],
            probability=row["probability"],
        )
        for row in EntityRelation.objects.filter(
            graph__user_id=user_id, file_id__in=file_ids
        ).values("subject_id", "object_id", "label__name", "probability")
    ]
    return WalkGraph(
        links=links,
        pointers=pointers,
        relations=relations,
        entity_names=names,
    )


def resolve_query_entities(
    user_id: Optional[int], names: Iterable[str], graph: WalkGraph
) -> Dict[str, int]:
    """Map names from the question onto entities present in the selected files."""
    return match_names(user_id, names, {link.entity_id for link in graph.links})


def match_names(
    user_id: Optional[int], names: Iterable[str], present: Set[int]
) -> Dict[str, int]:
    """Each name to the one entity in ``present`` that a spelling of it fits."""
    by_block: Dict[str, int] = {}
    tokens_by_entity: Dict[int, List[set]] = {}
    for key, cluster_id in ClusterAlias.objects.filter(
        user_id=user_id, cluster_id__in=present
    ).values_list("key", "cluster_id"):
        block = blocking_key(key)
        by_block.setdefault(block, cluster_id)
        tokens_by_entity.setdefault(cluster_id, []).append(set(block.split()))
    resolved: Dict[str, int] = {}
    for name in names:
        block = blocking_key(name)
        if not block:
            continue
        if block in by_block:
            resolved[name] = by_block[block]
            continue
        wanted = set(block.split())
        matches = {
            entity
            for entity, token_sets in tokens_by_entity.items()
            if any(wanted <= tokens for tokens in token_sets)
        }
        if len(matches) == 1:
            resolved[name] = next(iter(matches))
    return resolved


def load_walk_chunks(
    keys: Iterable[ChunkKey], user_id: Optional[int]
) -> Dict[ChunkKey, WalkChunk]:
    wanted = {(str(file_id), int(index)) for file_id, index in keys}
    if not wanted:
        return {}
    rows = DocumentChunk.objects.filter(
        file__user_id=user_id,
        file_id__in={int(file_id) for file_id, _ in wanted},
        chunk_index__in={index for _, index in wanted},
    ).select_related("file")
    # A file row carries its whole parsed document; only the name is needed.
    rows = rows.only(
        "file_id",
        "chunk_index",
        "text",
        "page_start",
        "page_end",
        "section",
        "heading_path",
        "file__name",
    )
    found: Dict[ChunkKey, WalkChunk] = {}
    for row in rows:
        key = (str(row.file_id), row.chunk_index)
        if key in wanted:
            found[key] = WalkChunk(
                key=key,
                text=row.text,
                file_name=row.file.name or "",
                page_start=row.page_start,
                page_end=row.page_end,
                section=row.section or "",
                retrieval_text=retrieval_text_for(
                    row.text, tuple(row.heading_path or ())
                ),
            )
    return found
