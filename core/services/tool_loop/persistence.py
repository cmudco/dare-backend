"""Shared persistence helpers for tool-loop stores."""

import json
from typing import Dict

MAX_PERSISTED_RESULT_CHARS = 5000


def serialize_persisted_result(raw_result: Dict) -> str:
    """Serialize a bounded, valid JSON result for turn history."""
    serialized = json.dumps(raw_result)
    if len(serialized) <= MAX_PERSISTED_RESULT_CHARS:
        return serialized

    compact_result = {
        "truncated": True,
        "original_chars": len(serialized),
        "content_preview": serialized[: MAX_PERSISTED_RESULT_CHARS - 500],
    }
    for key in ("success", "artifactId", "artifact_id", "message", "error"):
        if key in raw_result:
            compact_result[key] = raw_result[key]

    # The preserved metadata can itself be unexpectedly large. Reduce the
    # preview until the envelope remains within the database/UI limit.
    compact_serialized = json.dumps(compact_result)
    overflow = len(compact_serialized) - MAX_PERSISTED_RESULT_CHARS
    if overflow > 0:
        compact_result["content_preview"] = compact_result["content_preview"][
            : -(overflow + 1)
        ]
        compact_serialized = json.dumps(compact_result)
    return compact_serialized
