from abc import ABC, abstractmethod
from typing import List, Tuple, Dict, Optional
from django.conf import settings
import logging

logger = logging.getLogger(__name__)

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
        logger.info("Initializing PineconeVectorService")
        self.client = PineconeClient()

    def upsert_vectors(
        self,
        vectors: List[Tuple[str, List[float], Dict]],
        namespace: Optional[str] = None
    ) -> bool:
        logger.info(f"PineconeVectorService.upsert_vectors called with {len(vectors)} vectors and namespace={namespace}")
        return self.client.upsert_vectors(vectors, namespace)

    def query_vectors(
        self,
        vector: List[float],
        top_k: int = 5,
        namespace: Optional[str] = None,
        filter: Optional[Dict] = None
    ) -> List[Dict]:
        logger.info(f"PineconeVectorService.query_vectors called with namespace={namespace}")
        return self.client.query_vectors(vector, top_k, namespace, filter)

    def delete_vectors(
        self,
        ids: List[str],
        namespace: Optional[str] = None
    ) -> bool:
        logger.info(f"PineconeVectorService.delete_vectors called with {len(ids)} ids")
        return self.client.delete_vectors(ids, namespace)

    def delete_namespace(self, namespace: str) -> bool:
        logger.info(f"PineconeVectorService.delete_namespace called with namespace={namespace}")
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


def get_vector_service() -> BaseVectorService:
    """Factory function to get the appropriate vector service based on settings."""

    # Define as string, not tuple with parentheses
    vector_db = 'WEAVIATE'  # For testing purposes

    print(f"[vector_service] Initializing vector service of type: {vector_db}")
    logger.info(f"Initializing vector service for: {vector_db}")

    try:
        if vector_db == 'WEAVIATE':
            print("[vector_service] Creating WeaviateVectorService instance")
            return WeaviateVectorService()
        elif vector_db == 'PINECONE':
            print("[vector_service] Creating PineconeVectorService instance")
            service = PineconeVectorService()
            print(f"[vector_service] PineconeVectorService created: {service.__class__.__name__}")
            return service
        else:
            error_msg = f"Unsupported vector database: {vector_db}"
            print(f"[vector_service] ERROR: {error_msg}")
            raise ValueError(error_msg)
    except Exception as e:
        print(f"[vector_service] ERROR creating vector service: {str(e)}")
        logger.exception(f"Error creating vector service: {str(e)}")
        raise