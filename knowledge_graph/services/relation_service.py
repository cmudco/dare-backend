"""Discovers a graph's relationship types, prices the read, and runs it."""

import logging
import re
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Dict, List, Literal, Mapping, Optional, Sequence, Tuple

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
    ESTIMATED_TOKENS_PER_ALIAS_QUESTION,
    ESTIMATED_TOKENS_PER_LABEL,
    ESTIMATED_TOKENS_PER_QUESTION,
    LINKING_EXCLUDED_KINDS,
    MAX_FAILED_CHUNK_SHARE,
    MAX_IMPLIED_PAIRS_PER_CHUNK,
    MAX_PAIRS_PER_CHUNK,
    MAX_PROPOSAL_SENTENCES,
    MAX_PROPOSED_TYPES,
    MAX_REACH_PAIRS_PER_CHUNK,
    MAX_RELATION_LABELS,
    MIN_FAILED_CHUNKS_TO_STOP,
    PROPOSAL_BATCH_SENTENCES,
    RELATION_VERIFY_THRESHOLD,
    STRONG_PAIR_CHUNKS,
    VAGUE_RELATION_TYPES,
    GraphStatus,
    LabelSource,
)
from knowledge_graph.domain.alias_rules import file_owners, generic_keys
from knowledge_graph.domain.relation_pairs import (
    CandidatePair,
    PairMention,
    candidate_pairs,
    implied_pairs,
    opening_speaker,
    reach_pairs,
    stating_text,
    strong_pairs,
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
PAGE_START_CHARS = 400
# Reasoning models spend part of this before they write anything.
DISCOVERY_MAX_TOKENS = 8000
NO_RELATION = "none"
LABEL_SYSTEM = (
    "You maintain the list of relationship types for a knowledge graph. Each turn "
    "you see the current list and new passages. Reply with the changes to make: "
    "'add' a type the passages state and the list lacks, 'rename' one to a clearer "
    "name, or 'remove' one that overlaps another. Types must be short snake_case "
    "verbs for a concrete act that one sentence can prove, such as defeated, "
    "appointed, funded or acquired. Never use a vague type such as influenced, "
    "affected, related_to or involved_in. Never put a specific name in a type. "
    "Describe each type in plain words wide enough for every use of the verb, not "
    "limited to one kind of target. Keep the list small: prefer one general type to "
    "several narrow ones. Favor the acts that recur across the documents and what "
    "the user wants to learn, when they say. Mark a type two_way when 'A type B' "
    "always means 'B type A' too, like married_to or served_with. Reply with no "
    "changes when the list already covers the passages."
)
IMPLIED_RULES = (
    "This passage does not always name its speaker. 'I' and 'my' mean whoever is "
    "giving the statement, shown in the page start above it. Count a fact about the "
    "file's person only if that person is the speaker, or the sentence plainly means "
    "them. If someone else is speaking, this is not a fact about the file's person."
)
CHECK_RULES = (
    "Answer no if someone else did it (a country rather than its leader, or the "
    "reverse), if the second name is only where it happened or only part of what "
    "was affected, if it is only planned or announced, or if the act does not fit "
    "the type's meaning. Answer from the passage only, not outside knowledge. "
    "Comparisons, opinions, and negations do not count."
)


PROPOSAL_SYSTEM = (
    "A knowledge graph has a fixed list of relationship types. These numbered "
    "sentences matched none of them. Name up to five missing types. Each must be a "
    "short snake_case verb for a concrete act that one sentence can prove, such as "
    "funded or endorsed, and must recur across several sentences. Never use a vague "
    "type such as influenced, affected, related_to or involved_in, and never put a "
    "specific name in a type. Ignore sentences that state no act between two named "
    "things. For each type give a plain description and the numbers of the sentences "
    "that state it, and mark it two_way when 'A type B' always means 'B type A' "
    "too, like married_to. Reply with no types if none recur."
)


class ProposedType(BaseModel):
    name: str
    description: str
    two_way: bool
    sentences: List[int]


class ProposedTypes(BaseModel):
    types: List[ProposedType]


class LabelOperation(BaseModel):
    action: Literal["add", "rename", "remove"]
    name: str
    new_name: str
    description: str
    two_way: bool


class LabelUpdate(BaseModel):
    operations: List[LabelOperation]


@dataclass(frozen=True)
class BuildEstimate:
    chunks: int
    pairs: int
    tokens: int
    cost: float
    rate_per_million: float


def spread_sample(
    by_file: Mapping[int, Sequence[DocumentChunk]], size: int
) -> List[DocumentChunk]:
    """Evenly spaced chunks per file, so a type list hears every part of every file."""
    remaining = {file_id: len(rows) for file_id, rows in by_file.items()}
    quota = dict.fromkeys(by_file, 0)
    left = size
    while left > 0 and any(remaining.values()):
        for file_id in by_file:
            if left > 0 and remaining[file_id] > 0:
                quota[file_id] += 1
                remaining[file_id] -= 1
                left -= 1
    sample: List[DocumentChunk] = []
    for file_id, rows in by_file.items():
        count = quota[file_id]
        sample.extend(rows[index * len(rows) // count] for index in range(count))
    return sample


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
            if name in VAGUE_RELATION_TYPES:
                continue
            if name and name not in labels and len(labels) < MAX_RELATION_LABELS:
                labels[name] = RelationLabel.objects.create(
                    graph=graph,
                    name=name,
                    description=operation.description[:200],
                    source=LabelSource.DISCOVERED,
                    two_way=operation.two_way,
                )
            continue
        current = labels.get(name)
        if current is None or current.source == LabelSource.USER:
            continue
        if operation.action == "remove":
            # A suggestion must never quietly delete relationships someone paid for.
            if not current.relations.exists():
                current.delete()
                del labels[name]
            continue
        new_name = clean_label(operation.new_name)
        if new_name and new_name not in labels and new_name not in VAGUE_RELATION_TYPES:
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
        people: Dict[int, List[PairMention]] = defaultdict(list)
        weights: Dict[int, Counter] = defaultdict(Counter)
        spellings: Dict[int, Counter] = defaultdict(Counter)
        rows = list(
            DocumentEntity.objects.filter(
                chunk__in=cls.graph_chunks(graph).values("id")
            )
            .exclude(kind__in=LINKING_EXCLUDED_KINDS)
            .values("chunk_id", "key", "text", "mentions", "file_id", "kind")
        )
        generic = generic_keys(
            (row["key"], row["text"], row["mentions"], row["file_id"]) for row in rows
        )
        rows = [
            row
            for row in rows
            if row["key"] in alias_cluster and row["key"] not in generic
        ]
        in_file: Dict[int, List[Tuple[str, int, int]]] = defaultdict(list)
        for row in rows:
            if row["kind"] == "person":
                in_file[row["file_id"]].append(
                    (row["key"], alias_cluster[row["key"]], row["mentions"])
                )
        owners = {file_id: file_owners(items) for file_id, items in in_file.items()}
        for row in rows:
            cluster_id = alias_cluster[row["key"]]
            if row["kind"] == "person":
                cluster_id = owners[row["file_id"]].get(row["key"], cluster_id)
                people[row["chunk_id"]].append(PairMention(cluster_id, row["text"]))
                weights[row["file_id"]][cluster_id] += row["mentions"]
                spellings[cluster_id][row["text"]] += row["mentions"]
            mentions[row["chunk_id"]].append(PairMention(cluster_id, row["text"]))
        plan: Dict[int, List[CandidatePair]] = {}
        subjects = {
            file_id: PairMention(
                counts.most_common(1)[0][0],
                spellings[counts.most_common(1)[0][0]].most_common(1)[0][0],
            )
            for file_id, counts in weights.items()
        }
        chunk_rows = (
            cls.graph_chunks(graph)
            .filter(id__in=list(mentions))
            .order_by("file_id", "chunk_index")
            .values_list("id", "text", "file_id")
        )
        strong = strong_pairs(
            ([person.cluster_id for person in named] for named in people.values()),
            STRONG_PAIR_CHUNKS,
        )
        speaker: Dict[int, PairMention] = {}
        for chunk_id, text, file_id in chunk_rows:
            items = mentions[chunk_id]
            opened = opening_speaker(text, people[chunk_id])
            if opened is not None:
                speaker[file_id] = opened
            pairs = candidate_pairs(text, items, MAX_PAIRS_PER_CHUNK)
            subject = speaker.get(file_id) or subjects.get(file_id)
            if subject is not None:
                pairs = pairs + implied_pairs(
                    text, subject, items, pairs, MAX_IMPLIED_PAIRS_PER_CHUNK
                )
            pairs = pairs + reach_pairs(
                text, people[chunk_id], strong, pairs, MAX_REACH_PAIRS_PER_CHUNK
            )
            if pairs:
                plan[chunk_id] = pairs
        return plan

    @classmethod
    def page_starts(cls, graph: KnowledgeGraph) -> Dict[Tuple[int, str], str]:
        """The start of each page, which often names who a statement belongs to."""
        starts: Dict[Tuple[int, str], str] = {}
        rows = (
            cls.graph_chunks(graph)
            .order_by("file_id", "chunk_index")
            .values_list("file_id", "section", "text")
        )
        for file_id, section, text in rows:
            starts.setdefault((file_id, section or ""), text[:PAGE_START_CHARS])
        return starts

    @classmethod
    def estimates(
        cls, graph: KnowledgeGraph
    ) -> Tuple[BuildEstimate, Optional[BuildEstimate]]:
        """The price of reading every type, and of only the types not read yet."""
        plan = cls.plan_pairs(graph)
        lengths = dict(
            cls.graph_chunks(graph).filter(id__in=list(plan)).values_list("id", "text")
        )
        file_ids = list(graph.files.values_list("id", flat=True))
        alias_tokens = ESTIMATED_TOKENS_PER_ALIAS_QUESTION * len(
            EntityClusterService.pending_candidates(graph.user, file_ids)
        )
        rate = decisions_price_per_million()
        pairs = sum(len(items) for items in plan.values())

        def priced(label_count: int) -> BuildEstimate:
            per_question = (
                ESTIMATED_TOKENS_PER_QUESTION
                + ESTIMATED_TOKENS_PER_LABEL * max(label_count, 1)
            )
            tokens = alias_tokens + sum(
                2 * len(lengths.get(chunk_id, "")) // CHARS_PER_TOKEN
                + len(items) * per_question
                for chunk_id, items in plan.items()
            )
            return BuildEstimate(
                chunks=len(plan),
                pairs=pairs,
                tokens=tokens,
                cost=tokens * rate / 1_000_000,
                rate_per_million=rate,
            )

        unread = graph.labels.filter(read_at__isnull=True).count()
        read = graph.labels.count() - unread
        return (
            priced(graph.labels.count()),
            priced(unread) if unread and read else None,
        )

    @classmethod
    def estimate(cls, graph: KnowledgeGraph) -> BuildEstimate:
        return cls.estimates(graph)[0]

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
        plan = cls.plan_pairs(graph)
        rows = {
            row.id: row for row in cls.graph_chunks(graph).filter(id__in=list(plan))
        }
        by_file: Dict[int, List[DocumentChunk]] = defaultdict(list)
        for chunk_id in sorted(rows, key=lambda c: rows[c].chunk_index):
            by_file[rows[chunk_id].file_id].append(rows[chunk_id])
        return [row.text for row in spread_sample(by_file, DISCOVERY_SAMPLE_CHUNKS)]

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
                            (
                                f"The user wants to learn: {graph.goal}\n\n"
                                if graph.goal
                                else ""
                            )
                            + f"Current types ({len(current)} of "
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
                "comparisons, opinions, negations, names that only appear nearby, and "
                "acts done by someone else or to something else.",
            )
        ]
        for label in labels:
            phrase = label.name.replace("_", " ")
            hint = f" ({label.description})" if label.description else ""
            choices.append(
                Choice(f"f:{label.name}", f"{pair.a.text} {phrase} {pair.b.text}{hint}")
            )
            if not label.two_way:
                choices.append(
                    Choice(
                        f"r:{label.name}", f"{pair.b.text} {phrase} {pair.a.text}{hint}"
                    )
                )
        return Question(
            name=f"pair_{index}",
            instructions=(
                f"Using only this passage, what does it state about {pair.a.text} "
                f"and {pair.b.text}?" + (f" {IMPLIED_RULES}" if pair.implied else "")
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
    ) -> Tuple[List[Tuple[CandidatePair, str, bool, float]], int, List[str]]:
        close_old_connections()
        first = client.ask(
            text,
            [cls._choice_question(i, pair, labels) for i, pair in enumerate(pairs)],
            "Knowledge graph relationship read",
        )
        tentative = []
        unmatched: List[str] = []
        for index, pair in enumerate(pairs):
            answer = first.answers.get(f"pair_{index}")
            # No confidence cutoff: a dozen types split it, and the check decides.
            if answer is None or not answer.choice or answer.choice == NO_RELATION:
                if answer is not None and answer.choice == NO_RELATION:
                    unmatched.append(pair.sentence)
                continue
            direction, name = answer.choice.split(":", 1)
            tentative.append((pair, name, direction == "f"))
        if not tentative:
            return [], first.input_tokens, unmatched
        meanings = {label.name: label.description for label in labels}
        questions = []
        for index, (pair, name, forward) in enumerate(tentative):
            subject, target = (pair.a, pair.b) if forward else (pair.b, pair.a)
            questions.append(
                Question(
                    name=f"check_{index}",
                    instructions=(
                        f"Does the passage itself explicitly state that {subject.text} "
                        f"{name.replace('_', ' ')} {target.text}? "
                        f"{meanings.get(name, '')} "
                        f"{IMPLIED_RULES if pair.implied else ''} {CHECK_RULES}"
                    ),
                )
            )
        checked = client.ask(text, questions, "Knowledge graph relationship check")
        kept = []
        for index, (pair, name, forward) in enumerate(tentative):
            answer = checked.answers.get(f"check_{index}")
            if answer and (answer.probability or 0) >= RELATION_VERIFY_THRESHOLD:
                kept.append((pair, name, forward, float(answer.probability)))
        return kept, first.input_tokens + checked.input_tokens, unmatched

    @classmethod
    def build(cls, graph: KnowledgeGraph, new_only: bool = False) -> int:
        """Read every chunk, then swap in the new relations. Returns skipped chunks.

        The old relations stay until the read finishes, so a failed rerun never
        costs the graph someone already paid for. ``new_only`` reads just the types
        no build has read yet and keeps every existing relationship.
        """
        user = graph.user
        client = DecisionsService(user)
        file_ids = list(graph.files.values_list("id", flat=True))
        EntityClusterService.sync_user(user)
        EntityClusterService.adjudicate(user, file_ids, client)
        EntityClusterService.refresh_labels(user)
        plan = cls.plan_pairs(graph)
        labels = list(
            graph.labels.filter(read_at__isnull=True)
            if new_only
            else graph.labels.all()
        )
        rows = {
            row.id: row for row in cls.graph_chunks(graph).filter(id__in=list(plan))
        }
        starts = cls.page_starts(graph)

        def reading_text(chunk: DocumentChunk, pairs: Sequence[CandidatePair]) -> str:
            implied = [pair for pair in pairs if pair.implied]
            if not implied:
                return chunk.text
            start = starts.get((chunk.file_id, chunk.section or ""), "")
            first = implied[0]
            ids = [pair.a.cluster_id for pair in implied] + [
                pair.b.cluster_id for pair in implied
            ]
            subject = (
                first.a
                if ids.count(first.a.cluster_id) >= ids.count(first.b.cluster_id)
                else first.b
            )
            return (
                f"The sworn statement this passage belongs to was opened by "
                f"{subject.text}, named earlier in the file, unless the page start "
                f"shows someone else.\n\nStart of this page:\n{start}\n\n"
                f"Passage:\n{chunk.text}"
            )

        KnowledgeGraph.objects.filter(pk=graph.pk).update(
            chunks_total=len(plan), chunks_done=0, tokens_used=0
        )
        allowed_failures = max(
            MIN_FAILED_CHUNKS_TO_STOP, int(len(plan) * MAX_FAILED_CHUNK_SHARE)
        )
        results = []
        unmatched: List[str] = []
        done = tokens = skipped = 0
        with ThreadPoolExecutor(max_workers=PARALLEL_REQUESTS) as pool:
            futures = {
                pool.submit(
                    cls._read_chunk,
                    client,
                    reading_text(rows[chunk_id], pairs),
                    pairs,
                    labels,
                ): chunk_id
                for chunk_id, pairs in plan.items()
                if chunk_id in rows
            }
            for future in as_completed(futures):
                chunk_id = futures[future]
                done += 1
                try:
                    kept, used, none_fit = future.result()
                except Exception as error:
                    skipped += 1
                    logger.warning("Chunk %s could not be read: %s", chunk_id, error)
                    if skipped > allowed_failures:
                        pool.shutdown(wait=False, cancel_futures=True)
                        raise DecisionsUnavailable(str(error)) from error
                    continue
                tokens += used
                unmatched.extend(none_fit)
                results.append((rows[chunk_id], kept))
                KnowledgeGraph.objects.filter(pk=graph.pk).update(
                    chunks_done=done, tokens_used=tokens
                )
        cls._replace_relations(graph, results, labels)
        cls._propose_missing_types(graph, unmatched)
        return skipped

    @classmethod
    def _propose_missing_types(cls, graph, sentences: Sequence[str]) -> None:
        """Ask what the fixed type list missed. The paid read is already safe."""
        unique = list(dict.fromkeys(sentences))
        step = max(len(unique) // MAX_PROPOSAL_SENTENCES, 1)
        sample = unique[::step][:MAX_PROPOSAL_SENTENCES]
        existing = set(graph.labels.values_list("name", flat=True))
        found: Dict[str, Dict] = {}
        service = BackgroundModelService()
        try:
            for start in range(0, len(sample), PROPOSAL_BATCH_SENTENCES):
                batch = sample[start : start + PROPOSAL_BATCH_SENTENCES]
                listing = "\n".join(f"{i}. {text}" for i, text in enumerate(batch))
                result = async_to_sync(service.parse_structured)(
                    user=graph.user,
                    messages=[
                        {"role": "system", "content": PROPOSAL_SYSTEM},
                        {
                            "role": "user",
                            "content": (
                                f"Current types: {', '.join(sorted(existing))}\n\n"
                                f"Sentences:\n{listing}"
                            ),
                        },
                    ],
                    response_model=ProposedTypes,
                    description="Knowledge graph missing types",
                    max_tokens=DISCOVERY_MAX_TOKENS,
                    task=BackgroundTask.GRAPH_RELATIONS,
                )
                for proposed in result.value.types:
                    name = clean_label(proposed.name)
                    cited = [i for i in proposed.sentences if 0 <= i < len(batch)]
                    if not name or not cited or name in existing:
                        continue
                    if name in VAGUE_RELATION_TYPES:
                        continue
                    slot = found.setdefault(
                        name,
                        {
                            "name": name,
                            "description": proposed.description[:200],
                            "two_way": proposed.two_way,
                            "example": batch[cited[0]][:300],
                            "count": 0,
                        },
                    )
                    slot["count"] += len(cited)
        except Exception:
            # Suggestions are a bonus: failing here must not fail a finished read.
            logger.warning("Missing-type proposals failed", exc_info=True)
            return
        ranked = sorted(found.values(), key=lambda item: -item["count"])
        KnowledgeGraph.objects.filter(pk=graph.pk).update(
            proposed_types=ranked[:MAX_PROPOSED_TYPES]
        )

    @classmethod
    @transaction.atomic
    def _replace_relations(cls, graph, results, labels) -> None:
        # Reloaded here: a type removed during the read must not sink the whole swap.
        by_name = {label.name: label for label in graph.labels.all()}
        clusters = set(
            EntityCluster.objects.filter(user=graph.user).values_list("id", flat=True)
        )
        read = [label.pk for label in labels if label.name in by_name]
        graph.relations.filter(label_id__in=read).delete()
        for chunk, kept in results:
            cls._save_chunk_relations(graph, chunk, kept, by_name, clusters)
        RelationLabel.objects.filter(pk__in=read).update(read_at=timezone.now())

    @staticmethod
    def _save_chunk_relations(graph, chunk, kept, by_name, clusters) -> None:
        for pair, name, forward, probability in kept:
            subject_id, object_id = (
                (pair.a.cluster_id, pair.b.cluster_id)
                if forward
                else (pair.b.cluster_id, pair.a.cluster_id)
            )
            if name in by_name and by_name[name].two_way:
                subject_id, object_id = sorted((subject_id, object_id))
            if (
                name not in by_name
                or subject_id not in clusters
                or object_id not in clusters
            ):
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
                    "evidence": stating_text(chunk.text, pair, name)[:600],
                },
            )

    @classmethod
    def run_prepare(cls, graph_id: int) -> None:
        graph = KnowledgeGraph.objects.get(pk=graph_id)
        EntityClusterService.sync_user(graph.user)
        EntityClusterService.refresh_labels(graph.user)
        KnowledgeGraph.objects.filter(pk=graph_id).update(
            status=GraphStatus.READY_FREE, error=""
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
    def run_build(cls, graph_id: int, new_only: bool = False) -> None:
        graph = KnowledgeGraph.objects.get(pk=graph_id)
        try:
            skipped = cls.build(graph, new_only)
        except DecisionsUnavailable as error:
            logger.warning("Relation build failed for graph %s: %s", graph_id, error)
            cls.mark_failed(graph_id, f"{error} Earlier relationships are unchanged.")
            return
        note = (
            f"{skipped} chunk{'' if skipped == 1 else 's'} could not be read."
            if skipped
            else ""
        )
        KnowledgeGraph.objects.filter(pk=graph_id).update(
            status=GraphStatus.READY, error=note, built_at=timezone.now()
        )
