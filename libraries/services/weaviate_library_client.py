"""Weaviate client for shared-library corpora.

Unlike the per-user ``Document`` client (core/helpers/weaviate.py), this hosts a
library as its OWN dedicated collection with external vectors and queries it with
NO user filter — the "dedicated, un-scoped collection" shape. Kept separate so
the per-user document path is untouched.
"""

import uuid
from typing import Dict, List, Tuple

import weaviate
from django.conf import settings
from weaviate.classes.config import Configure, DataType, Property
from weaviate.classes.query import HybridFusion, MetadataQuery

# The library envelope, declared as a typed Weaviate schema.
LIBRARY_PROPERTIES = [
    ("text", DataType.TEXT),
    ("title", DataType.TEXT),
    ("source_ref", DataType.TEXT),
    ("library_id", DataType.TEXT),
    ("library_slug", DataType.TEXT),
    ("library_name", DataType.TEXT),
    ("pdf_file", DataType.TEXT),
    ("pdf_stem", DataType.TEXT),
    ("page", DataType.INT),
    ("chunk_index", DataType.INT),
    ("total_chunks", DataType.INT),
    ("transcription_id", DataType.INT),
    ("original_id", DataType.TEXT),
]
_PROPERTY_NAMES = {name for name, _ in LIBRARY_PROPERTIES}


class WeaviateLibraryClient:
    """Manages one library's dedicated collection. Open once, ``close()`` when done."""

    def __init__(self):
        self.client = weaviate.connect_to_local(
            host=settings.WEAVIATE.get("HOST", "localhost"),
            port=settings.WEAVIATE.get("PORT", 8080),
            skip_init_checks=settings.WEAVIATE.get("SKIP_INIT_CHECKS", True),
        )

    def close(self):
        if getattr(self, "client", None):
            self.client.close()

    def ensure_collection(self, name: str):
        if not self.client.collections.exists(name):
            self.client.collections.create(
                name=name,
                vectorizer_config=Configure.Vectorizer.none(),
                properties=[
                    Property(name=n, data_type=t) for n, t in LIBRARY_PROPERTIES
                ],
            )

    def delete_collection(self, name: str):
        if self.client.collections.exists(name):
            self.client.collections.delete(name)

    def upsert(self, collection_name: str, items: List[Tuple[str, List[float], Dict]]):
        """Insert (id, vector, metadata) tuples into the collection."""
        self.ensure_collection(collection_name)
        collection = self.client.collections.get(collection_name)
        with collection.batch.dynamic() as batch:
            for vector_id, vector, metadata in items:
                weaviate_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, vector_id))
                props = {k: v for k, v in metadata.items() if k in _PROPERTY_NAMES}
                batch.add_object(properties=props, vector=vector, uuid=weaviate_uuid)

    def query(
        self,
        collection_name: str,
        vector: List[float],
        top_k: int = 10,
        query_text: str = "",
        include_vector: bool = False,
    ) -> List[Dict]:
        """Search with NO user filter. Returns [{score, metadata, vector?}].

        When ``query_text`` is given, run a HYBRID search (BM25 keyword + dense
        vector, fused with Reciprocal Rank Fusion) so exact terms — names,
        certificate/case numbers, place names — are matched alongside meaning.
        Falls back to pure near-vector when no text is supplied. Pass
        ``include_vector`` to also return each object's embedding (needed for MMR).
        """
        if not self.client.collections.exists(collection_name):
            return []
        collection = self.client.collections.get(collection_name)

        def _vec(obj):
            if not include_vector:
                return None
            v = getattr(obj, "vector", None)
            return v.get("default") if isinstance(v, dict) else v

        results = []
        if query_text:
            response = collection.query.hybrid(
                query=query_text,
                vector=vector,
                alpha=0.5,  # balanced keyword/vector blend
                limit=top_k,
                fusion_type=HybridFusion.RANKED,  # Reciprocal Rank Fusion
                return_metadata=MetadataQuery(score=True),
                include_vector=include_vector,
            )
            for obj in response.objects:
                score = getattr(obj.metadata, "score", 0.0) or 0.0
                results.append(
                    {
                        "score": score,
                        "metadata": dict(obj.properties),
                        "vector": _vec(obj),
                    }
                )
        else:
            response = collection.query.near_vector(
                near_vector=vector,
                limit=top_k,
                return_metadata=MetadataQuery(distance=True),
                include_vector=include_vector,
            )
            for obj in response.objects:
                distance = getattr(obj.metadata, "distance", 1.0)
                results.append(
                    {
                        "score": 1.0 - distance,
                        "metadata": dict(obj.properties),
                        "vector": _vec(obj),
                    }
                )
        return results
