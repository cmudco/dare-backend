from django.conf import settings
from typing import Dict, Any

VECTOR_DIMENSION = 3072

NAMESPACE_SEPARATOR = "_"

WEAVIATE_SETTINGS = {
    'HOST': getattr(settings, 'WEAVIATE_HOST', 'localhost'),
    'PORT': getattr(settings, 'WEAVIATE_PORT', 8080),
    'COLLECTION_NAME': getattr(settings, 'WEAVIATE_COLLECTION', 'Document'),
    'SKIP_INIT_CHECKS': getattr(settings, 'WEAVIATE_SKIP_INIT_CHECKS', True),
}

PINECONE_SETTINGS = {
    'API_KEY': getattr(settings, 'PINECONE_API_KEY', ''),
    'INDEX_NAME': getattr(settings, 'PINECONE_INDEX_NAME', ''),
}

def get_user_namespace(user_id: int) -> str:
    """Generate a namespace string for a user."""
    return f"user{NAMESPACE_SEPARATOR}{user_id}"

def get_user_id_from_namespace(namespace: str) -> int:
    """Extract user ID from a namespace string."""
    if not namespace or NAMESPACE_SEPARATOR not in namespace:
        raise ValueError(f"Invalid namespace format: {namespace}")
    try:
        prefix, user_id = namespace.split(NAMESPACE_SEPARATOR, 1)
        if prefix != "user":
            raise ValueError(f"Invalid namespace prefix: {prefix}")
        return int(user_id)
    except (ValueError, IndexError) as e:
        raise ValueError(f"Error parsing namespace {namespace}: {str(e)}")

def create_vector_id(file_id: int, chunk_index: int) -> str:
    """Create a standardized vector ID."""
    return f"file_{file_id}_chunk_{chunk_index}"

def parse_vector_id(vector_id: str) -> Dict[str, Any]:
    """Parse a vector ID into its components."""
    try:
        parts = vector_id.split('_')
        if len(parts) >= 4 and parts[0] == "file" and parts[2] == "chunk":
            return {
                "file_id": parts[1],
                "chunk_index": int(parts[3])
            }
    except (IndexError, ValueError):
        pass
    return {"file_id": "", "chunk_index": 0}
