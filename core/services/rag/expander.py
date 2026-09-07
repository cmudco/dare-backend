"""Expand stage: follow the document graph one hop from each hit.

Two kinds of hop, in that order of priority: a stored in-document pointer
("see section 7.2"), then an entity shared with another selected file.

Pure. The loaders are injected so this module never imports Django models; the
default loaders in ``build_pipeline`` read ``DocumentReference`` / ``DocumentEntity``.
"""

import logging
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from core.services.rag.dtos import ReferenceHop, RetrievedChunk

logger = logging.getLogger(__name__)

ChunkKey = Tuple[str, int]
HopLoader = Callable[
    [Iterable[ChunkKey], Optional[int]], Dict[ChunkKey, Sequence[object]]
]
EntityHopLoader = Callable[
    [Iterable[ChunkKey], Optional[int], Sequence[int]],
    Dict[ChunkKey, Sequence[object]],
]

DEFAULT_PER_HIT = 2
DEFAULT_MAX_ADDED = 6
UNRANKED_FACTOR = 0.9


class GraphExpander:
    """Adds the targets of each hit's resolved pointers, plus chunks in other
    selected files that share a rare entity, to the candidate pool."""

    def __init__(
        self,
        loader: HopLoader,
        entity_loader: Optional[EntityHopLoader] = None,
        per_hit: int = DEFAULT_PER_HIT,
        max_added: int = DEFAULT_MAX_ADDED,
    ):
        self._loader = loader
        self._entity_loader = entity_loader
        self.per_hit = per_hit
        self.max_added = max_added

    def expand(
        self,
        pool: List[RetrievedChunk],
        reranker_on: bool,
        user_id: Optional[int],
        file_ids: Sequence[int] = (),
    ) -> List[RetrievedChunk]:
        """``user_id`` scopes the loaders' queries to the pool's owner.

        ``file_ids`` are the files the user selected: an entity hop may only
        land in one of them, and there is nothing to cross to until there are
        at least two.
        """
        keys = [
            (chunk.file_id, chunk.chunk_index)
            for chunk in pool
            if chunk.source_type == "document" and chunk.file_id
        ]
        if not keys:
            return []
        present = {(chunk.file_id, chunk.chunk_index) for chunk in pool}
        added: List[RetrievedChunk] = []
        followed: Dict[ChunkKey, int] = {}
        try:
            self._walk(
                pool,
                self._loader(keys, user_id),
                present,
                added,
                followed,
                reranker_on,
                entity=False,
            )
        except Exception as error:
            logger.warning("Graph expand skipped: %s", error, exc_info=True)
            return []

        # Entity hops are the weaker signal, so they only get the room the
        # author's own pointers left behind.
        if (
            self._entity_loader is not None
            and len(set(file_ids)) >= 2
            and len(added) < self.max_added
        ):
            try:
                self._walk(
                    pool,
                    self._entity_loader(keys, user_id, file_ids),
                    present,
                    added,
                    followed,
                    reranker_on,
                    entity=True,
                )
            except Exception as error:
                # The pointer hops already found are still good; keep them.
                logger.warning("Entity expand skipped: %s", error, exc_info=True)
        return added

    def _walk(
        self,
        pool: List[RetrievedChunk],
        hops: Dict[ChunkKey, Sequence[object]],
        present: Set[ChunkKey],
        added: List[RetrievedChunk],
        followed: Dict[ChunkKey, int],
        reranker_on: bool,
        *,
        entity: bool,
    ) -> None:
        """One pass over the pool, seating hop targets within both caps.

        ``present`` and ``followed`` are carried across passes, so a pointer
        hop counts against its hit's ``per_hit`` budget before entity hops
        get a look at it.
        """
        for chunk in pool:
            if len(added) >= self.max_added:
                return
            source = (chunk.file_id, chunk.chunk_index)
            for hop in hops.get(source, []):
                if followed.get(source, 0) >= self.per_hit:
                    break
                if len(added) >= self.max_added:
                    return
                if entity:
                    if not hop.file_id:
                        # No target file to land in; nothing to add for
                        # this hop.
                        continue
                    file_id = str(hop.file_id)
                    file_name = hop.file_name
                    source_ref = hop.file_name
                else:
                    file_id = chunk.file_id
                    file_name = hop.file_name or chunk.file_name
                    source_ref = chunk.source_ref
                target = (file_id, hop.chunk_index)
                if target in present:
                    continue
                present.add(target)
                followed[source] = followed.get(source, 0) + 1
                added.append(
                    RetrievedChunk(
                        text=hop.text,
                        source_ref=source_ref,
                        score=(
                            chunk.score
                            if reranker_on
                            else chunk.score * UNRANKED_FACTOR
                        ),
                        chunk_index=hop.chunk_index,
                        source_type="document",
                        file_id=file_id,
                        file_name=file_name,
                        page_start=hop.page_start,
                        page_end=hop.page_end,
                        section=hop.section or "",
                        # Third-party/custom loaders written against the
                        # original hop contract may not provide the optional
                        # retrieval representation yet. In that case the
                        # source body remains the safe ranking fallback.
                        retrieval_text=getattr(hop, "retrieval_text", ""),
                        via=ReferenceHop(
                            source_chunk_index=chunk.chunk_index,
                            kind="entity" if entity else hop.kind,
                            key=hop.key,
                            raw_text=hop.raw_text,
                            source_file_id=chunk.file_id,
                        ),
                    )
                )
