"""Read models for the knowledge graph page."""

import math
from collections import Counter, defaultdict
from dataclasses import asdict
from typing import Dict, List, Set, Tuple

from core.services.background_model_service import BackgroundModelUnavailable
from core.services.decisions_service import DecisionsUnavailable
from files.models import DocumentChunk, DocumentEntity
from knowledge_graph.constants import LINKING_EXCLUDED_KINDS
from knowledge_graph.models import (
    ClusterAlias,
    EntityCluster,
    EntityRelation,
    KnowledgeGraph,
)
from knowledge_graph.services.relation_service import RelationBuildService

MAX_NODES = 60
MAX_LINK_EDGES = 90
MAX_MENTIONS = 30
PREVIEW_CHARS = 240


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
        }

    @classmethod
    def view(cls, graph: KnowledgeGraph) -> Dict:
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
            .values("chunk_id", "file_id", "key", "mentions")
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

        relations = list(
            EntityRelation.objects.filter(graph=graph)
            .select_related("label", "file", "chunk")
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

        def importance(cluster_id: int) -> float:
            return (
                5 * relation_degree[cluster_id]
                + 3 * (len(files_of[cluster_id]) - 1)
                + math.log1p(mentions[cluster_id])
            )

        chosen = sorted(mentions, key=importance, reverse=True)[:MAX_NODES]
        chosen_set = set(chosen)
        clusters = EntityCluster.objects.filter(id__in=chosen_set).in_bulk()
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
        links = [
            {"source": a, "target": b, "weight": weight}
            for (a, b), weight in co_mentions.most_common(MAX_LINK_EDGES)
            if (a, b) not in typed_pairs
        ]
        return {
            "nodes": nodes,
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
        rows = (
            DocumentEntity.objects.filter(file_id__in=file_ids, key__in=keys)
            .select_related("chunk", "file")
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
            EntityRelation.objects.filter(graph=graph, subject=cluster)
            | EntityRelation.objects.filter(graph=graph, object=cluster)
        ).select_related("subject", "object", "label", "file", "chunk")
        return {
            "id": cluster.id,
            "label": cluster.label,
            "kind": cluster.kind,
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
            "unavailable_reason": "",
        }
        try:
            payload["discovery_cost"] = RelationBuildService.discovery_estimate(graph)
            payload["build"] = asdict(RelationBuildService.estimate(graph))
        except (BackgroundModelUnavailable, DecisionsUnavailable) as error:
            payload["unavailable_reason"] = str(error)
        return payload
