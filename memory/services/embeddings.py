"""Embeddings for memory rows and retrieval queries.

One short sentence in, 512 floats out. The conversation is never embedded —
only the extracted statement (``"{key} {text}"``, at write time) and, at query
time, the question. 512 dimensions rather than the default 1536: the model is
trained so a truncated vector still works, and 512 keeps ~97% of retrieval
quality for a third of the storage.

Deliberately its own client rather than ``core.helpers.openai.OpenAIWrapper``:
that wrapper hardcodes text-embedding-3-large/3072 for the document RAG
namespace and other callers depend on it; memory's vectors must stay 512-dim
or every stored row goes stale.

Failure mode is ``None``s, never an exception — a missing embedding degrades
ranking to the lexical and recency signals, which is worse; an exception here
would take the writer job (or worse, a conversation) down, which is
unacceptable.
"""

import logging
from typing import List, Optional

from openai import OpenAI

from config.env import OPENAI_API_KEY
from memory.constants import EMBED_DIMS, EMBED_MODEL

logger = logging.getLogger(__name__)

_client: Optional[OpenAI] = None


def _get_client() -> Optional[OpenAI]:
    global _client
    if _client is None:
        if not OPENAI_API_KEY:
            return None
        _client = OpenAI(api_key=OPENAI_API_KEY)
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
        logger.warning("[memory] embeddings skipped: no OPENAI_API_KEY configured")
        return results

    try:
        response = client.embeddings.create(
            model=EMBED_MODEL, input=cleaned, dimensions=EMBED_DIMS
        )
    except Exception as exc:
        logger.warning(
            "[memory] embedding call failed (%d texts): %s", len(cleaned), exc
        )
        return results

    for position, item in zip(keep, response.data):
        results[position] = item.embedding
    return results


def embed_one(text: str) -> Optional[List[float]]:
    return embed_texts([text])[0]
