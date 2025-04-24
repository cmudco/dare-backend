from typing import Dict, List, Optional, Tuple
from pinecone import Pinecone
from django.conf import settings
from config.env import PINECONE_API_KEY, PINECONE_INDEX_NAME


class PineconeClient:
    def __init__(self):
        self.pc = Pinecone(api_key=PINECONE_API_KEY)
        self.index = self.pc.Index(PINECONE_INDEX_NAME)

    def upsert_vectors(
        self,
        vectors: List[Tuple[str, List[float], Dict]],
        namespace: Optional[str] = None
    ) -> bool:
        """Upsert vectors to Pinecone."""
        try:
            formatted_vectors = [
                (id, vector, metadata)
                for id, vector, metadata in vectors
            ]

            self.index.upsert(
                vectors=formatted_vectors,
                namespace=namespace
            )
            return True
        except Exception as e:
            raise Exception(f"Error upserting vectors: {str(e)}")

    def delete_vectors(
        self,
        ids: List[str],
        namespace: Optional[str] = None
    ) -> bool:
        """Delete vectors by their IDs."""
        try:
            self.index.delete(ids=ids, namespace=namespace)
            return True
        except Exception as e:
            raise Exception(f"Error deleting vectors: {str(e)}")

    def query_vectors(
        self,
        vector: List[float],
        top_k: int = 5,
        namespace: Optional[str] = None,
        filter: Optional[Dict] = None
    ) -> List[Dict]:
        """Query similar vectors from Pinecone."""
        try:
            results = self.index.query(
                vector=vector,
                top_k=top_k,
                namespace=namespace,
                filter=filter,
                include_metadata=True
            )
            return results.matches
        except Exception as e:
            raise Exception(f"Error querying vectors: {str(e)}")

    def delete_namespace(self, namespace: str) -> bool:
        """Delete an entire namespace."""
        try:
            self.index.delete(delete_all=True, namespace=namespace)
            return True
        except Exception as e:
            raise Exception(f"Error deleting namespace: {str(e)}")