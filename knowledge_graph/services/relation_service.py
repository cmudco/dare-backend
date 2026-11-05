"""Discovers a graph's relationship types, prices the read, and runs it."""

import logging
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Dict, List, Literal, Sequence, Tuple

from asgiref.sync import async_to_sync
from django.db import close_old_connections, transaction
from django.utils import timezone
from pydantic import BaseModel

from core.constants import BackgroundTask
from core.services.background_model_service import (
    BackgroundModelService,
    BackgroundModelUnavailable,
    resolve_background_model,
)
from core.services.decisions_service import (
    Choice,
    DecisionsService,
    DecisionsUnavailable,
    Question,
    decisions_price_per_million,
)
from files.models import DocumentChunk, DocumentEntity
from knowledge_graph.constants import (
    CHARS_PER_TOKEN,
    DISCOVERY_BATCH_CHUNKS,
    DISCOVERY_SAMPLE_CHUNKS,
    ESTIMATED_DISCOVERY_OUTPUT_TOKENS,
    ESTIMATED_DISCOVERY_PROMPT_TOKENS,
    ESTIMATED_TOKENS_PER_LABEL,
    ESTIMATED_TOKENS_PER_QUESTION,
    LINKING_EXCLUDED_KINDS,
    MAX_PAIRS_PER_CHUNK,
    MAX_RELATION_LABELS,
    RELATION_CHOICE_THRESHOLD,
    RELATION_VERIFY_THRESHOLD,
    GraphStatus,
    LabelSource,
)
from knowledge_graph.domain.relation_pairs import (
    CandidatePair,
    PairMention,
    candidate_pairs,
)
from knowledge_graph.models import (
    ClusterAlias,
    EntityCluster,
    EntityRelation,
    KnowledgeGraph,
    RelationLabel,
)
from knowledge_graph.services.cluster_service import EntityClusterService

logger = logging.getLogger(__name__)

PARALLEL_REQUESTS = 6
# Reasoning models spend part of this before they write anything.
DISCOVERY_MAX_TOKENS = 4000
NO_RELATION = "none"
LABEL_SYSTEM = (
    "You maintain the list of relationship types for a knowledge graph. Each turn "
    "you see the current list and new passages. Reply with the changes to make: "
    "'add' a type the passages state and the list lacks, 'rename' one to a clearer "
    "name, or 'remove' one that overlaps another. Types must be short snake_case "
    "verbs that are general enough to recur, such as produced or directed. Never "
    "put a specific name in a type. Keep the list small: prefer one general type "
    "to several narrow ones. Reply with no changes when the list already covers "
    "the passages."
)


class LabelOperation(BaseModel):
    action: Literal["add", "rename", "remove"]
    name: str
    new_name: str
    description: str


class LabelUpdate(BaseModel):
    operations: List[LabelOperation]


@dataclass(frozen=True)
class BuildEstimate:
    chunks: int
    pairs: int
    tokens: int
    cost: float
    rate_per_million: float


def clean_label(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")[:48]


def apply_label_operations(
    graph: KnowledgeGraph, operations: Sequence[LabelOperation]
) -> None:
    """Apply model output under fixed rules: the cap holds, user labels stay."""
    labels = {label.name: label for label in graph.labels.all()}
    for operation in operations:
        name = clean_label(operation.name)
        if operation.action == "add":
            if name and name not in labels and len(labels) < MAX_RELATION_LABELS:
                labels[name] = RelationLabel.objects.create(
                    graph=graph,
                    name=name,
                    description=operation.description[:200],
                    source=LabelSource.DISCOVERED,
                )
            continue
        current = labels.get(name)
        if current is None or current.source == LabelSource.USER:
            continue
        if operation.action == "remove":
            current.delete()
            del labels[name]
            continue
        new_name = clean_label(operation.new_name)
        if new_name and new_name not in labels:
            current.name = new_name
            if operation.description:
                current.description = operation.description[:200]
            current.save(update_fields=["name", "description", "updated_at"])
            labels[new_name] = labels.pop(name)


class RelationBuildService:
    @staticmethod
    def graph_chunks(graph: KnowledgeGraph):
        file_ids = graph.files.filter(is_deleted=False, is_active=True).values("id")
        return DocumentChunk.objects.filter(file_id__in=file_ids)

    @classmethod
    def plan_pairs(cls, graph: KnowledgeGraph) -> Dict[int, List[CandidatePair]]:
        """Candidate pairs per chunk. One owner for the estimate and the build."""
        alias_cluster = dict(
            ClusterAlias.objects.filter(user=graph.user).values_list(
                "key", "cluster_id"
            )
        )
        mentions: Dict[int, List[PairMention]] = defaultdict(list)
        rows = (
            DocumentEntity.objects.filter(
                chunk__in=cls.graph_chunks(graph).values("id")
            )
            .exclude(kind__in=LINKING_EXCLUDED_KINDS)
            .values("chunk_id", "key", "text")
        )
        for row in rows:
            cluster_id = alias_cluster.get(row["key"])
            if cluster_id is not None:
                mentions[row["chunk_id"]].append(PairMention(cluster_id, row["text"]))
        plan: Dict[int, List[CandidatePair]] = {}
        chunk_texts = dict(
            cls.graph_chunks(graph)
            .filter(id__in=list(mentions))
            .values_list("id", "text")
        )
        for chunk_id, items in mentions.items():
            pairs = candidate_pairs(
                chunk_texts.get(chunk_id, ""), items, MAX_PAIRS_PER_CHUNK
            )
            if pairs:
                plan[chunk_id] = pairs
        return plan

    @classmethod
    def estimate(cls, graph: KnowledgeGraph) -> BuildEstimate:
        plan = cls.plan_pairs(graph)
        lengths = dict(
            cls.graph_chunks(graph).filter(id__in=list(plan)).values_list("id", "text")
        )
        labels = max(graph.labels.count(), 1)
        per_question = (
            ESTIMATED_TOKENS_PER_QUESTION + ESTIMATED_TOKENS_PER_LABEL * labels
        )
        pairs = sum(len(items) for items in plan.values())
        tokens = sum(
            2 * len(lengths.get(chunk_id, "")) // CHARS_PER_TOKEN
            + len(items) * per_question
            for chunk_id, items in plan.items()
        )
        rate = decisions_price_per_million()
        return BuildEstimate(
            chunks=len(plan),
            pairs=pairs,
            tokens=tokens,
            cost=tokens * rate / 1_000_000,
            rate_per_million=rate,
        )

    @classmethod
    def discovery_estimate(cls, graph: KnowledgeGraph) -> float:
        route = resolve_background_model(graph.user, BackgroundTask.GRAPH_RELATIONS)
        sample = cls._discovery_sample(graph)
        calls = (len(sample) + DISCOVERY_BATCH_CHUNKS - 1) // DISCOVERY_BATCH_CHUNKS
        input_tokens = (
            sum(len(text) for text in sample) // CHARS_PER_TOKEN
            + calls * ESTIMATED_DISCOVERY_PROMPT_TOKENS
        )
        output_tokens = calls * ESTIMATED_DISCOVERY_OUTPUT_TOKENS
        model = route.model
        return (
            input_tokens * float(model.input_token_rate_per_million)
            + output_tokens * float(model.output_token_rate_per_million)
        ) / 1_000_000

    @classmethod
    def _discovery_sample(cls, graph: KnowledgeGraph) -> List[str]:
        """Passages richest in pairs, one file at a time so every file is heard."""
        plan = cls.plan_pairs(graph)
        rows = {
            row.id: row for row in cls.graph_chunks(graph).filter(id__in=list(plan))
        }
        by_file: Dict[int, List[DocumentChunk]] = defaultdict(list)
        for chunk_id in sorted(plan, key=lambda c: -len(plan[c])):
            if chunk_id in rows:
                by_file[rows[chunk_id].file_id].append(rows[chunk_id])
        sample: List[str] = []
        while by_file and len(sample) < DISCOVERY_SAMPLE_CHUNKS:
            for file_id in list(by_file):
                sample.append(by_file[file_id].pop(0).text)
                if not by_file[file_id]:
                    del by_file[file_id]
                if len(sample) >= DISCOVERY_SAMPLE_CHUNKS:
                    break
        return sample

    @classmethod
    def discover_labels(cls, graph: KnowledgeGraph) -> None:
        sample = cls._discovery_sample(graph)
        service = BackgroundModelService()
        for start in range(0, len(sample), DISCOVERY_BATCH_CHUNKS):
            passages = "\n\n---\n\n".join(
                sample[start : start + DISCOVERY_BATCH_CHUNKS]
            )
            current = [
                f"{label.name}: {label.description}" for label in graph.labels.all()
            ]
            result = async_to_sync(service.parse_structured)(
                user=graph.user,
                messages=[
                    {"role": "system", "content": LABEL_SYSTEM},
                    {
                        "role": "user",
                        "content": (
                            f"Current types ({len(current)} of "
                            f"{MAX_RELATION_LABELS} allowed):\n"
                            + ("\n".join(current) or "(none yet)")
                            + f"\n\nPassages:\n{passages}"
                        ),
                    },
                ],
                response_model=LabelUpdate,
                description="Knowledge graph relationship types",
                max_tokens=DISCOVERY_MAX_TOKENS,
                task=BackgroundTask.GRAPH_RELATIONS,
            )
            apply_label_operations(graph, result.value.operations)

    @staticmethod
    def _choice_question(
        index: int, pair: CandidatePair, labels: Sequence[RelationLabel]
    ) -> Question:
        choices = [
            Choice(
                NO_RELATION,
                "The passage does not state any of the other options. This includes "
                "comparisons, opinions, negations, and names that only appear nearby.",
            )
        ]
        for label in labels:
            phrase = label.name.replace("_", " ")
            hint = f" ({label.description})" if label.description else ""
            choices.append(
                Choice(f"f:{label.name}", f"{pair.a.text} {phrase} {pair.b.text}{hint}")
            )
            choices.append(
                Choice(f"r:{label.name}", f"{pair.b.text} {phrase} {pair.a.text}{hint}")
            )
        return Question(
            name=f"pair_{index}",
            instructions=(
                f"Using only this passage, what does it state about {pair.a.text} "
                f"and {pair.b.text}?"
            ),
            choices=choices,
        )

    @classmethod
    def _read_chunk(
        cls,
        client: DecisionsService,
        text: str,
        pairs: Sequence[CandidatePair],
        labels: Sequence[RelationLabel],
    ) -> Tuple[List[Tuple[CandidatePair, str, bool, float]], int]:
        close_old_connections()
        first = client.ask(
            text,
            [cls._choice_question(i, pair, labels) for i, pair in enumerate(pairs)],
            "Knowledge graph relationship read",
        )
        tentative = []
        for index, pair in enumerate(pairs):
            answer = first.answers.get(f"pair_{index}")
            if (
                answer is None
                or not answer.choice
                or answer.choice == NO_RELATION
                or (answer.confidence or 0) < RELATION_CHOICE_THRESHOLD
            ):
                continue
            direction, name = answer.choice.split(":", 1)
            tentative.append((pair, name, direction == "f"))
        if not tentative:
            return [], first.input_tokens
        questions = []
        for index, (pair, name, forward) in enumerate(tentative):
            subject, target = (pair.a, pair.b) if forward else (pair.b, pair.a)
            questions.append(
                Question(
                    name=f"check_{index}",
                    instructions=(
                        f"Does the passage itself explicitly state that {subject.text} "
                        f"{name.replace('_', ' ')} {target.text}? Answer from the "
                        "passage only, not outside knowledge. Comparisons, opinions, "
                        "and negations do not count."
                    ),
                )
            )
        checked = client.ask(text, questions, "Knowledge graph relationship check")
        kept = []
        for index, (pair, name, forward) in enumerate(tentative):
            answer = checked.answers.get(f"check_{index}")
            if answer and (answer.probability or 0) >= RELATION_VERIFY_THRESHOLD:
                kept.append((pair, name, forward, float(answer.probability)))
        return kept, first.input_tokens + checked.input_tokens

    @classmethod
    def build(cls, graph: KnowledgeGraph) -> None:
        """Replace the graph's relations from its current labels."""
        user = graph.user
        client = DecisionsService(user)
        file_ids = list(graph.files.values_list("id", flat=True))
        EntityClusterService.sync_user(user)
        EntityClusterService.adjudicate(user, file_ids, client)
        EntityClusterService.refresh_labels(user)
        plan = cls.plan_pairs(graph)
        labels = list(graph.labels.all())
        by_name = {label.name: label for label in labels}
        rows = {
            row.id: row for row in cls.graph_chunks(graph).filter(id__in=list(plan))
        }
        graph.relations.all().delete()
        KnowledgeGraph.objects.filter(pk=graph.pk).update(
            chunks_total=len(plan), chunks_done=0
        )
        clusters = {
            cluster.pk: cluster for cluster in EntityCluster.objects.filter(user=user)
        }
        done = 0
        tokens = 0
        with ThreadPoolExecutor(max_workers=PARALLEL_REQUESTS) as pool:
            futures = {
                pool.submit(
                    cls._read_chunk, client, rows[chunk_id].text, pairs, labels
                ): chunk_id
                for chunk_id, pairs in plan.items()
                if chunk_id in rows
            }
            for future, chunk_id in futures.items():
                kept, used = future.result()
                tokens += used
                done += 1
                cls._save_chunk_relations(
                    graph, rows[chunk_id], kept, by_name, clusters
                )
                KnowledgeGraph.objects.filter(pk=graph.pk).update(
                    chunks_done=done, tokens_used=tokens
                )

    @staticmethod
    @transaction.atomic
    def _save_chunk_relations(graph, chunk, kept, by_name, clusters) -> None:
        for pair, name, forward, probability in kept:
            subject_id, object_id = (
                (pair.a.cluster_id, pair.b.cluster_id)
                if forward
                else (pair.b.cluster_id, pair.a.cluster_id)
            )
            if subject_id not in clusters or object_id not in clusters:
                continue
            EntityRelation.objects.update_or_create(
                chunk=chunk,
                subject_id=subject_id,
                object_id=object_id,
                label=by_name[name],
                defaults={
                    "graph": graph,
                    "file_id": chunk.file_id,
                    "probability": probability,
                    "evidence": pair.sentence[:600],
                },
            )

    @staticmethod
    def mark_failed(graph_id: int, message: str) -> None:
        KnowledgeGraph.objects.filter(pk=graph_id).update(
            status=GraphStatus.FAILED, error=message[:300]
        )

    @classmethod
    def run_discovery(cls, graph_id: int) -> None:
        graph = KnowledgeGraph.objects.get(pk=graph_id)
        try:
            cls.discover_labels(graph)
        except BackgroundModelUnavailable as error:
            logger.warning("Label discovery failed for graph %s: %s", graph_id, error)
            cls.mark_failed(graph_id, "The background model could not run.")
            return
        KnowledgeGraph.objects.filter(pk=graph_id).update(
            status=GraphStatus.AWAITING_APPROVAL, error=""
        )

    @classmethod
    def run_build(cls, graph_id: int) -> None:
        graph = KnowledgeGraph.objects.get(pk=graph_id)
        try:
            cls.build(graph)
        except DecisionsUnavailable as error:
            logger.warning("Relation build failed for graph %s: %s", graph_id, error)
            cls.mark_failed(graph_id, str(error))
            return
        KnowledgeGraph.objects.filter(pk=graph_id).update(
            status=GraphStatus.READY, error="", built_at=timezone.now()
        )
