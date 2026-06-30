"""Retrieve stage (audit mistakes #5/#6) — hybrid BM25 + dense + RRF.

Each retriever owns its own embedding and vector-store access, so it is
"powerful enough to call its respective database queries" rather than leaning on
the orchestrator. An abstract base with per-source implementations and a factory,
mirroring the ``vector_service`` reference pattern (rules.md §2/§10).
"""

from abc import ABC, abstractmethod
from typing import List, Optional, Tuple

from core.helpers.openai import OpenAIWrapper
from core.services.rag.dtos import RetrievalRequest, RetrievedChunk
from libraries.services.library_search import search_libraries


class BaseRetriever(ABC):
    """Embeds the query and runs hybrid retrieval against one source."""

    def __init__(self, openai_client: Optional[OpenAIWrapper] = None):
        self.openai_client = openai_client or OpenAIWrapper()

    def embed(self, text: str) -> List[float]:
        return self.openai_client.create_embeddings(text)

    @abstractmethod
    def search(
        self,
        request: RetrievalRequest,
        query_vector: List[float],
        query_text: str,
        want_vectors: bool,
    ) -> List[RetrievedChunk]:
        """Hybrid search; ``query_text`` drives BM25, ``query_vector`` the dense leg."""


class LibraryRetriever(BaseRetriever):
    """Shared-library corpora (dedicated, un-scoped Weaviate collections)."""

    def search(
        self,
        request: RetrievalRequest,
        query_vector: List[float],
        query_text: str,
        want_vectors: bool,
    ) -> List[RetrievedChunk]:
        matches = search_libraries(
            query_vector,
            list(request.library_ids),
            top_k=request.top_k,
            similarity_threshold=request.similarity_threshold,
            query_text=query_text,
            include_vector=want_vectors,
        )
        return [
            RetrievedChunk(
                text=m["text"],
                source_ref=m["source_ref"],
                score=m["score"],
                chunk_index=m.get("chunk_index", 0),
                source_type="library",
                library=m.get("library"),
                vector=m.get("vector"),
            )
            for m in matches
        ]


_RETRIEVERS = {"library": LibraryRetriever}


def get_retriever(
    source_type: str = "library", openai_client: Optional[OpenAIWrapper] = None
) -> BaseRetriever:
    """Factory: pick the retriever for a source type (rules.md §2)."""
    retriever_cls = _RETRIEVERS.get(source_type, LibraryRetriever)
    return retriever_cls(openai_client=openai_client)
