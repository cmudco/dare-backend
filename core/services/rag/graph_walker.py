"""Graph walk: Personalized PageRank over chunks and entities.

Score starts at the chunks search found and the entities the question names,
spreads along shared entities, stated relationships and in-document pointers,
and collects in connected chunks. An entity linked to many chunks passes little
to each, so rare links count for more without any extra rule.

Pure: the graph comes from an injected loader, so nothing here touches the ORM.
"""

import logging
import math
import time
from dataclasses import dataclass, field, replace
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

import networkx as nx

from core.services.rag.dtos import ReferenceHop, RetrievedChunk, WalkStep

logger = logging.getLogger(__name__)

ChunkKey = Tuple[str, int]
Node = Tuple[str, object]

DAMPING = 0.5
CHUNK_SEED_FACTOR = 0.05
RELATION_WEIGHT = 3.0
POINTER_WEIGHT = 2.0
MAX_HOPS = 8
PATH_SOURCE_CHUNKS = 3
MAX_PATH_NODES = 7


@dataclass(frozen=True)
class WalkEntityLink:
    chunk: ChunkKey
    entity_id: int
    text: str
    mentions: int = 1


@dataclass(frozen=True)
class WalkPointer:
    source: ChunkKey
    target: ChunkKey
    text: str


@dataclass(frozen=True)
class WalkRelation:
    subject: int
    object: int
    label: str
    probability: float


@dataclass(frozen=True)
class WalkGraph:
    links: Sequence[WalkEntityLink] = ()
    pointers: Sequence[WalkPointer] = ()
    relations: Sequence[WalkRelation] = ()
    entity_names: Dict[int, str] = field(default_factory=dict)


@dataclass(frozen=True)
class WalkChunk:
    key: ChunkKey
    text: str
    file_name: str
    page_start: Optional[int]
    page_end: Optional[int]
    section: str
    retrieval_text: str


@dataclass(frozen=True)
class WalkResult:
    """``pool`` is the search pool with linked chunks marked; ``hops`` holds the rest."""

    pool: List[RetrievedChunk]
    hops: List[RetrievedChunk]
    nodes: int = 0
    edges: int = 0
    milliseconds: int = 0
    query_entities: Dict[str, str] = field(default_factory=dict)
    error: str = ""


GraphLoader = Callable[[Optional[int], Sequence[int]], WalkGraph]
EntityResolver = Callable[[Optional[int], Iterable[str], WalkGraph], Dict[str, int]]
ChunkLoader = Callable[[Iterable[ChunkKey], Optional[int]], Dict[ChunkKey, WalkChunk]]


def _chunk_node(key: ChunkKey) -> Node:
    return ("chunk", key)


def _entity_node(entity_id: int) -> Node:
    return ("entity", entity_id)


def build_graph(graph: WalkGraph) -> nx.Graph:
    network = nx.Graph()
    for link in graph.links:
        weight = 1 + math.log(max(link.mentions, 1))
        network.add_edge(
            _chunk_node(link.chunk),
            _entity_node(link.entity_id),
            weight=weight,
            kind="mention",
            text=link.text,
        )
    for pointer in graph.pointers:
        if pointer.source != pointer.target:
            network.add_edge(
                _chunk_node(pointer.source),
                _chunk_node(pointer.target),
                weight=POINTER_WEIGHT,
                kind="pointer",
                text=pointer.text,
            )
    for relation in graph.relations:
        if relation.subject == relation.object:
            continue
        network.add_edge(
            _entity_node(relation.subject),
            _entity_node(relation.object),
            weight=RELATION_WEIGHT * relation.probability,
            kind="relation",
            text=relation.label,
            subject=relation.subject,
        )
    return network


def seed_weights(
    pool: Sequence[RetrievedChunk], entity_ids: Iterable[int], network: nx.Graph
) -> Dict[Node, float]:
    seeds: Dict[Node, float] = {}
    top = max((chunk.score for chunk in pool), default=0.0) or 1.0
    for chunk in pool:
        node = _chunk_node((chunk.file_id, chunk.chunk_index))
        if node in network:
            seeds[node] = max(
                seeds.get(node, 0.0), CHUNK_SEED_FACTOR * chunk.score / top
            )
    for entity_id in entity_ids:
        node = _entity_node(entity_id)
        if node in network:
            seeds[node] = 1.0 / max(network.degree(node), 1)
    return seeds


class GraphWalker:
    def __init__(
        self,
        graph_loader: GraphLoader,
        entity_resolver: EntityResolver,
        chunk_loader: ChunkLoader,
        max_hops: int = MAX_HOPS,
    ):
        self._load_graph = graph_loader
        self._resolve = entity_resolver
        self._load_chunks = chunk_loader
        self.max_hops = max_hops

    def walk(
        self,
        pool: List[RetrievedChunk],
        user_id: Optional[int],
        file_ids: Sequence[int],
        query_entities: Sequence[str],
    ) -> WalkResult:
        started = time.monotonic()
        graph = self._load_graph(user_id, file_ids)
        network = build_graph(graph)
        resolved = self._resolve(user_id, query_entities, graph)
        names = {
            name: graph.entity_names.get(entity, name)
            for name, entity in resolved.items()
        }
        seeds = seed_weights(pool, resolved.values(), network)
        if not seeds:
            return WalkResult(pool=pool, hops=[], query_entities=names)
        try:
            scores = nx.pagerank(
                network,
                alpha=DAMPING,
                personalization=seeds,
                weight="weight",
            )
        except nx.PowerIterationFailedConvergence as error:
            logger.warning("Graph walk did not converge: %s", error)
            return WalkResult(
                pool=pool,
                hops=[],
                query_entities=names,
                error="The graph walk did not converge",
            )
        marked, hops = self._rank(pool, user_id, network, graph, scores, resolved)
        return WalkResult(
            pool=marked,
            hops=hops,
            nodes=network.number_of_nodes(),
            edges=network.number_of_edges(),
            milliseconds=int((time.monotonic() - started) * 1000),
            query_entities=names,
        )

    def _rank(self, pool, user_id, network, graph, scores, resolved):
        by_key = {(c.file_id, c.chunk_index): c for c in pool}
        sources = {_entity_node(entity) for entity in resolved.values()}
        sources = {node for node in sources if node in network}
        anchors = [
            _chunk_node((c.file_id, c.chunk_index))
            for c in pool[:PATH_SOURCE_CHUNKS]
            if _chunk_node((c.file_id, c.chunk_index)) in network
        ]
        sources.update(anchors)
        if not sources:
            return pool, []
        for _, _, data in network.edges(data=True):
            data["cost"] = 1.0 / max(data["weight"], 1e-6)
        _, paths = nx.multi_source_dijkstra(network, sources, weight="cost")
        ranked = sorted(
            (node for node in scores if node[0] == "chunk"),
            key=lambda node: -scores[node],
        )
        anchor_set = set(anchors)
        chosen: List[Tuple[ChunkKey, List[Node], float]] = []
        for node in ranked:
            path = paths.get(node)
            if (
                node in anchor_set
                or path is None
                or not 2 < len(path) <= MAX_PATH_NODES
            ):
                continue
            chosen.append((node[1], path, scores[node]))
            if len(chosen) >= self.max_hops:
                break
        missing = list(
            {
                node[1]
                for _, path, _ in chosen
                for node in path
                if node[0] == "chunk" and node[1] not in by_key
            }
        )
        loaded = self._load_chunks(missing, user_id) if missing else {}
        marked = list(pool)
        positions = {(c.file_id, c.chunk_index): i for i, c in enumerate(pool)}
        hops: List[RetrievedChunk] = []
        anchor_score = pool[0].score if pool else 0.0
        for key, path, score in chosen:
            via = self._hop(path, network, graph, by_key, loaded)
            existing = by_key.get(key)
            if existing is not None:
                marked[positions[key]] = replace(existing, via=via, graph_score=score)
                continue
            row = loaded.get(key)
            if row is None:
                continue
            hops.append(
                RetrievedChunk(
                    text=row.text,
                    source_ref=row.file_name,
                    score=anchor_score,
                    chunk_index=key[1],
                    source_type="document",
                    file_id=key[0],
                    file_name=row.file_name,
                    page_start=row.page_start,
                    page_end=row.page_end,
                    section=row.section,
                    retrieval_text=row.retrieval_text,
                    via=via,
                    graph_score=score,
                )
            )
        return marked, hops

    def _hop(self, path, network, graph, by_key, loaded) -> ReferenceHop:
        steps = self._steps(path, network, graph, by_key, loaded)
        origin = path[0]
        bridge = next(
            (
                graph.entity_names.get(node[1], "")
                for node in path[1:-1]
                if node[0] == "entity"
            ),
            "",
        )
        source_file, source_index = origin[1] if origin[0] == "chunk" else ("", 0)
        return ReferenceHop(
            source_chunk_index=source_index,
            kind="walk",
            key=bridge.lower(),
            raw_text=bridge,
            source_file_id=source_file,
            path=tuple(steps),
        )

    @staticmethod
    def _steps(path, network, graph, by_key, loaded) -> List[WalkStep]:
        steps: List[WalkStep] = []
        for index, node in enumerate(path):
            relation = ""
            reverse = False
            mentioned_as = ""
            if index > 0:
                edge = network.edges[path[index - 1], node]
                if edge["kind"] == "relation":
                    relation = edge["text"]
                    reverse = edge["subject"] != path[index - 1][1]
                elif edge["kind"] == "pointer":
                    relation = "points_to"
                elif node[0] == "entity":
                    mentioned_as = edge["text"]
            if node[0] == "entity":
                name = graph.entity_names.get(node[1], "")
                steps.append(
                    WalkStep(
                        kind="entity",
                        label=name,
                        entity_id=node[1],
                        relation=relation,
                        relation_reversed=reverse,
                        mentioned_as=(
                            mentioned_as if mentioned_as.lower() != name.lower() else ""
                        ),
                    )
                )
                continue
            row = by_key.get(node[1]) or loaded.get(node[1])
            file_name = getattr(row, "file_name", "") or ""
            steps.append(
                WalkStep(
                    kind="chunk",
                    label=file_name,
                    page=getattr(row, "page_start", None),
                    relation=relation,
                    file_id=node[1][0],
                    chunk_index=node[1][1],
                )
            )
        return steps
