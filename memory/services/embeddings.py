"""Embeddings for memory rows and retrieval queries.

One short sentence in, 512 floats out. The conversation is never embedded —
only the extracted statement (``"{key} {text}"``, at write time) and, at query
time, the question. 512 dimensions rather than the default 1536: the model is
trained so a truncated vector still works, and 512 keeps ~97% of retrieval
quality for a third of the storage.

Configured separately from document embeddings (``MEMORY_EMBEDDING_*``):
memory's vectors live in a fixed ``vector(512)`` column, so a vector of any
other length is dropped rather than written.

Failure mode is ``None``s, never an exception — a missing embedding degrades
ranking to the lexical and recency signals, which is worse; an exception here
would take the writer job (or worse, a conversation) down, which is
unacceptable.
"""

import logging
from typing import List, Optional

from config import env
from core.helpers.embeddings import EmbeddingClient
from memory.constants import EMBED_DIMS

logger = logging.getLogger(__name__)

_client: Optional[EmbeddingClient] = None


def _get_client() -> Optional[EmbeddingClient]:
    global _client
    if _client is None:
        if not (env.MEMORY_EMBEDDING_API_KEY or env.MEMORY_EMBEDDING_BASE_URL):
            return None
        _client = EmbeddingClient(
            base_url=env.MEMORY_EMBEDDING_BASE_URL,
            api_key=env.MEMORY_EMBEDDING_API_KEY,
            model=env.MEMORY_EMBEDDING_MODEL,
            dimensions=env.MEMORY_EMBEDDING_DIMENSIONS,
            send_dimensions=env.MEMORY_EMBEDDING_SEND_DIMENSIONS,
        )
    return _client


def embed_texts(texts: List[str]) -> List[Optional[List[float]]]:
    """Embed several strings in one request, positionally.

    The returned list lines up with ``texts`` position for position — callers
    zip it against a parallel list of records. The blanks are filtered before
    the API call and the results scattered back into place; a version that
    ``filter(bool)``-ed and re-mapped positionally once shifted every embedding
    after an empty string onto the WRONG memory, and nothing looked broken.
    """
    keep: List[int] = []
    cleaned: List[str] = []
    for index, text in enumerate(texts):
        trimmed = (text or "").strip()
        if trimmed:
            keep.append(index)
            cleaned.append(trimmed)

    results: List[Optional[List[float]]] = [None] * len(texts)
    if not cleaned:
        return results

    client = _get_client()
    if client is None:
        logger.warning("[memory] embeddings skipped: no embedding endpoint configured")
        return results

    try:
        vectors = client.create_batch_embeddings(cleaned)
    except Exception as exc:
        logger.warning(
            "[memory] embedding call failed (%d texts): %s", len(cleaned), exc
        )
        return results

    for position, vector in zip(keep, vectors):
        if len(vector) != EMBED_DIMS:
            logger.warning(
                "[memory] dropped a %d-dim embedding; the column holds %d",
                len(vector),
                EMBED_DIMS,
            )
            continue
        results[position] = vector
    return results


def embed_one(text: str) -> Optional[List[float]]:
    return embed_texts([text])[0]
