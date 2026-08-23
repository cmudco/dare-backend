"""Per-turn context-assembly trace.

Records what went into the prompt before round 1 of a chat turn — prompt,
referenced conversations, full file reads, retrieval, memory, history — with
per-stage timings. The finished payload is persisted on the message and
emitted to the frontend as one ``context_trace`` event, where it renders as
the "how this answer was built" chip.

Keys are camelCase at construction (like ``RetrievalTrace.to_payload``) so
the payload has the same shape over the socket and the REST serializer.
"""

import time
from contextlib import contextmanager
from typing import Any, Dict, List


class ContextTraceRecorder:
    """Collects timed stages while a turn's prompt is being assembled."""

    def __init__(self) -> None:
        self._started = time.monotonic()
        self._stages: List[Dict[str, Any]] = []

    @contextmanager
    def stage(self, kind: str):
        """Time one assembly stage; the caller fills in its detail.

        Yields a dict pre-seeded with ``kind``. Add detail keys to it inside
        the block; leave it empty-but-for-kind to drop the stage (stages that
        found nothing to add are noise, not signal).
        """
        detail: Dict[str, Any] = {"kind": kind}
        start = time.monotonic()
        try:
            yield detail
        finally:
            if len(detail) > 1:
                detail["ms"] = int((time.monotonic() - start) * 1000)
                self._stages.append(detail)

    def add_stage(self, kind: str, ms: int = 0, **detail: Any) -> None:
        """Record a stage measured by the caller (used outside the builder)."""
        self._stages.append({"kind": kind, "ms": ms, **detail})

    def to_payload(self) -> Dict[str, Any]:
        """The trace as a wire/DB payload.

        Returned even when no stage recorded anything: the first turn of a
        conversation has no history/retrieval/files, but prepare_chat still
        appends its media/tools stages afterward — a None here would drop
        those (and the whole trace) exactly on first turns.
        """
        return {
            "totalMs": int((time.monotonic() - self._started) * 1000),
            "stages": self._stages,
        }
