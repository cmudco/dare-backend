import uuid
from typing import Any, Dict, List, Optional, Tuple

import weaviate
from django.conf import settings
from weaviate.classes.config import Configure, DataType, Property


class WeaviateClient:
    def __init__(self):
        self.collection_name = settings.WEAVIATE.get("COLLECTION_NAME", "Document")
        self.client = self._connect_to_weaviate()
        self._create_collection()

    def _connect_to_weaviate(self):
        try:
            client = weaviate.connect_to_local(
                host=settings.WEAVIATE.get("HOST", "localhost"),
                port=settings.WEAVIATE.get("PORT", 8080),
                skip_init_checks=settings.WEAVIATE.get("SKIP_INIT_CHECKS", True),
            )
            return client
        except Exception as e:
            error_details = (
                f"Host: {settings.WEAVIATE.get('HOST', 'localhost')}, "
                f"Port: {settings.WEAVIATE.get('PORT', 8080)}, "
                f"Error: {str(e)}"
            )
            raise ConnectionError(
                f"Could not connect to Weaviate. Details: {error_details}"
            )

    def _close_connection(self):
        if hasattr(self, "client") and self.client:
            self.client.close()

    def _create_collection(self):
        try:
            if not self.client.collections.exists(self.collection_name):
                self.client.collections.create(
                    name=self.collection_name,
                    vectorizer_config=Configure.Vectorizer.none(),
                    properties=[
                        Property(name="title", data_type=DataType.TEXT),
                        Property(name="content", data_type=DataType.TEXT),
                        Property(name="user_id", data_type=DataType.TEXT),
                        Property(name="file_id", data_type=DataType.TEXT),
                        Property(name="chunk_index", data_type=DataType.INT),
                        Property(name="original_id", data_type=DataType.TEXT),
                    ],
                )
        except Exception as e:
            raise

    def upsert_document(
        self, doc_id: str, vector: List[float], metadata: Dict[str, Any], user_id: str
    ) -> bool:
        try:
            collection = self.client.collections.get(self.collection_name)

            properties = {
                "title": metadata.get("title", ""),
                "content": metadata.get("content", ""),
                "user_id": user_id,
                "file_id": metadata.get("file_id", ""),
                "chunk_index": metadata.get("chunk_index", 0),
                "original_id": doc_id,
            }

            weaviate_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, doc_id))

            try:
                existing = collection.query.fetch_objects(
                    filters=weaviate.classes.query.Filter.by_id().equal(weaviate_uuid),
                    limit=1,
                )
                if existing.objects:
                    collection.data.delete_by_id(uuid=weaviate_uuid)
            except Exception as e:
                pass

            collection.data.insert(
                properties=properties, vector=vector, uuid=weaviate_uuid
            )

            return True
        except Exception as e:
            raise

    def query_documents(
        self,
        vector: List[float],
        user_id: str,
        top_k: int = 5,
        query_text: str = "",
    ) -> List[Dict]:
        try:
            collection = self.client.collections.get(self.collection_name)
            user_filter = weaviate.classes.query.Filter.by_property("user_id").equal(
                user_id
            )

            if query_text:
                # HYBRID: BM25 keyword + dense vector, fused with RELATIVE_SCORE so
                # the returned score stays on a 0-1 scale compatible with the
                # downstream cosine-style similarity threshold.
                response = collection.query.hybrid(
                    query=query_text,
                    vector=vector,
                    alpha=0.5,
                    limit=top_k,
                    filters=user_filter,
                    fusion_type=weaviate.classes.query.HybridFusion.RELATIVE_SCORE,
                    return_metadata=weaviate.classes.query.MetadataQuery(score=True),
                )
            else:
                response = collection.query.near_vector(
                    near_vector=vector,
                    limit=top_k,
                    filters=user_filter,
                    return_metadata=weaviate.classes.query.MetadataQuery(distance=True),
                )

            results = []
            for obj in response.objects:
                properties = obj.properties

                file_id = properties.get("file_id", "")
                if file_id is not None:
                    file_id = str(file_id)

                if query_text:
                    cosine_similarity = getattr(obj.metadata, "score", 0.0) or 0.0
                else:
                    distance = getattr(obj.metadata, "distance", 1.0)
                    cosine_similarity = 1.0 - distance

                chunk_index = properties.get("chunk_index", 0)

                results.append(
                    {
                        "id": file_id,
                        "metadata": {
                            "title": properties.get("title"),
                            "content": properties.get("content"),
                            "user_id": properties.get("user_id"),
                            "chunk_index": chunk_index,
                        },
                        "score": cosine_similarity,
                        "raw_similarity": cosine_similarity,
                    }
                )

            return results
        except Exception as e:
            raise

    def delete_document(self, doc_id: str, user_id: str) -> bool:
        try:
            collection = self.client.collections.get(self.collection_name)

            if "_" in doc_id:
                document_filter = weaviate.classes.query.Filter.by_property(
                    "original_id"
                ).equal(doc_id) & weaviate.classes.query.Filter.by_property(
                    "user_id"
                ).equal(
                    user_id
                )
                response = collection.query.fetch_objects(
                    filters=document_filter, limit=1
                )

                if response.objects:
                    weaviate_uuid = response.objects[0].uuid
                    collection.data.delete_by_id(uuid=weaviate_uuid)
                    return True

            document_filter = weaviate.classes.query.Filter.by_property(
                "file_id"
            ).equal(doc_id) & weaviate.classes.query.Filter.by_property(
                "user_id"
            ).equal(
                user_id
            )
            response = collection.query.fetch_objects(
                filters=document_filter, limit=100
            )

            if not response.objects:
                return False

            for obj in response.objects:
                weaviate_uuid = obj.uuid
                collection.data.delete_by_id(uuid=weaviate_uuid)

            return True
        except Exception as e:
            raise

    def upsert_vectors(
        self,
        vectors: List[Tuple[str, List[float], Dict]],
        namespace: Optional[str] = None,
    ) -> bool:
        """Upsert vectors to Weaviate."""
        try:
            if not namespace:
                raise ValueError(
                    "Namespace (user_id) is required for Weaviate upsertion"
                )

            user_id = namespace.replace("user_", "")

            for i, (vector_id, embedding, metadata) in enumerate(vectors):
                chunk_index = 0
                vector_parts = vector_id.split("_")

                if len(vector_parts) >= 4:
                    try:
                        chunk_index = int(vector_parts[3])
                    except (ValueError, IndexError) as e:
                        chunk_index = metadata.get("chunk_index", 0)
                else:
                    chunk_index = metadata.get("chunk_index", 0)

                doc_id = metadata.get("file_id")

                if doc_id is not None:
                    doc_id = str(doc_id)

                weaviate_metadata = {
                    "title": metadata.get("file_name", ""),
                    "content": metadata.get("text", ""),
                    "user_id": user_id,
                    "file_id": doc_id,
                    "chunk_index": chunk_index,
                }

                full_doc_id = f"{doc_id}_{chunk_index}"

                self.upsert_document(
                    doc_id=full_doc_id,
                    vector=embedding,
                    metadata=weaviate_metadata,
                    user_id=user_id,
                )
            return True
        except Exception as e:
            raise Exception(f"Error upserting vectors to Weaviate: {str(e)}")

    def query_vectors(
        self,
        vector: List[float],
        top_k: int = 5,
        namespace: Optional[str] = None,
        filter: Optional[Dict] = None,
        query_text: str = "",
    ) -> List[Dict]:
        """Query similar vectors from Weaviate matching the vector service interface."""
        try:
            if not namespace or not filter or not filter.get("user_id"):
                raise ValueError("Namespace (user_id) is required for Weaviate queries")

            user_id = namespace.replace("user_", "")

            file_ids = []
            if filter and "file_id" in filter and "$in" in filter["file_id"]:
                file_ids = [str(id) for id in filter["file_id"]["$in"]]

            results = self.query_documents(
                vector=vector, user_id=user_id, top_k=top_k, query_text=query_text
            )

            formatted_results = []
            for result in results:
                metadata = result.get("metadata", {})
                file_id = result["id"]
                chunk_index = metadata.get("chunk_index", 0)

                if file_ids and file_id not in file_ids:
                    continue

                formatted_results.append(
                    {
                        "id": f"file_{file_id}_chunk_{chunk_index}",
                        "score": result.get("score", 0.0),
                        "metadata": {
                            "file_id": file_id,
                            "user_id": metadata.get("user_id"),
                            "file_name": metadata.get("title"),
                            "text": metadata.get("content"),
                            "chunk_index": chunk_index,
                        },
                    }
                )

            return formatted_results
        except Exception as e:
            raise Exception(f"Error querying vectors from Weaviate: {str(e)}")

    def delete_vectors(self, ids: List[str], namespace: Optional[str] = None) -> bool:
        """Delete vectors by their IDs matching the vector service interface."""
        try:
            if not namespace:
                raise ValueError(
                    "Namespace (user_id) is required for Weaviate deletion"
                )

            user_id = namespace.replace("user_", "")

            for vector_id in ids:
                try:
                    vector_parts = vector_id.split("_")
                    if len(vector_parts) >= 4:
                        file_id = vector_parts[1]
                        chunk_index = vector_parts[3]
                        doc_id = f"{file_id}_{chunk_index}"
                        self.delete_document(doc_id=doc_id, user_id=user_id)
                    else:
                        file_id = (
                            vector_parts[1] if len(vector_parts) > 1 else vector_id
                        )
                        self.delete_document(doc_id=file_id, user_id=user_id)
                except Exception as e:
                    continue
            return True
        except Exception as e:
            raise Exception(f"Error deleting vectors from Weaviate: {str(e)}")

    def delete_namespace(self, namespace: str) -> bool:
        """Delete an entire namespace matching the vector service interface."""
        try:
            user_id = namespace.replace("user_", "")
            collection = self.client.collections.get(self.collection_name)
            user_filter = weaviate.classes.query.Filter.by_property("user_id").equal(
                user_id
            )
            collection.data.delete_many(where=user_filter)
            return True
        except Exception as e:
            raise Exception(f"Error deleting namespace from Weaviate: {str(e)}")
