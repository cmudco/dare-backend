"""Deterministic validation of a complete document index."""

import math
from numbers import Real

from core.config.processing import VECTOR_DIMENSION
from core.config.vector_db import create_vector_id


class VectorIntegrityError(ValueError):
    """Safe, deterministic validation failure without provider payloads."""


def validate_embedding(vector):
    if not isinstance(vector, (list, tuple)) or len(vector) != VECTOR_DIMENSION:
        raise VectorIntegrityError("Embedding has an incorrect dimension or is empty")
    if any(
        isinstance(value, bool)
        or not isinstance(value, Real)
        or not math.isfinite(value)
        for value in vector
    ):
        raise VectorIntegrityError(
            "Embedding contains non-finite or non-numeric values"
        )


def validate_generated(vectors, chunks, file_id, user_id):
    if len(vectors) != len(chunks):
        raise VectorIntegrityError(
            "Embedding count does not match the expected chunk count"
        )
    seen = set()
    for vector_id, embedding, metadata in vectors:
        index = metadata.get("chunk_index")
        if type(index) is not int or not 0 <= index < len(chunks) or index in seen:
            raise VectorIntegrityError(
                "Embedding chunk identity is missing, duplicated or invalid"
            )
        seen.add(index)
        if (
            vector_id != create_vector_id(file_id, index)
            or metadata.get("file_id") != str(file_id)
            or metadata.get("user_id") != str(user_id)
            or metadata.get("text") != chunks[index]
        ):
            raise VectorIntegrityError(
                "Embedding ownership or chunk metadata does not match"
            )
        validate_embedding(embedding)


def verify_stored(expected, objects):
    """Compare every returned identity and persisted metadata, including source text."""
    wanted = {metadata["chunk_index"]: metadata for _, _, metadata in expected}
    if len(objects) != len(wanted):
        raise VectorIntegrityError(
            "Stored generation count does not match expected chunks"
        )
    seen = set()
    for obj in objects:
        metadata = obj["metadata"]
        index = metadata.get("chunk_index")
        if type(index) is not int or index in seen or index not in wanted:
            raise VectorIntegrityError(
                "Stored generation contains duplicate or unexpected chunks"
            )
        seen.add(index)
        for key in (
            "file_id",
            "user_id",
            "chunk_index",
            "file_name",
            "file_type",
            "text",
            "body_text",
        ):
            if metadata.get(key) != wanted[index].get(key):
                raise VectorIntegrityError(f"Stored chunk metadata mismatch: {key}")
        validate_embedding(obj["vector"])
    if seen != set(wanted):
        raise VectorIntegrityError(
            "Stored generation is missing expected chunk identities"
        )
