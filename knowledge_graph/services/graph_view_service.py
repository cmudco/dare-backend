"""Read models for the knowledge graph page."""

import math
from collections import Counter, defaultdict
from dataclasses import asdict
from typing import Dict, List, Optional, Set, Tuple

from core.services.background_model_service import BackgroundModelUnavailable
from core.services.decisions_service import DecisionsUnavailable
from files.models import DocumentChunk, DocumentEntity
from knowledge_graph.constants import LINKING_EXCLUDED_KINDS
from knowledge_graph.domain.alias_rules import generic_keys
from knowledge_graph.models import (
    ClusterAlias,
    EntityCluster,
    EntityRelation,
    KnowledgeGraph,
)
from knowledge_graph.services.relation_service import RelationBuildService

MAX_NODES = 60
PERSON_BONUS = 3
MAX_FOCUS_NEIGHBOURS = 12
MAX_LINK_EDGES = 90
MAX_MENTIONS = 30
PREVIEW_CHARS = 240
RELATION_FIELDS = (
    "subject_id",
    "object_id",
    "probability",
    "evidence",
    "label__name",
    "file__name",
    "chunk__page_start",
)


class GraphViewService:
    @staticmethod
    def _file_ids(graph: KnowledgeGraph) -> List[int]:
        return list(
            graph.files.filter(is_deleted=False, is_active=True).values_list(
                "id", flat=True
            )
        )

    @classmethod
    def summary(cls, graph: KnowledgeGraph) -> Dict:
        file_ids = cls._file_ids(graph)
        return {
            "id": graph.id,
            "name": graph.name,
            "description": graph.description,
            "goal": graph.goal,
            "status": graph.status,
            "status_label": graph.get_status_display(),
            "error": graph.error,
            "file_ids": file_ids,
            "chunks_total": graph.chunks_total,
            "chunks_done": graph.chunks_done,
            "tokens_used": graph.tokens_used,
            "approved_at": graph.approved_at,
            "built_at": graph.built_at,
            "created_at": graph.created_at,
            "relation_count": graph.relations.count(),
            "label_count": graph.labels.count(),
            "chunk_count": DocumentChunk.objects.filter(file_id__in=file_ids).count(),
            "has_eval_report": bool(graph.eval_report),
            "proposed_types": [
                item
                for item in graph.proposed_types
                if item["name"] not in set(graph.labels.values_list("name", flat=True))
            ],
        }

    @classmethod
    def view(cls, graph: KnowledgeGraph, focus: Optional[int] = None) -> Dict:
        file_ids = cls._file_ids(graph)
        aliases = list(
            ClusterAlias.objects.filter(user=graph.user).values(
                "key", "cluster_id", "text", "source"
            )
        )
        cluster_of = {row["key"]: row["cluster_id"] for row in aliases}
        rows = list(
            DocumentEntity.objects.filter(file_id__in=file_ids)
            .exclude(kind__in=LINKING_EXCLUDED_KINDS)
            .values("chunk_id", "file_id", "key", "text", "mentions")
        )
        generic = generic_keys(
            (row["key"], row["text"], row["mentions"], row["file_id"]) for row in rows
        )
        mentions: Counter = Counter()
        files_of: Dict[int, Set[int]] = defaultdict(set)
        chunk_members: Dict[int, Set[int]] = defaultdict(set)
        keys_seen: Dict[int, Set[str]] = defaultdict(set)
        for row in rows:
            cluster_id = cluster_of.get(row["key"])
            if cluster_id is None:
                continue
            mentions[cluster_id] += row["mentions"]
            files_of[cluster_id].add(row["file_id"])
            chunk_members[row["chunk_id"]].add(cluster_id)
            keys_seen[cluster_id].add(row["key"])

        # Only these columns: a file row carries its whole parsed document.
        relations = list(
            EntityRelation.objects.filter(graph=graph)
            .select_related("label", "file", "chunk")
            .only(*RELATION_FIELDS)
            .order_by("-probability")
        )
        relation_degree: Counter = Counter()
        grouped: Dict[Tuple[int, str, int], Dict] = {}
        for relation in relations:
            slot = (relation.subject_id, relation.label.name, relation.object_id)
            relation_degree[relation.subject_id] += 1
            relation_degree[relation.object_id] += 1
            if slot not in grouped:
                grouped[slot] = {
                    "source": relation.subject_id,
                    "target": relation.object_id,
                    "label": relation.label.name,
                    "probability": relation.probability,
                    "evidence": relation.evidence,
                    "file_name": relation.file.name,
                    "page": relation.chunk.page_start,
                    "count": 0,
                }
            grouped[slot]["count"] += 1

        kinds = dict(
            EntityCluster.objects.filter(id__in=mentions).values_list("id", "kind")
        )

        def importance(cluster_id: int) -> float:
            # A name in nearly every file is form boilerplate, not a connection.
            spread = len(files_of[cluster_id])
            rarity = (1 - spread / max(len(file_ids), 1)) ** 2
            return (
                5 * relation_degree[cluster_id]
                + 3 * (spread - 1) * rarity
                + math.log1p(mentions[cluster_id])
                + (PERSON_BONUS if kinds.get(cluster_id) == "person" else 0)
            )

        ranked = sorted(mentions, key=importance, reverse=True)
        # A common noun like "government" links everything and explains nothing.
        named = [c for c in ranked if keys_seen[c] - generic]
        chosen = named[:MAX_NODES]
        # Searching for an entity outside the top nodes brings it and its closest
        # neighbours onto the canvas.
        if focus in mentions:
            neighbours: Counter = Counter()
            for members in chunk_members.values():
                if focus in members:
                    neighbours.update(members - {focus})
            nearest = [cluster_id for cluster_id, _ in neighbours.most_common()]
            for cluster_id in [focus, *nearest[:MAX_FOCUS_NEIGHBOURS]]:
                if cluster_id not in chosen:
                    chosen.append(cluster_id)
        chosen_set = set(chosen)
        all_clusters = EntityCluster.objects.filter(id__in=mentions).in_bulk()
        clusters = {key: all_clusters[key] for key in chosen_set if key in all_clusters}
        nodes = [
            {
                "id": cluster_id,
                "label": clusters[cluster_id].label,
                "kind": clusters[cluster_id].kind,
                "mentions": mentions[cluster_id],
                "files": len(files_of[cluster_id]),
                "aliases": len(keys_seen[cluster_id]),
                "relations": relation_degree[cluster_id],
            }
            for cluster_id in chosen
            if cluster_id in clusters
        ]
        co_mentions: Counter = Counter()
        for members in chunk_members.values():
            present = sorted(members & chosen_set)
            for index, a in enumerate(present):
                for b in present[index + 1 :]:
                    co_mentions[(a, b)] += 1
        typed_pairs = {tuple(sorted((s, t))) for s, _, t in grouped}
        strongest = {
            pair
            for pair, _ in co_mentions.most_common(MAX_LINK_EDGES)
            if pair not in typed_pairs
        }
        # Every pair ships so a click can show all of an entity's links; only the
        # strong ones shape the layout, so selecting never moves the graph.
        links = [
            {
                "source": a,
                "target": b,
                "weight": weight,
                "strong": (a, b) in strongest or focus in (a, b),
            }
            for (a, b), weight in co_mentions.most_common()
            if (a, b) not in typed_pairs
        ]
        return {
            "nodes": nodes,
            "entities": [
                {"id": cluster_id, "label": all_clusters[cluster_id].label}
                for cluster_id in ranked
                if cluster_id in all_clusters
            ],
            "relations": [
                edge
                for edge in grouped.values()
                if edge["source"] in chosen_set and edge["target"] in chosen_set
            ],
            "links": links,
            "labels": [
                {
                    "id": label.id,
                    "name": label.name,
                    "description": label.description,
                    "source": label.source,
                    "relation_count": label.relations.count(),
                    "read": label.read_at is not None,
                }
                for label in graph.labels.all()
            ],
            "stats": {
                "documents": len(file_ids),
                "passages": DocumentChunk.objects.filter(file_id__in=file_ids).count(),
                "entities": len(mentions),
                "shown_entities": len(nodes),
                "spellings": sum(len(keys) for keys in keys_seen.values()),
                "merged_entities": sum(
                    1 for keys in keys_seen.values() if len(keys) > 1
                ),
                "cross_document_entities": sum(
                    1 for files in files_of.values() if len(files) > 1
                ),
                "relations": len(relations),
            },
        }

    @staticmethod
    def cluster_detail(graph: KnowledgeGraph, cluster: EntityCluster) -> Dict:
        file_ids = GraphViewService._file_ids(graph)
        aliases = list(cluster.aliases.all())
        keys = [alias.key for alias in aliases]
        rows_for_files = list(
            DocumentEntity.objects.filter(file_id__in=file_ids, key__in=keys).only(
                "file_id"
            )
        )
        rows = (
            DocumentEntity.objects.filter(file_id__in=file_ids, key__in=keys)
            .select_related("chunk", "file")
            .only(
                "text",
                "mentions",
                "file_id",
                "file__name",
                "chunk__text",
                "chunk__page_start",
                "chunk__chunk_index",
            )
            .order_by("-mentions")[:MAX_MENTIONS]
        )
        mentions = []
        seen_chunks = set()
        for row in rows:
            if row.chunk_id in seen_chunks:
                continue
            seen_chunks.add(row.chunk_id)
            text = row.chunk.text
            position = max(text.lower().find(row.text.lower()), 0)
            start = max(position - PREVIEW_CHARS // 2, 0)
            mentions.append(
                {
                    "file_id": row.file_id,
                    "file_name": row.file.name,
                    "page": row.chunk.page_start,
                    "chunk_index": row.chunk.chunk_index,
                    "text": row.text,
                    "preview": " ".join(text[start : start + PREVIEW_CHARS].split()),
                }
            )
        relations = (
            (
                EntityRelation.objects.filter(graph=graph, subject=cluster)
                | EntityRelation.objects.filter(graph=graph, object=cluster)
            )
            .select_related("subject", "object", "label", "file", "chunk")
            .only(*RELATION_FIELDS, "subject__label", "object__label")
        )
        return {
            "id": cluster.id,
            "label": cluster.label,
            "kind": cluster.kind,
            "files": len({row.file_id for row in rows_for_files}),
            "aliases": [
                {
                    "key": alias.key,
                    "text": alias.text,
                    "source": alias.source,
                    "probability": alias.probability,
                }
                for alias in aliases
            ],
            "mentions": mentions,
            "relations": [
                {
                    "id": relation.id,
                    "subject_id": relation.subject_id,
                    "subject": relation.subject.label,
                    "label": relation.label.name,
                    "object_id": relation.object_id,
                    "object": relation.object.label,
                    "probability": relation.probability,
                    "evidence": relation.evidence,
                    "file_name": relation.file.name,
                    "page": relation.chunk.page_start,
                }
                for relation in relations[:MAX_MENTIONS]
            ],
        }

    @staticmethod
    def estimate(graph: KnowledgeGraph) -> Dict:
        payload: Dict = {
            "discovery_cost": None,
            "build": None,
            "new_types": None,
            "unavailable_reason": "",
        }
        try:
            payload["discovery_cost"] = RelationBuildService.discovery_estimate(graph)
            build, new_types = RelationBuildService.estimates(graph)
            payload["build"] = asdict(build)
            payload["new_types"] = asdict(new_types) if new_types else None
        except (BackgroundModelUnavailable, DecisionsUnavailable) as error:
            payload["unavailable_reason"] = str(error)
        return payload
