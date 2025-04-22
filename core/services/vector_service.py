from abc import ABC, abstractmethod
from typing import List, Tuple, Dict, Optional

from core.helpers import weaviate

class BaseVectorService(ABC):
    """Abstract base class for vector database services."""

    @abstractmethod
    def upsert_vectors(
        self,
        vectors: List[Tuple[str, List[float], Dict]],
        namespace: Optional[str] = None
    ) -> bool:
        """Upsert vectors to the vector database."""
        pass

    @abstractmethod
    def query_vectors(
        self,
        vector: List[float],
        top_k: int = 5,
        namespace: Optional[str] = None,
        filter: Optional[Dict] = None
    ) -> List[Dict]:
        """Query similar vectors from the vector database."""
        pass

    @abstractmethod
    def delete_vectors(
        self,
        ids: List[str],
        namespace: Optional[str] = None
    ) -> bool:
        """Delete vectors by their IDs."""
        pass

    @abstractmethod
    def delete_namespace(self, namespace: str) -> bool:
        """Delete an entire namespace."""
        pass


class PineconeVectorService(BaseVectorService):
    """Pinecone implementation of the vector service."""

    def __init__(self):
        from core.helpers.pinecone import PineconeClient
        self.client = PineconeClient()

    def upsert_vectors(
        self,
        vectors: List[Tuple[str, List[float], Dict]],
        namespace: Optional[str] = None
    ) -> bool:
        return self.client.upsert_vectors(vectors, namespace)

    def query_vectors(
        self,
        vector: List[float],
        top_k: int = 5,
        namespace: Optional[str] = None,
        filter: Optional[Dict] = None
    ) -> List[Dict]:
        return self.client.query_vectors(vector, top_k, namespace, filter)

    def delete_vectors(
        self,
        ids: List[str],
        namespace: Optional[str] = None
    ) -> bool:
        return self.client.delete_vectors(ids, namespace)

    def delete_namespace(self, namespace: str) -> bool:
        return self.client.delete_namespace(namespace)


class WeaviateVectorService(BaseVectorService):
    """Weaviate implementation of the vector service."""

    def __init__(self):
        from core.helpers.weaviate import WeaviateClient
        self.client = WeaviateClient()

    def upsert_vectors(
        self,
        vectors: List[Tuple[str, List[float], Dict]],
        namespace: Optional[str] = None
    ) -> bool:
        try:
            for vector_id, embedding, metadata in vectors:
                doc_id = metadata.get('file_id')
                user_id = metadata.get('user_id')
                weaviate_metadata = {
                    'title': metadata.get('file_name', ''),
                    'content': metadata.get('text', ''),
                    'user_id': user_id,
                    'file_id': int(doc_id)
                }
                self.client.upsert_document(
                    doc_id=doc_id,
                    vector=embedding,
                    metadata=weaviate_metadata,
                    user_id=user_id
                )
            return True
        except Exception as e:
            raise Exception(f"Error upserting vectors to Weaviate: {str(e)}")

    def query_vectors(
        self,
        vector: List[float],
        top_k: int = 5,
        namespace: Optional[str] = None,
        filter: Optional[Dict] = None
    ) -> List[Dict]:
        if not filter or not filter.get('user_id') or not filter.get('file_id'):
            raise ValueError("User ID and file IDs are required for Weaviate query")

        user_id = filter['user_id']
        file_ids = filter['file_id'].get('$in', [])

        # Query documents
        results = self.client.query_documents(vector=vector, user_id=user_id, top_k=top_k)

        # Format results to match Pinecone's structure
        formatted_results = []
        for result in results:
            metadata = result.get('metadata', {})
            formatted_results.append({
                'id': f"file_{result['id']}_chunk_0",  # Mimic Pinecone's vector ID format
                'score': result.get('score', 0.0),
                'metadata': {
                    'file_id': result['id'],
                    'user_id': metadata.get('user_id'),
                    'file_name': metadata.get('title'),
                    'text': metadata.get('content'),
                    'chunk_index': 0  # Weaviate doesn't store chunk_index explicitly
                }
            })
        return formatted_results

    def delete_vectors(
        self,
        ids: List[str],
        namespace: Optional[str] = None
    ) -> bool:
        try:
            if not namespace:
                raise ValueError("Namespace (user_id) is required for Weaviate deletion")
            user_id = namespace.replace("user_", "")
            for vector_id in ids:
                # Extract file_id from vector_id (format: file_{file_id}_chunk_{index})
                try:
                    file_id = vector_id.split('_')[1]
                    self.client.delete_document(doc_id=file_id, user_id=user_id)
                except IndexError:
                    continue
            return True
        except Exception as e:
            raise Exception(f"Error deleting vectors from Weaviate: {str(e)}")

    def delete_namespace(self, namespace: str) -> bool:
        try:
            user_id = namespace.replace("user_", "")
            collection = self.client.client.collections.get(self.client.collection_name)
            user_filter = weaviate.classes.query.Filter.by_property("user_id").equal(user_id)
            collection.data.delete_many(where=user_filter)
            return True
        except Exception as e:
            raise Exception(f"Error deleting namespace from Weaviate: {str(e)}")


def get_vector_service() -> BaseVectorService:
    """Factory function to get the appropriate vector service based on settings."""
    from django.conf import settings
    vector_db = getattr(settings, 'VECTOR_DB')
    print(f"Using vector database: {vector_db}")

    if vector_db == 'WEAVIATE':
        return WeaviateVectorService()
    elif vector_db == 'PINECONE':
        return PineconeVectorService()
    else:
        raise ValueError(f"Unsupported vector database: {vector_db}")