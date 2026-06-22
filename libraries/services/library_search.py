"""Query selected shared libraries and return structured matches.

A shared library lives in a dedicated, un-scoped container (Pinecone namespace
or Weaviate collection), so queries run near-vector against it with NO user
filter. Results are blended into the RAG context alongside the user's own
documents and saved as citation snippets, tagged with their source so archival
material is never mistaken for the user's own.
"""

import logging
from typing import Dict, List

from libraries.models import SharedLibrary
from libraries.services.library_store import LibraryVectorStore

logger = logging.getLogger(__name__)


def _match_field(match, key: str, default=None):
    """Read a field from a Pinecone (object) or Weaviate (dict) match."""
    if isinstance(match, dict):
        return match.get(key, default)
    return getattr(match, key, default)


def search_libraries(
    query_vector: List[float],
    library_ids: List[int],
    top_k: int = 10,
    similarity_threshold: float = 0.0,
) -> List[Dict]:
    """Search each selected library's container and return structured matches.

    Returns a list of dicts, each: ``{library, text, source_ref, score,
    chunk_index}`` — ready to both render as context and persist as a snippet.
    """
    if not library_ids:
        return []

    libraries = SharedLibrary.active_objects.filter(
        id__in=library_ids, is_available=True
    )

    results: List[Dict] = []
    for library in libraries:
        store = LibraryVectorStore(library)
        try:
            matches = store.query(query_vector, top_k=top_k)
        except Exception as exc:
            logger.warning("Library search failed for %s: %s", library.slug, exc)
            continue
        finally:
            store.close()

        for match in matches or []:
            score = _match_field(match, "score", 0.0) or 0.0
            if score < similarity_threshold:
                continue
            metadata: Dict = _match_field(match, "metadata", {}) or {}
            text = metadata.get("text", "")
            if not text:
                continue
            results.append(
                {
                    "library": library,
                    "text": text,
                    "source_ref": (
                        metadata.get("source_ref")
                        or metadata.get("title")
                        or library.name
                    ),
                    "score": float(score),
                    "chunk_index": int(metadata.get("chunk_index") or 0),
                }
            )

    return results
