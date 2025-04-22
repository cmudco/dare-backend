from typing import Any, Dict, List
import weaviate
from weaviate.classes.config import Configure, Property, DataType
import logging
from django.conf import settings

logger = logging.getLogger(__name__)

class WeaviateClient:
    def __init__(self):
        self.collection_name = settings.WEAVIATE.get('COLLECTION_NAME', 'Document')
        self.client = self._connect_to_weaviate()
        self._create_collection()

    def _connect_to_weaviate(self):
        try:
            client = weaviate.connect_to_local(
                host=settings.WEAVIATE.get('HOST', 'localhost'),
                port=settings.WEAVIATE.get('PORT', 8080),
                skip_init_checks=settings.WEAVIATE.get('SKIP_INIT_CHECKS', True)
            )
            logger.info(f"Connected to Weaviate at {settings.WEAVIATE.get('HOST')}:{settings.WEAVIATE.get('PORT')}")
            return client
        except Exception as e:
            logger.error(f"Failed to connect to Weaviate: {str(e)}")
            raise ConnectionError(f"Could not connect to Weaviate: {str(e)}")

    def _close_connection(self):
        if hasattr(self, 'client') and self.client:
            self.client.close()
            logger.info("Closed Weaviate client connection")

    def _create_collection(self):
        try:
            if not self.client.collections.exists(self.collection_name):
                logger.info(f"Creating Weaviate collection: {self.collection_name}")
                self.client.collections.create(
                    name=self.collection_name,
                    vectorizer_config=Configure.Vectorizer.none(),
                    properties=[
                        Property(name="title", data_type=DataType.TEXT),
                        Property(name="content", data_type=DataType.TEXT),
                        Property(name="user_id", data_type=DataType.TEXT),
                        Property(name="file_id", data_type=DataType.INT),
                    ]
                )
                logger.info("Collection created successfully")
        except Exception as e:
            logger.error(f"Error creating collection: {str(e)}")
            raise

    def upsert_document(self, doc_id: str, vector: List[float], metadata: Dict[str, Any], user_id: str) -> bool:
        try:
            collection = self.client.collections.get(self.collection_name)
            properties = {
                "title": metadata.get("title", ""),
                "content": metadata.get("content", ""),
                "user_id": user_id,
                "file_id": int(doc_id)
            }
            collection.data.insert(properties=properties, vector=vector)
            logger.info(f"Upserted document with file_id {doc_id} for user {user_id}")
            return True
        except Exception as e:
            logger.error(f"Error upserting document {doc_id}: {str(e)}")
            raise

    def query_documents(self, vector: List[float], user_id: str, top_k: int = 5) -> List[Dict]:
        try:
            collection = self.client.collections.get(self.collection_name)
            user_filter = weaviate.classes.query.Filter.by_property("user_id").equal(user_id)
            response = collection.query.near_vector(
                near_vector=vector,
                limit=top_k,
                filters=user_filter,
                return_metadata=weaviate.classes.query.MetadataQuery(distance=True)
            )
            results = []
            for obj in response.objects:
                distance = getattr(obj.metadata, 'distance', 1.0)
                cosine_similarity = 1.0 - distance
                results.append({
                    "id": str(obj.properties.get("file_id")),
                    "metadata": {
                        "title": obj.properties.get("title"),
                        "content": obj.properties.get("content"),
                        "user_id": obj.properties.get("user_id")
                    },
                    "score": cosine_similarity,
                    "raw_similarity": cosine_similarity
                })
            return results
        except Exception as e:
            logger.error(f"Error querying documents: {str(e)}")
            raise

    def delete_document(self, doc_id: str, user_id: str) -> bool:
        try:
            collection = self.client.collections.get(self.collection_name)
            document_filter = (
                weaviate.classes.query.Filter.by_property("file_id").equal(int(doc_id)) &
                weaviate.classes.query.Filter.by_property("user_id").equal(user_id)
            )
            response = collection.query.fetch_objects(filters=document_filter, limit=1)
            if not response.objects:
                logger.warning(f"Document with file_id {doc_id} not found for user {user_id}")
                return False
            weaviate_uuid = response.objects[0].uuid
            collection.data.delete_by_id(uuid=weaviate_uuid)
            logger.info(f"Deleted document with file_id {doc_id} for user {user_id}")
            return True
        except Exception as e:
            logger.error(f"Error deleting document {doc_id}: {str(e)}")
            raise