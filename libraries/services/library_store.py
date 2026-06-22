"""Backend-agnostic read/write for a single shared library's corpus.

Routes by the library's declared backend:
  * Pinecone -> a dedicated namespace in the shared index.
  * Weaviate -> a dedicated collection.

Open once, call ``close()`` when done (Weaviate holds a live connection).
"""

from typing import Dict, List, Tuple

from core.helpers.pinecone import PineconeClient
from libraries.constants import VectorBackend


class LibraryVectorStore:
    def __init__(self, library):
        self.library = library
        self.backend = library.backend
        self._weaviate = None
        if self.backend == VectorBackend.WEAVIATE:
            from libraries.services.weaviate_library_client import \
                WeaviateLibraryClient

            self._weaviate = WeaviateLibraryClient()
        else:
            self._pinecone = PineconeClient()

    def close(self):
        if self._weaviate is not None:
            self._weaviate.close()

    def clear(self):
        """Drop the library's existing corpus so a re-import is a clean replace."""
        if self.backend == VectorBackend.WEAVIATE:
            self._weaviate.delete_collection(self.library.weaviate_class)
        else:
            try:
                self._pinecone.delete_namespace(self.library.namespace)
            except Exception:
                pass

    def upsert(self, items: List[Tuple[str, List[float], Dict]]):
        if self.backend == VectorBackend.WEAVIATE:
            self._weaviate.upsert(self.library.weaviate_class, items)
        else:
            self._pinecone.upsert_vectors(items, namespace=self.library.namespace)

    def query(self, vector: List[float], top_k: int = 10) -> List[Dict]:
        if self.backend == VectorBackend.WEAVIATE:
            return self._weaviate.query(self.library.weaviate_class, vector, top_k)
        return self._pinecone.query_vectors(
            vector=vector, top_k=top_k, namespace=self.library.namespace, filter=None
        )
