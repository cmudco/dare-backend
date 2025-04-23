from typing import Dict, List, Optional, Tuple
from pinecone import Pinecone
from django.conf import settings
import logging
from config.env import PINECONE_API_KEY, PINECONE_INDEX_NAME

logger = logging.getLogger(__name__)

class PineconeClient:
    def __init__(self):
        try:
            logger.info(f"Initializing Pinecone client with index: {PINECONE_INDEX_NAME}")
            self.pc = Pinecone(api_key=PINECONE_API_KEY)
            self.index = self.pc.Index(PINECONE_INDEX_NAME)
            logger.info("Pinecone client initialized successfully")
        except Exception as e:
            logger.error(f"Error initializing Pinecone client: {str(e)}")
            raise Exception(f"Error initializing Pinecone client: {str(e)}")

    def upsert_vectors(
        self,
        vectors: List[Tuple[str, List[float], Dict]],
        namespace: Optional[str] = None
    ) -> bool:
        """Upsert vectors to Pinecone."""
        try:
            logger.info(f"Upserting {len(vectors)} vectors to Pinecone namespace '{namespace}'")

            # Log some sample vector data for debugging
            if vectors:
                sample_id, sample_vector, sample_metadata = vectors[0]
                vector_len = len(sample_vector)
                logger.info(f"Sample vector - id: {sample_id}, vector length: {vector_len}")
                logger.info(f"Sample metadata: {sample_metadata}")

            formatted_vectors = []
            for i, (id, vector, metadata) in enumerate(vectors):
                logger.debug(f"Processing vector {i+1}/{len(vectors)}: id={id}")

                # Ensure chunk_index is in metadata and is an integer
                if 'chunk_index' in metadata:
                    chunk_index = metadata['chunk_index']
                    logger.debug(f"Vector {id} has chunk_index={chunk_index}")
                else:
                    # Extract chunk_index from id if possible (format: file_{file_id}_chunk_{index})
                    try:
                        parts = id.split('_')
                        if len(parts) >= 4 and parts[2] == 'chunk':
                            chunk_index = int(parts[3])
                            metadata['chunk_index'] = chunk_index
                            logger.debug(f"Extracted chunk_index={chunk_index} from id={id}")
                    except (IndexError, ValueError) as e:
                        logger.warning(f"Could not extract chunk_index from id={id}: {str(e)}")

                formatted_vectors.append((id, vector, metadata))

            # Log batch size
            logger.info(f"Sending batch of {len(formatted_vectors)} vectors to Pinecone")

            # Perform the upsert
            response = self.index.upsert(
                vectors=formatted_vectors,
                namespace=namespace
            )

            # Log response
            logger.info(f"Pinecone upsert response: {response}")

            # Verify vectors were inserted by querying for one
            if vectors:
                sample_id = vectors[0][0]
                logger.info(f"Verifying upsert by fetching vector with id={sample_id}")
                try:
                    fetch_response = self.index.fetch(ids=[sample_id], namespace=namespace)
                    if sample_id in fetch_response.vectors:
                        logger.info(f"Vector {sample_id} successfully verified in Pinecone")
                    else:
                        logger.warning(f"Vector {sample_id} not found in Pinecone after upsert!")
                except Exception as e:
                    logger.error(f"Error verifying vector {sample_id}: {str(e)}")

            return True
        except Exception as e:
            logger.error(f"Error upserting vectors to Pinecone: {str(e)}", exc_info=True)
            raise Exception(f"Error upserting vectors to Pinecone: {str(e)}")

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
            logger.info(f"Querying Pinecone with top_k={top_k}, namespace={namespace}, filter={filter}")

            # Log vector length
            logger.debug(f"Query vector length: {len(vector)}")

            results = self.index.query(
                vector=vector,
                top_k=top_k,
                namespace=namespace,
                filter=filter,
                include_metadata=True
            )

            # Log results
            match_count = len(results.matches) if hasattr(results, 'matches') else 0
            logger.info(f"Pinecone query returned {match_count} matches")

            if match_count > 0:
                for i, match in enumerate(results.matches[:3]):  # Log first 3 matches
                    logger.info(f"Match {i+1}: id={match.id}, score={match.score}")
                    if hasattr(match, 'metadata'):
                        chunk_index = match.metadata.get('chunk_index', 'not found')
                        logger.info(f"   metadata: chunk_index={chunk_index}")

            return results.matches
        except Exception as e:
            logger.error(f"Error querying vectors from Pinecone: {str(e)}", exc_info=True)
            raise Exception(f"Error querying vectors from Pinecone: {str(e)}")

    def delete_namespace(self, namespace: str) -> bool:
        """Delete an entire namespace."""
        try:
            self.index.delete(delete_all=True, namespace=namespace)
            return True
        except Exception as e:
            raise Exception(f"Error deleting namespace: {str(e)}")