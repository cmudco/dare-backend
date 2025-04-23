from typing import Any, Dict, List, Tuple, Optional
import weaviate
from weaviate.classes.config import Configure, Property, DataType
import logging
from django.conf import settings
import uuid

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
                        Property(name="file_id", data_type=DataType.TEXT),  # Store as TEXT to avoid numeric conversion
                        Property(name="chunk_index", data_type=DataType.INT),
                        Property(name="original_id", data_type=DataType.TEXT)  # Store the original ID to help with lookups
                    ]
                )
                logger.info("Collection created successfully")
        except Exception as e:
            logger.error(f"Error creating collection: {str(e)}")
            raise

    def upsert_document(self, doc_id: str, vector: List[float], metadata: Dict[str, Any], user_id: str) -> bool:
        try:
            collection = self.client.collections.get(self.collection_name)

            # Log the incoming data
            logger.info(f"Upserting document with doc_id={doc_id}, file_id={metadata.get('file_id')}")

            # Make sure all keys in properties are strings
            properties = {
                "title": metadata.get("title", ""),
                "content": metadata.get("content", ""),
                "user_id": user_id,
                "file_id": metadata.get("file_id", ""),  # Ensure file_id is a string
                "chunk_index": metadata.get("chunk_index", 0),
                "original_id": doc_id  # Store the original ID as a property
            }

            # Generate a valid UUID based on the doc_id
            # This creates a deterministic UUID v5 using a namespace and the doc_id
            weaviate_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, doc_id))
            logger.info(f"Generated Weaviate UUID: {weaviate_uuid} for doc_id={doc_id}")

            # Use a UUID that we can control
            try:
                # If a document with this UUID already exists, delete it first
                existing = collection.query.fetch_objects(
                    filters=weaviate.classes.query.Filter.by_id().equal(weaviate_uuid),
                    limit=1
                )
                if existing.objects:
                    logger.info(f"Document with UUID {weaviate_uuid} already exists, deleting it first")
                    collection.data.delete_by_id(uuid=weaviate_uuid)
            except Exception as e:
                logger.warning(f"Error checking for existing document: {str(e)}")

            # Insert with explicit UUID
            collection.data.insert(
                properties=properties,
                vector=vector,
                uuid=weaviate_uuid
            )

            logger.info(f"Successfully upserted document with UUID={weaviate_uuid}, original_id={doc_id}, file_id={properties['file_id']}")
            return True
        except Exception as e:
            logger.error(f"Error upserting document {doc_id}: {str(e)}")
            raise

    def query_documents(self, vector: List[float], user_id: str, top_k: int = 5) -> List[Dict]:
        try:
            collection = self.client.collections.get(self.collection_name)
            user_filter = weaviate.classes.query.Filter.by_property("user_id").equal(user_id)

            logger.info(f"Querying Weaviate documents for user_id={user_id}, top_k={top_k}")

            response = collection.query.near_vector(
                near_vector=vector,
                limit=top_k,
                filters=user_filter,
                return_metadata=weaviate.classes.query.MetadataQuery(distance=True)
            )

            logger.info(f"Weaviate query returned {len(response.objects)} objects")

            results = []
            for obj in response.objects:
                # Get properties
                properties = obj.properties

                # Extract file_id as a string to avoid any type conversion issues
                file_id = properties.get("file_id")
                if file_id is not None:
                    file_id = str(file_id)  # Ensure it's a string

                # Log the raw data we're working with
                logger.info(f"Processing Weaviate object: uuid={obj.uuid}, properties={properties}")

                # Calculate similarity from distance
                distance = getattr(obj.metadata, 'distance', 1.0)
                cosine_similarity = 1.0 - distance

                chunk_index = properties.get("chunk_index", 0)
                logger.info(f"Extracted file_id={file_id}, chunk_index={chunk_index}")

                results.append({
                    "id": file_id,
                    "metadata": {
                        "title": properties.get("title"),
                        "content": properties.get("content"),
                        "user_id": properties.get("user_id"),
                        "chunk_index": chunk_index
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

            # First, try to find by original_id property if it's a compound ID (file_id_chunk_index)
            if '_' in doc_id:
                logger.info(f"Looking for document with original_id={doc_id}")
                document_filter = (
                    weaviate.classes.query.Filter.by_property("original_id").equal(doc_id) &
                    weaviate.classes.query.Filter.by_property("user_id").equal(user_id)
                )
                response = collection.query.fetch_objects(filters=document_filter, limit=1)

                if response.objects:
                    weaviate_uuid = response.objects[0].uuid
                    logger.info(f"Found document with original_id={doc_id}, uuid={weaviate_uuid}")
                    collection.data.delete_by_id(uuid=weaviate_uuid)
                    logger.info(f"Deleted document with original_id={doc_id}")
                    return True

            # If not found or not a compound ID, try by file_id
            logger.info(f"Looking for document with file_id={doc_id}")
            document_filter = (
                weaviate.classes.query.Filter.by_property("file_id").equal(doc_id) &
                weaviate.classes.query.Filter.by_property("user_id").equal(user_id)
            )
            response = collection.query.fetch_objects(filters=document_filter, limit=100)

            if not response.objects:
                logger.warning(f"No documents found with file_id={doc_id} for user={user_id}")
                return False

            logger.info(f"Found {len(response.objects)} documents with file_id={doc_id}")

            for obj in response.objects:
                weaviate_uuid = obj.uuid
                logger.info(f"Deleting document with uuid={weaviate_uuid}")
                collection.data.delete_by_id(uuid=weaviate_uuid)

            logger.info(f"Deleted all documents with file_id={doc_id} for user={user_id}")
            return True
        except Exception as e:
            logger.error(f"Error deleting document {doc_id}: {str(e)}")
            raise

    # New methods to match the vector service abstraction

    def upsert_vectors(
        self,
        vectors: List[Tuple[str, List[float], Dict]],
        namespace: Optional[str] = None
    ) -> bool:
        """Upsert vectors to Weaviate."""
        try:
            if not namespace:
                raise ValueError("Namespace (user_id) is required for Weaviate upsertion")

            user_id = namespace.replace("user_", "")
            logger.info(f"Upserting {len(vectors)} vectors to Weaviate for user {user_id}")

            for i, (vector_id, embedding, metadata) in enumerate(vectors):
                # Extract chunk index from vector_id (format: file_{file_id}_chunk_{index})
                chunk_index = 0
                vector_parts = vector_id.split('_')

                logger.info(f"Processing vector #{i+1}: vector_id={vector_id}, vector_parts={vector_parts}")

                if len(vector_parts) >= 4:
                    try:
                        chunk_index = int(vector_parts[3])
                        logger.info(f"Extracted chunk_index={chunk_index} from vector_id")
                    except (ValueError, IndexError) as e:
                        logger.warning(f"Failed to extract chunk_index from vector_id: {e}, falling back to metadata")
                        chunk_index = metadata.get('chunk_index', 0)
                else:
                    chunk_index = metadata.get('chunk_index', 0)
                    logger.info(f"Using chunk_index={chunk_index} from metadata")

                doc_id = metadata.get('file_id')
                logger.info(f"Original file_id from metadata: {doc_id}")

                # Make sure we store doc_id as a string to avoid any unintended conversions
                if doc_id is not None:
                    # Ensure doc_id is a string
                    doc_id = str(doc_id)
                    logger.info(f"Converted file_id to string: {doc_id}")

                weaviate_metadata = {
                    'title': metadata.get('file_name', ''),
                    'content': metadata.get('text', ''),
                    'user_id': user_id,
                    'file_id': doc_id,  # Store as string to avoid conversion issues
                    'chunk_index': chunk_index
                }

                logger.info(f"Created Weaviate metadata: {weaviate_metadata}")

                # Use compound ID to store chunk information
                full_doc_id = f"{doc_id}_{chunk_index}"
                logger.info(f"Using full_doc_id for Weaviate: {full_doc_id}")

                self.upsert_document(
                    doc_id=full_doc_id,
                    vector=embedding,
                    metadata=weaviate_metadata,
                    user_id=user_id
                )
            return True
        except Exception as e:
            logger.error(f"Error upserting vectors to Weaviate: {str(e)}")
            raise Exception(f"Error upserting vectors to Weaviate: {str(e)}")

    def query_vectors(
        self,
        vector: List[float],
        top_k: int = 5,
        namespace: Optional[str] = None,
        filter: Optional[Dict] = None
    ) -> List[Dict]:
        """Query similar vectors from Weaviate matching the vector service interface."""
        try:
            if not namespace or not filter or not filter.get('user_id'):
                raise ValueError("Namespace (user_id) is required for Weaviate queries")

            user_id = namespace.replace("user_", "")
            logger.info(f"Query vectors with filter: {filter}")

            # Extract file IDs from filter if available
            file_ids = []
            if filter and 'file_id' in filter and '$in' in filter['file_id']:
                file_ids = [str(id) for id in filter['file_id']['$in']]
                logger.info(f"Filtering by file_ids: {file_ids}")

            # Query documents
            results = self.query_documents(vector=vector, user_id=user_id, top_k=top_k)
            logger.info(f"Raw query returned {len(results)} results")

            # Format results to match Pinecone's structure
            formatted_results = []
            for result in results:
                metadata = result.get("metadata", {})
                file_id = result["id"]
                chunk_index = metadata.get("chunk_index", 0)

                # Skip if we have a file_ids filter and this file_id is not in it
                if file_ids and file_id not in file_ids:
                    logger.info(f"Skipping result for file_id={file_id} as it's not in requested file_ids {file_ids}")
                    continue

                logger.info(f"Adding result: file_id={file_id}, chunk_index={chunk_index}")

                formatted_results.append({
                    'id': f"file_{file_id}_chunk_{chunk_index}",
                    'score': result.get('score', 0.0),
                    'metadata': {
                        'file_id': file_id,
                        'user_id': metadata.get('user_id'),
                        'file_name': metadata.get('title'),
                        'text': metadata.get('content'),
                        'chunk_index': chunk_index
                    }
                })

            logger.info(f"Returning {len(formatted_results)} formatted results")
            return formatted_results
        except Exception as e:
            logger.error(f"Error querying vectors from Weaviate: {str(e)}", exc_info=True)
            raise Exception(f"Error querying vectors from Weaviate: {str(e)}")

    def delete_vectors(
        self,
        ids: List[str],
        namespace: Optional[str] = None
    ) -> bool:
        """Delete vectors by their IDs matching the vector service interface."""
        try:
            if not namespace:
                raise ValueError("Namespace (user_id) is required for Weaviate deletion")

            user_id = namespace.replace("user_", "")

            for vector_id in ids:
                # Extract file_id and chunk_index from vector_id (format: file_{file_id}_chunk_{index})
                try:
                    vector_parts = vector_id.split('_')
                    if len(vector_parts) >= 4:
                        file_id = vector_parts[1]
                        chunk_index = vector_parts[3]
                        # Delete the specific document with file_id and chunk_index
                        doc_id = f"{file_id}_{chunk_index}"
                        self.delete_document(doc_id=doc_id, user_id=user_id)
                    else:
                        # Backward compatibility for old format
                        file_id = vector_parts[1] if len(vector_parts) > 1 else vector_id
                        self.delete_document(doc_id=file_id, user_id=user_id)
                except Exception as e:
                    logger.warning(f"Error deleting vector {vector_id}: {str(e)}")
                    continue
            return True
        except Exception as e:
            logger.error(f"Error deleting vectors from Weaviate: {str(e)}")
            raise Exception(f"Error deleting vectors from Weaviate: {str(e)}")

    def delete_namespace(self, namespace: str) -> bool:
        """Delete an entire namespace matching the vector service interface."""
        try:
            user_id = namespace.replace("user_", "")
            collection = self.client.collections.get(self.collection_name)
            user_filter = weaviate.classes.query.Filter.by_property("user_id").equal(user_id)
            collection.data.delete_many(where=user_filter)
            return True
        except Exception as e:
            logger.error(f"Error deleting namespace from Weaviate: {str(e)}")
            raise Exception(f"Error deleting namespace from Weaviate: {str(e)}")