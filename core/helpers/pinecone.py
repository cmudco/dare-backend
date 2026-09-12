import math
from typing import Dict, List, Optional, Tuple

from django.conf import settings
from pinecone import Pinecone

from config.env import PINECONE_API_KEY, PINECONE_INDEX_NAME


class PineconeClient:
    def __init__(self):
        self.pc = Pinecone(api_key=PINECONE_API_KEY)
        self.index = self.pc.Index(PINECONE_INDEX_NAME)

    def upsert_vectors(
        self,
        vectors: List[Tuple[str, List[float], Dict]],
        namespace: Optional[str] = None,
    ) -> bool:
        """Upsert vectors to Pinecone."""
        try:
            formatted_vectors = [
                (id, vector, metadata) for id, vector, metadata in vectors
            ]

            self.index.upsert(vectors=formatted_vectors, namespace=namespace)
            return True
        except Exception as e:
            raise Exception(f"Error upserting vectors: {str(e)}")

    def read_generation(self, generation, user_id, logical_file_id):
        namespace = f"user_{user_id}"
        objects = []
        for ids in self.index.list(namespace=namespace):
            response = self.index.fetch(ids=ids, namespace=namespace)
            for vector_id, record in response.vectors.items():
                metadata = dict(record.metadata or {})
                if metadata.get("file_id") != str(generation):
                    continue
                index = metadata.get("chunk_index")
                if (
                    isinstance(index, bool)
                    or not isinstance(index, (int, float))
                    or not math.isfinite(index)
                    or int(index) != index
                ):
                    raise ValueError("Stored Pinecone chunk index is invalid")
                metadata["chunk_index"] = int(index)
                expected_id = f"file_{logical_file_id}_chunk_{int(index)}:{generation}"
                if vector_id != expected_id:
                    raise ValueError("Stored Pinecone object identity mismatch")
                objects.append({"metadata": metadata, "vector": list(record.values)})
        return objects

    def delete_vectors(self, ids: List[str], namespace: Optional[str] = None) -> bool:
        """Delete vectors by their IDs."""
        try:
            self.index.delete(ids=ids, namespace=namespace)
            return True
        except Exception as e:
            raise Exception(f"Error deleting vectors: {str(e)}")

    def delete_file_vectors(
        self,
        file_id: int,
        user_id: int,
        namespace: Optional[str] = None,
    ) -> bool:
        """Delete every vector for one owned file without a result-count cap."""
        try:
            self.index.delete(
                filter={
                    "file_id": {"$eq": str(file_id)},
                    "user_id": {"$eq": str(user_id)},
                },
                namespace=namespace,
            )
            return True
        except Exception as e:
            raise Exception(f"Error deleting file vectors: {str(e)}")

    def query_vectors(
        self,
        vector: List[float],
        top_k: int = 5,
        namespace: Optional[str] = None,
        filter: Optional[Dict] = None,
        include_vector: bool = False,
    ) -> List[Dict]:
        """Query similar vectors from Pinecone."""
        try:
            results = self.index.query(
                vector=vector,
                top_k=top_k,
                namespace=namespace,
                filter=filter,
                include_metadata=True,
                include_values=include_vector,
            )
            return [
                {
                    "id": match.id,
                    "score": match.score,
                    "metadata": match.metadata,
                    "vector": list(match.values) if include_vector else None,
                }
                for match in results.matches
            ]
        except Exception as e:
            raise Exception(f"Error querying vectors: {str(e)}")

    def delete_namespace(self, namespace: str) -> bool:
        """Delete an entire namespace."""
        try:
            self.index.delete(delete_all=True, namespace=namespace)
            return True
        except Exception as e:
            raise Exception(f"Error deleting namespace: {str(e)}")
