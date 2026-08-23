from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Tuple, Union

from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib.auth import get_user_model

from core.config.processing import VECTOR_DIMENSION
from core.config.vector_db import get_user_namespace
from core.helpers.pinecone import PineconeClient
from core.helpers.weaviate import WeaviateClient
from users.constants import VectorDBChoice

User = get_user_model()


def client_operation(func):
    """Decorator for standardized error handling in vector client operations."""

    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            operation_name = func.__name__
            raise Exception(f"Error in {operation_name}: {str(e)}")

    return wrapper


class BaseVectorClient(ABC):
    """Abstract base class for vector database clients."""

    @abstractmethod
    def upsert_vectors(
        self,
        vectors: List[Tuple[str, List[float], Dict]],
        namespace: Optional[str] = None,
    ) -> bool:
        pass

    @abstractmethod
    def query_vectors(
        self,
        vector: List[float],
        top_k: int = 5,
        namespace: Optional[str] = None,
        filter: Optional[Dict] = None,
        query_text: str = "",
    ) -> List[Dict]:
        pass

    @abstractmethod
    def delete_vectors(self, ids: List[str], namespace: Optional[str] = None) -> bool:
        pass

    @abstractmethod
    def delete_namespace(self, namespace: str) -> bool:
        pass


class BaseVectorService(ABC):
    """Abstract base class for vector database services."""

    def close(self) -> None:
        """Release an optional underlying client transport."""
        close = getattr(getattr(self, "client", None), "close", None)
        if callable(close):
            close()

    @abstractmethod
    def upsert_vectors(
        self,
        vectors: List[Tuple[str, List[float], Dict]],
        namespace: Optional[str] = None,
    ) -> bool:
        """Upsert vectors to the vector database."""
        pass

    @abstractmethod
    def query_vectors(
        self,
        vector: List[float],
        top_k: int = 5,
        namespace: Optional[str] = None,
        filter: Optional[Dict] = None,
        query_text: str = "",
    ) -> List[Dict]:
        """Query similar vectors from the vector database."""
        pass

    @abstractmethod
    def delete_vectors(self, ids: List[str], namespace: Optional[str] = None) -> bool:
        """Delete vectors by their IDs."""
        pass

    @abstractmethod
    def delete_namespace(self, namespace: str) -> bool:
        """Delete an entire namespace."""
        pass

    def search_documents(
        self,
        vector: List[float],
        user_id: int,
        file_ids: List[int],
        top_k: int = 10,
        query_text: str = "",
    ) -> List[Dict]:
        """
        Search for documents similar to the given vector.
        This provides a higher-level interface that works with document concepts.

        Passing ``query_text`` enables hybrid (BM25 + dense) retrieval on
        backends that support it (Weaviate); Pinecone stays dense-only.
        """
        filter_query = {
            "user_id": str(user_id),
            "file_id": {"$in": [str(file_id) for file_id in file_ids]},
        }

        return self.query_vectors(
            vector=vector,
            top_k=top_k,
            namespace=get_user_namespace(user_id),
            filter=filter_query,
            query_text=query_text,
        )

    def delete_file_vectors(self, file_id: int, user_id: int) -> bool:
        """
        Delete all vectors related to a specific file.
        This provides a more domain-specific interface for deleting file vectors.
        """
        filter_query = {"user_id": str(user_id), "file_id": {"$in": [str(file_id)]}}

        dummy_vector = [0] * VECTOR_DIMENSION

        results = self.query_vectors(
            vector=dummy_vector,
            top_k=1000,
            namespace=get_user_namespace(user_id),
            filter=filter_query,
        )

        if results:
            vector_ids = [match["id"] for match in results]
            return self.delete_vectors(
                ids=vector_ids, namespace=get_user_namespace(user_id)
            )

        return True


class PineconeVectorService(BaseVectorService):
    """Pinecone implementation of the vector service."""

    def __init__(self):
        self.client = PineconeClient()

    @client_operation
    def upsert_vectors(
        self,
        vectors: List[Tuple[str, List[float], Dict]],
        namespace: Optional[str] = None,
    ) -> bool:
        return self.client.upsert_vectors(vectors, namespace)

    def query_vectors(
        self,
        vector: List[float],
        top_k: int = 5,
        namespace: Optional[str] = None,
        filter: Optional[Dict] = None,
        query_text: str = "",
    ) -> List[Dict]:
        # Pinecone path is dense-only here; query_text is accepted but unused.
        return self.client.query_vectors(vector, top_k, namespace, filter)

    def delete_vectors(self, ids: List[str], namespace: Optional[str] = None) -> bool:
        return self.client.delete_vectors(ids, namespace)

    def delete_namespace(self, namespace: str) -> bool:
        return self.client.delete_namespace(namespace)


class WeaviateVectorService(BaseVectorService):
    """Weaviate implementation of the vector service."""

    def __init__(self):
        self.client = WeaviateClient()

    @client_operation
    def upsert_vectors(
        self,
        vectors: List[Tuple[str, List[float], Dict]],
        namespace: Optional[str] = None,
    ) -> bool:
        return self.client.upsert_vectors(vectors, namespace)

    @client_operation
    def query_vectors(
        self,
        vector: List[float],
        top_k: int = 5,
        namespace: Optional[str] = None,
        filter: Optional[Dict] = None,
        query_text: str = "",
    ) -> List[Dict]:
        return self.client.query_vectors(
            vector, top_k, namespace, filter, query_text=query_text
        )

    @client_operation
    def delete_vectors(self, ids: List[str], namespace: Optional[str] = None) -> bool:
        return self.client.delete_vectors(ids, namespace)

    @client_operation
    def delete_namespace(self, namespace: str) -> bool:
        return self.client.delete_namespace(namespace)


def get_vector_service(user_id: Optional[int] = None) -> BaseVectorService:
    """
    Factory function to get the appropriate vector service based on user preference.
    """
    if user_id is None:
        return WeaviateVectorService()

    try:
        user = User.objects.get(id=user_id)

        if user.vector_db == VectorDBChoice.PINECONE:
            return PineconeVectorService()

        return WeaviateVectorService()
    except Exception as e:
        return WeaviateVectorService()


async def get_vector_service_async(user_id: Optional[int] = None) -> BaseVectorService:
    """Async version with connection testing."""
    if user_id is None:
        return WeaviateVectorService()

    try:

        user_data = await sync_to_async(lambda: User.objects.get(id=user_id))()

        if user_data.vector_db == VectorDBChoice.PINECONE:
            try:
                service = PineconeVectorService()
                return service
            except Exception as e:
                return WeaviateVectorService()

        return WeaviateVectorService()
    except Exception as e:
        return WeaviateVectorService()
