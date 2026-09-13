"""Live search-index health for one file.

Publication verifies a generation once; this answers the later question of
whether the vectors are still there. The published attempt row records how
many chunks were verified into the active generation (chunk indexes are
contiguous from zero), the vector backend says what it holds now, and the
result is the difference. Files indexed before attempt rows existed fall back
to their map rows, and files with neither can only report presence. Identities
only: the full content comparison already happened at publication time.
"""

import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

from django.utils import timezone

from core.services.vector_service import get_vector_service
from files.constants import FileStatus
from files.models import DocumentChunk, File, VectorIndexAttempt
from users.constants import VectorDBChoice

logger = logging.getLogger(__name__)

STATE_VERIFIED = "verified"
STATE_INCOMPLETE = "incomplete"
STATE_MISSING = "missing"
STATE_UNAVAILABLE = "unavailable"
STATE_UNVERIFIABLE = "unverifiable"
STATE_NOT_INDEXED = "not_indexed"
STATE_PROCESSING = "processing"

MISSING_CHUNKS_SHOWN = 100


@dataclass(frozen=True)
class IndexHealth:
    state: str
    present: int
    checked_at: datetime
    generation: str = ""
    backend: Optional[str] = None
    expected: Optional[int] = None
    missing_count: int = 0
    missing_chunks: List[int] = field(default_factory=list)
    unexpected: int = 0
    error: str = ""

    def as_dict(self) -> dict:
        return {
            "state": self.state,
            "expected": self.expected,
            "present": self.present,
            "missing_count": self.missing_count,
            "missing_chunks": self.missing_chunks,
            "unexpected": self.unexpected,
            "generation": self.generation,
            "backend": self.backend,
            "checked_at": self.checked_at,
            "error": self.error,
        }


def expected_chunk_indexes(file: File) -> List[int]:
    """Every chunk the active generation was published with, including recovered
    text that is embedded but never becomes a map row."""
    if file.index_generation:
        verified = (
            VectorIndexAttempt.objects.filter(
                file=file, generation=file.index_generation, status="published"
            )
            .values_list("verified_count", flat=True)
            .first()
        )
        if verified:
            return list(range(verified))
    return list(
        DocumentChunk.objects.filter(file=file)
        .order_by("chunk_index")
        .values_list("chunk_index", flat=True)
    )


def check_index_health(file: File) -> IndexHealth:
    checked_at = timezone.now()
    expected = expected_chunk_indexes(file)
    backend = (
        VectorDBChoice(file.vector_db_source).label
        if file.vector_db_source is not None
        else None
    )
    base = {
        "checked_at": checked_at,
        "generation": file.vector_index_key,
        "backend": backend,
        "expected": len(expected) if expected else None,
    }

    if file.status == FileStatus.PROCESSING:
        return IndexHealth(state=STATE_PROCESSING, present=0, **base)
    if file.vector_db_source is None or (
        not expected and file.status != FileStatus.PROCESSED
    ):
        return IndexHealth(state=STATE_NOT_INDEXED, present=0, **base)

    service = None
    try:
        service = get_vector_service(file.user_id, backend=file.vector_db_source)
        stored = service.list_generation_chunk_indexes(
            file.vector_index_key, file.user_id, file.pk
        )
    except Exception as error:
        logger.warning(
            "Search index health check unavailable for file %s: %s",
            file.pk,
            error,
        )
        return IndexHealth(
            state=STATE_UNAVAILABLE,
            present=0,
            error="The vector database could not be reached.",
            **base,
        )
    finally:
        if service is not None:
            service.close()

    counts = Counter(stored)
    if not expected:
        # Indexed before chunk rows existed: presence can be shown, completeness cannot.
        return IndexHealth(
            state=STATE_UNVERIFIABLE if counts else STATE_MISSING,
            present=sum(counts.values()),
            **base,
        )

    wanted = set(expected)
    missing = [index for index in expected if index not in counts]
    unexpected = sum(
        count if index not in wanted else count - 1 for index, count in counts.items()
    )
    present = len(wanted) - len(missing)
    if present == 0:
        state = STATE_MISSING
    elif missing or unexpected:
        state = STATE_INCOMPLETE
    else:
        state = STATE_VERIFIED
    return IndexHealth(
        state=state,
        present=present,
        missing_count=len(missing),
        missing_chunks=missing[:MISSING_CHUNKS_SHOWN],
        unexpected=unexpected,
        **base,
    )
