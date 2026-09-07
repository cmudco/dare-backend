"""Persistence and read models for the document map.

Owns every ORM touch for chunks and references: the ingest write, the
cleanup on delete or refresh, the lookups the retrieval pipeline needs, and
the payload the Map tab renders. Pure stages in ``core/services/rag`` never
import this module; they receive loaders instead.
"""

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from django.db import transaction
from django.db.models import Count, Exists, OuterRef

from core.config.document_parsing import HEADING_LABELS
from core.config.entities import (
    BOILERPLATE_MIN_FILES,
    BOILERPLATE_SHARE,
    NON_LINKING_KINDS,
)
from core.services.document_parsers.headings import infer_flat_chapter_hierarchy
from core.services.rag.entity_extractor import EntityMention
from core.services.rag.reference_resolver import ResolvedReference
from core.services.rag.structured_chunker import StructuredChunk, retrieval_text_for
from files.models import DocumentChunk, DocumentEntity, DocumentReference, File

PREVIEW_CHARS = 160
MAX_ENTITIES_SHOWN = 12
# How many entity-hop candidates load_entity_hops offers per hit. The
# expander walks them in order and takes the first not already in its
# retrieval pool, so a target already present costs the hit nothing as long
# as a later candidate lands somewhere new.
ENTITY_HOP_CANDIDATES = 3
ChunkKey = Tuple[str, int]


@dataclass(frozen=True)
class ChunkStructure:
    page_start: Optional[int]
    page_end: Optional[int]
    section: str


@dataclass(frozen=True)
class LoadedHop:
    """A resolved outgoing reference with the target chunk's content."""

    kind: str
    key: str
    raw_text: str
    chunk_index: int
    text: str
    page_start: Optional[int]
    page_end: Optional[int]
    section: str
    file_name: str
    entity_kind: str = ""
    file_id: str = ""
    retrieval_text: str = ""


class DocumentMapService:
    @staticmethod
    def replace(
        file: File,
        chunks: Sequence[Tuple[int, StructuredChunk]],
        references: Sequence[ResolvedReference],
    ) -> Tuple[int, int]:
        """Rebuild the file's chunk and reference rows; returns (found, resolved).

        ``chunks`` pairs each chunk with its vector-store index, which is the
        position the embedding service assigned, so a skipped embedding never
        shifts the map off the vectors.
        """
        with transaction.atomic():
            DocumentChunk.objects.filter(file=file).delete()
            rows = DocumentChunk.objects.bulk_create(
                [
                    DocumentChunk(
                        file=file,
                        chunk_index=index,
                        text=chunk.text,
                        element_kind=chunk.element_kind,
                        page_start=chunk.page_start,
                        page_end=chunk.page_end,
                        section_order=chunk.section_order,
                        section=(chunk.section or "")[:512],
                        heading_path=list(chunk.heading_path),
                        order_start=chunk.order_start,
                        order_end=chunk.order_end,
                    )
                    for index, chunk in chunks
                ]
            )
            by_index = {row.chunk_index: row for row in rows}
            edges: List[DocumentReference] = []
            seen = set()
            for reference in references:
                source = by_index.get(reference.source_chunk_index)
                dedupe_key = (
                    reference.source_chunk_index,
                    reference.kind,
                    reference.key,
                )
                if source is None or dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                target = (
                    by_index.get(reference.target_chunk_index)
                    if reference.target_chunk_index is not None
                    else None
                )
                edges.append(
                    DocumentReference(
                        file=file,
                        source_chunk=source,
                        kind=reference.kind,
                        key=reference.key[:64],
                        raw_text=reference.raw_text[:200],
                        target_order=reference.target_order,
                        target_chunk=target,
                    )
                )
            DocumentReference.objects.bulk_create(edges)
        resolved = sum(1 for edge in edges if edge.resolved)
        return len(edges), resolved

    @staticmethod
    def clear(file_id: int) -> None:
        DocumentChunk.objects.filter(file_id=file_id).delete()

    @staticmethod
    def replace_entities(
        file: File,
        chunks: Sequence[Tuple[int, StructuredChunk]],
        mentions_per_chunk: Sequence[Sequence[EntityMention]],
    ) -> int:
        """Rebuild the file's entity rows; returns how many were written."""
        rows_by_index = {
            row.chunk_index: row for row in DocumentChunk.objects.filter(file=file)
        }
        entities: List[DocumentEntity] = []
        for (index, _), mentions in zip(chunks, mentions_per_chunk):
            chunk_row = rows_by_index.get(index)
            if chunk_row is None:
                continue
            seen = set()
            for mention in mentions:
                slot = (mention.kind, mention.key)
                if slot in seen:
                    continue
                seen.add(slot)
                entities.append(
                    DocumentEntity(
                        file=file,
                        chunk=chunk_row,
                        kind=mention.kind[:32],
                        text=mention.text[:200],
                        key=mention.key[:200],
                        mentions=max(int(mention.mentions), 1),
                        confidence=float(mention.confidence),
                    )
                )
        with transaction.atomic():
            DocumentEntity.objects.filter(file=file).delete()
            DocumentEntity.objects.bulk_create(entities)
        return len(entities)

    @staticmethod
    def write_chunk_indexes(
        file: File, chunks: Sequence[Tuple[int, StructuredChunk]]
    ) -> None:
        """Stamp each stored element with the chunk that covers it.

        Also clears ``chunk_index`` from elements no longer covered by any
        chunk, so a re-ingest that drops or reshuffles chunks does not leave
        a stale pointer at an index that may now mean something else.
        """
        model = dict(file.document_model or {})
        elements = model.get("elements") or []
        if not elements:
            return
        index_by_order: Dict[int, int] = {}
        for index, chunk in chunks:
            if chunk.order_start is None or chunk.order_end is None:
                continue
            for order in range(chunk.order_start, chunk.order_end + 1):
                index_by_order.setdefault(order, index)
        changed = False
        for element in elements:
            index = index_by_order.get(element.get("order"))
            if index is not None:
                if element.get("chunk_index") != index:
                    element["chunk_index"] = index
                    changed = True
            elif "chunk_index" in element:
                del element["chunk_index"]
                changed = True
        if changed:
            File.active_objects.filter(pk=file.pk).update(document_model=model)
            file.document_model = model

    @staticmethod
    def load_structure(
        keys: Iterable[ChunkKey], user_id: Optional[int]
    ) -> Dict[ChunkKey, ChunkStructure]:
        """Structure for the given chunks, restricted to ``user_id``'s files."""
        wanted = {(str(file_id), int(index)) for file_id, index in keys}
        if not wanted:
            return {}
        rows = DocumentChunk.objects.filter(
            file__user_id=user_id,
            file_id__in={int(file_id) for file_id, _ in wanted},
            chunk_index__in={index for _, index in wanted},
        ).only("file_id", "chunk_index", "page_start", "page_end", "section")
        found = {}
        for row in rows:
            key = (str(row.file_id), row.chunk_index)
            if key in wanted:
                found[key] = ChunkStructure(row.page_start, row.page_end, row.section)
        return found

    @staticmethod
    def load_hops(
        keys: Iterable[ChunkKey], user_id: Optional[int]
    ) -> Dict[ChunkKey, List[LoadedHop]]:
        """Resolved outgoing hops, restricted to ``user_id``'s files."""
        wanted = {(str(file_id), int(index)) for file_id, index in keys}
        if not wanted:
            return {}
        rows = (
            DocumentReference.objects.filter(
                source_chunk__file__user_id=user_id,
                source_chunk__file_id__in={int(file_id) for file_id, _ in wanted},
                source_chunk__chunk_index__in={index for _, index in wanted},
                target_chunk__isnull=False,
            )
            .select_related("source_chunk", "target_chunk", "file")
            .order_by("id")
        )
        hops: Dict[ChunkKey, List[LoadedHop]] = defaultdict(list)
        for row in rows:
            key = (str(row.file_id), row.source_chunk.chunk_index)
            if key not in wanted:
                continue
            target = row.target_chunk
            hops[key].append(
                LoadedHop(
                    kind=row.kind,
                    key=row.key,
                    raw_text=row.raw_text,
                    chunk_index=target.chunk_index,
                    text=target.text,
                    page_start=target.page_start,
                    page_end=target.page_end,
                    section=target.section,
                    file_name=row.file.name or "",
                    retrieval_text=retrieval_text_for(
                        target.text, tuple(target.heading_path or ())
                    ),
                )
            )
        return dict(hops)

    @staticmethod
    def load_entity_hops(
        keys: Iterable[ChunkKey], user_id: Optional[int], file_ids: Sequence[int]
    ) -> Dict[ChunkKey, List[LoadedHop]]:
        """Up to ``ENTITY_HOP_CANDIDATES`` hops per hit: chunks in other
        selected files that share one of the hit's entities, best first.

        The expander walks a hit's candidates in order and takes the first
        one not already in its retrieval pool. Offering only one candidate
        meant a hit whose single best target was already retrieved lost its
        hop entirely, spending the small hop budget on weaker hits further
        down the pool instead. Ranking several lets the expander skip a
        present target without the hit going empty.

        Rarity and boilerplate are measured over the user's whole indexed
        file set, not just the files selected for this request: scoring
        purely against the selection means every entity shared by a
        two-file selection sits in "all" of it, so no key could ever look
        rare enough (or common enough to be boilerplate) and a small
        selection could never link at all. A link still requires the
        entity to occur in at least two of the *selected* files, so an
        entity that is common in the wider library but happens not to
        repeat within this particular selection does not link either.
        """
        scope = {int(file_id) for file_id in file_ids}
        wanted = {(str(file_id), int(index)) for file_id, index in keys}
        if len(scope) < 2 or not wanted:
            return {}
        hit_rows = list(
            DocumentEntity.objects.filter(
                file__user_id=user_id,
                file_id__in=scope,
                chunk__chunk_index__in={index for _, index in wanted},
            )
            .exclude(kind__in=NON_LINKING_KINDS)
            .values("file_id", "chunk__chunk_index", "kind", "key", "text", "mentions")
        )
        hit_rows = [
            r
            for r in hit_rows
            if (str(r["file_id"]), r["chunk__chunk_index"]) in wanted
        ]
        if not hit_rows:
            return {}

        n_user = (
            File.active_objects.filter(user_id=user_id)
            .filter(Exists(DocumentEntity.objects.filter(file=OuterRef("pk"))))
            .count()
        )

        slots = {(r["kind"], r["key"]) for r in hit_rows}
        slot_keys = {k for _, k in slots}
        df_scope: Dict[Tuple[str, str], int] = {}
        for row in (
            DocumentEntity.objects.filter(
                file__user_id=user_id, file_id__in=scope, key__in=slot_keys
            )
            .values("kind", "key")
            .annotate(files=Count("file", distinct=True))
        ):
            if (row["kind"], row["key"]) in slots:
                df_scope[(row["kind"], row["key"])] = row["files"]
        df_user: Dict[Tuple[str, str], int] = {}
        for row in (
            DocumentEntity.objects.filter(
                file__user_id=user_id,
                file__is_deleted=False,
                file__is_active=True,
                key__in=slot_keys,
            )
            .values("kind", "key")
            .annotate(files=Count("file", distinct=True))
        ):
            if (row["kind"], row["key"]) in slots:
                df_user[(row["kind"], row["key"])] = row["files"]

        def weight(slot: Tuple[str, str]) -> Optional[float]:
            in_scope = df_scope.get(slot, 0)
            # The entity has to repeat in at least one other selected file
            # than the hit's own for the hop to have somewhere to land.
            if in_scope < 2:
                return None
            in_library = df_user.get(slot, in_scope)
            if (
                n_user >= BOILERPLATE_MIN_FILES
                and in_library / n_user > BOILERPLATE_SHARE
            ):
                return None
            return math.log(n_user / in_library)

        by_hit: Dict[ChunkKey, List[dict]] = defaultdict(list)
        for row in hit_rows:
            by_hit[(str(row["file_id"]), row["chunk__chunk_index"])].append(row)

        # Every eligible slot of every hit, ranked weight-descending then
        # mentions-descending — not just each hit's top slot — so a hit can
        # fall back to its second- or third-best entity when the best one's
        # target turns out to already be in the pool.
        ranked_by_hit: Dict[ChunkKey, List[dict]] = {}
        eligible_keys: Set[str] = set()
        for hit_key, rows in by_hit.items():
            scored = []
            for r in rows:
                w = weight((r["kind"], r["key"]))
                if w is not None:
                    scored.append((w, r["mentions"], r))
            scored.sort(key=lambda item: (-item[0], -item[1]))
            if scored:
                ranked_by_hit[hit_key] = [item[2] for item in scored]
                eligible_keys.update(item[2]["key"] for item in scored)
        if not ranked_by_hit:
            return {}

        targets_by_slot: Dict[Tuple[str, str], List[DocumentEntity]] = defaultdict(list)
        for target in DocumentEntity.objects.filter(
            file__user_id=user_id, file_id__in=scope, key__in=eligible_keys
        ).select_related("chunk", "file"):
            targets_by_slot[(target.kind, target.key)].append(target)

        hops: Dict[ChunkKey, List[LoadedHop]] = {}
        for hit_key, ranked_rows in ranked_by_hit.items():
            candidates: List[LoadedHop] = []
            used_chunks: Set[ChunkKey] = set()
            used_files: Set[int] = set()
            for row in ranked_rows:
                if len(candidates) >= ENTITY_HOP_CANDIDATES:
                    break
                remaining = [
                    t
                    for t in targets_by_slot.get((row["kind"], row["key"]), [])
                    if t.file_id != row["file_id"]
                    and (str(t.file_id), t.chunk.chunk_index) not in used_chunks
                ]
                while remaining and len(candidates) < ENTITY_HOP_CANDIDATES:
                    best = min(
                        remaining,
                        key=lambda t: (
                            t.file_id in used_files,
                            -t.mentions,
                            t.chunk.chunk_index,
                        ),
                    )
                    candidates.append(
                        LoadedHop(
                            kind="entity",
                            key=row["key"],
                            raw_text=row["text"],
                            chunk_index=best.chunk.chunk_index,
                            text=best.chunk.text,
                            page_start=best.chunk.page_start,
                            page_end=best.chunk.page_end,
                            section=best.chunk.section,
                            file_name=best.file.name or "",
                            entity_kind=row["kind"],
                            file_id=str(best.file_id),
                            retrieval_text=retrieval_text_for(
                                best.chunk.text, tuple(best.chunk.heading_path or ())
                            ),
                        )
                    )
                    used_chunks.add((str(best.file_id), best.chunk.chunk_index))
                    used_files.add(best.file_id)
                    remaining.remove(best)
            if candidates:
                hops[hit_key] = candidates
        return hops

    @classmethod
    def build_map(cls, file: File) -> Dict:
        """The Map tab payload: section tree, chunks, references, counts."""
        elements = (file.document_model or {}).get("elements") or []
        chunk_rows = list(
            DocumentChunk.objects.filter(file=file).order_by("chunk_index")
        )
        reference_rows = list(
            DocumentReference.objects.filter(file=file)
            .select_related("source_chunk", "target_chunk")
            .order_by("id")
        )
        entity_rows = list(
            DocumentEntity.objects.filter(file=file)
            .values("chunk__chunk_index", "kind", "key", "text", "mentions")
            .order_by("chunk__chunk_index", "-mentions")
        )
        keys = {row["key"] for row in entity_rows}
        elsewhere = {
            (row["kind"], row["key"]): row["files"]
            for row in DocumentEntity.objects.filter(
                file__user_id=file.user_id,
                file__is_deleted=False,
                file__is_active=True,
                key__in=keys,
            )
            .exclude(file=file)
            .exclude(kind__in=NON_LINKING_KINDS)
            .values("kind", "key")
            .annotate(files=Count("file", distinct=True))
        }
        slots = {(row["kind"], row["key"]) for row in entity_rows}
        entities_by_chunk: Dict[int, List[Dict]] = defaultdict(list)
        for row in entity_rows:
            entities_by_chunk[row["chunk__chunk_index"]].append(
                {
                    "kind": row["kind"],
                    "text": row["text"],
                    "key": row["key"],
                    "mentions": row["mentions"],
                    "other_documents": elsewhere.get((row["kind"], row["key"]), 0),
                }
            )
        for index, items in entities_by_chunk.items():
            items.sort(key=lambda e: (-e["other_documents"], -e["mentions"], e["text"]))
            del items[MAX_ENTITIES_SHOWN:]

        chunk_count_by_section: Dict[int, int] = defaultdict(int)
        for row in chunk_rows:
            if row.section_order is not None:
                chunk_count_by_section[row.section_order] += 1

        heading_rows = [
            (
                int(element["order"]),
                element.get("text", ""),
                int(element.get("level") if element.get("level") is not None else 1),
                element.get("label", ""),
            )
            for element in elements
            if element.get("label") in HEADING_LABELS and element.get("text")
        ]
        inferred_hierarchy = infer_flat_chapter_hierarchy(heading_rows)

        nodes: Dict[int, Dict] = {}
        for element in elements:
            if element.get("label") not in HEADING_LABELS or not element.get("text"):
                continue
            order = int(element["order"])
            stored_level = int(
                element.get("level") if element.get("level") is not None else 1
            )
            level, parent_order = inferred_hierarchy.get(
                order, (stored_level, element.get("parent_order"))
            )
            nodes[order] = {
                "order": order,
                "level": level,
                "number": element.get("number"),
                "text": element.get("text", ""),
                "page_no": element.get("page_no"),
                "parent_order": parent_order,
                "chunk_count": chunk_count_by_section.get(order, 0),
                "children": [],
            }
        roots: List[Dict] = []
        for order, node in nodes.items():
            parent = (
                nodes.get(node["parent_order"])
                if node["parent_order"] is not None
                else None
            )
            (parent["children"] if parent is not None else roots).append(node)

        chunks = [
            {
                "chunk_index": row.chunk_index,
                "element_kind": row.element_kind,
                "page_start": row.page_start,
                "page_end": row.page_end,
                "section_order": row.section_order,
                "section": row.section,
                "order_start": row.order_start,
                "order_end": row.order_end,
                "preview": " ".join(row.text.split())[:PREVIEW_CHARS],
                "preview_truncated": len(" ".join(row.text.split())) > PREVIEW_CHARS,
                "char_count": len(row.text),
                "word_count": len(row.text.split()),
                "entities": entities_by_chunk.get(row.chunk_index, []),
            }
            for row in chunk_rows
        ]
        references = [
            {
                "id": row.id,
                "source_chunk_index": row.source_chunk.chunk_index,
                "kind": row.kind,
                "key": row.key,
                "raw_text": row.raw_text,
                "target_order": row.target_order,
                "target_chunk_index": (
                    row.target_chunk.chunk_index
                    if row.target_chunk is not None
                    else None
                ),
                "resolved": row.resolved,
            }
            for row in reference_rows
        ]
        return {
            "id": file.id,
            "name": file.name,
            "structured": bool(chunk_rows),
            "sections": roots,
            "chunks": chunks,
            "references": references,
            "counts": {
                "sections": len(nodes),
                "chunks": len(chunks),
                "references": len(references),
                "resolved": sum(1 for row in reference_rows if row.resolved),
                "entities": len(slots),
                "linked_entities": sum(1 for slot in slots if elsewhere.get(slot)),
            },
        }

    @staticmethod
    def build_chunk_detail(file: File, chunk_index: int) -> Dict:
        """Full text for one Map selection, loaded separately from the tree."""
        row = DocumentChunk.objects.get(file=file, chunk_index=chunk_index)
        return {
            "chunk_index": row.chunk_index,
            "text": row.text,
            "char_count": len(row.text),
            "word_count": len(row.text.split()),
        }
