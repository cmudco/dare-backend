"""Persist an inspectable per-attempt processing history for each uploaded file.

``processing_stage`` stays the cheap list value; this owns the file-viewer audit trail.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any, Dict, Optional

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from files.constants import FileProcessingStage
from files.models import File

JOURNEY_VERSION = 1
MAX_ATTEMPTS = 10
MAX_ERROR_LENGTH = 2000

STAGE_LABELS = {
    "parsing": "Docling parsing & classification",
    "enriching": "Visual enrichment",
    "embedding": "Embedding generation",
    "indexing": "Vector indexing",
}

STAGE_TO_FILE_STAGE = {
    "parsing": FileProcessingStage.PARSING,
    "enriching": FileProcessingStage.ENRICHING,
    "embedding": FileProcessingStage.EMBEDDING,
    "indexing": FileProcessingStage.INDEXING,
}


def _iso_now() -> str:
    return timezone.now().isoformat()


def _duration_seconds(started_at: Optional[str], completed_at: str) -> float:
    started = parse_datetime(started_at or "")
    completed = parse_datetime(completed_at)
    if not started or not completed:
        return 0.0
    return round(max((completed - started).total_seconds(), 0.0), 3)


def _json_value(value: Any) -> Any:
    """Keep stage details JSON-safe without hiding useful scalar metrics."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return str(value)


class FileProcessingJourney:
    """Mutable recorder for one file's current ingestion attempt."""

    def __init__(self, file: File):
        self.file = file
        payload = deepcopy(file.processing_journey or {})
        if payload.get("version") != JOURNEY_VERSION:
            payload = {"version": JOURNEY_VERSION, "attempts": []}
        payload.setdefault("attempts", [])
        self.payload: Dict[str, Any] = payload

    @property
    def attempts(self):
        return self.payload["attempts"]

    @property
    def current_attempt(self) -> Optional[Dict[str, Any]]:
        return self.attempts[-1] if self.attempts else None

    def begin_attempt(self) -> None:
        """Append a retry without erasing the evidence from earlier attempts."""
        previous = self.current_attempt
        if previous and previous.get("status") == "processing":
            completed_at = _iso_now()
            previous.update(
                {
                    "status": "failed",
                    "completed_at": completed_at,
                    "duration_seconds": _duration_seconds(
                        previous.get("started_at"), completed_at
                    ),
                    "error": "Processing restarted before this attempt completed.",
                }
            )

        next_number = (previous or {}).get("number", 0) + 1
        self.attempts.append(
            {
                "number": next_number,
                "status": "processing",
                "started_at": _iso_now(),
                "stages": [],
            }
        )
        self.payload["attempts"] = self.attempts[-MAX_ATTEMPTS:]
        self._persist()

    def stage(self, key: str) -> "ProcessingStageContext":
        if key not in STAGE_LABELS:
            raise ValueError(f"Unknown file processing stage: {key}")
        return ProcessingStageContext(self, key)

    def complete_attempt(self, outcome: str = "processed") -> None:
        attempt = self._require_attempt()
        completed_at = _iso_now()
        attempt.update(
            {
                "status": "complete",
                "outcome": outcome,
                "completed_at": completed_at,
                "duration_seconds": _duration_seconds(
                    attempt.get("started_at"), completed_at
                ),
            }
        )
        self._persist()

    def fail_attempt(self, error: Exception | str) -> None:
        attempt = self._require_attempt()
        completed_at = _iso_now()
        attempt.update(
            {
                "status": "failed",
                "completed_at": completed_at,
                "duration_seconds": _duration_seconds(
                    attempt.get("started_at"), completed_at
                ),
                "error": str(error)[:MAX_ERROR_LENGTH],
            }
        )
        self._persist()

    def _start_stage(self, key: str) -> Dict[str, Any]:
        attempt = self._require_attempt()
        stage = {
            "key": key,
            "label": STAGE_LABELS[key],
            "status": "running",
            "started_at": _iso_now(),
            "details": {},
        }
        attempt["stages"].append(stage)
        self.file.processing_stage = STAGE_TO_FILE_STAGE[key]
        self._persist(processing_stage=self.file.processing_stage)
        return stage

    def _finish_stage(
        self,
        stage: Dict[str, Any],
        status: str,
        details: Dict[str, Any],
        error: Optional[Exception | str] = None,
    ) -> None:
        completed_at = _iso_now()
        stage.update(
            {
                "status": status,
                "completed_at": completed_at,
                "duration_seconds": _duration_seconds(
                    stage.get("started_at"), completed_at
                ),
                "details": _json_value(details),
            }
        )
        if error is not None:
            stage["error"] = str(error)[:MAX_ERROR_LENGTH]
        self._persist()

    def _require_attempt(self) -> Dict[str, Any]:
        attempt = self.current_attempt
        if attempt is None:
            raise RuntimeError("Processing journey has no active attempt")
        return attempt

    def _persist(self, processing_stage: Optional[str] = None) -> None:
        update_fields: Dict[str, Any] = {"processing_journey": self.payload}
        if processing_stage is not None:
            update_fields["processing_stage"] = processing_stage
        File.active_objects.filter(pk=self.file.pk).update(**update_fields)
        self.file.processing_journey = deepcopy(self.payload)


class ProcessingStageContext:
    """Context manager that closes a stage correctly on every exit path."""

    def __init__(self, journey: FileProcessingJourney, key: str):
        self.journey = journey
        self.key = key
        self.stage: Optional[Dict[str, Any]] = None
        self.details: Dict[str, Any] = {}
        self.final_status = "complete"

    def __enter__(self) -> "ProcessingStageContext":
        self.stage = self.journey._start_stage(self.key)
        return self

    def add_details(self, **details: Any) -> None:
        self.details.update(details)

    def skip(self, reason: str, **details: Any) -> None:
        self.final_status = "skipped"
        self.details.update({"reason": reason, **details})

    def partial(self, reason: str, **details: Any) -> None:
        self.final_status = "partial"
        self.details.update({"reason": reason, **details})

    def __exit__(self, exc_type, exc, traceback) -> bool:
        if self.stage is None:
            return False
        if exc is not None:
            self.journey._finish_stage(self.stage, "failed", self.details, error=exc)
            return False
        self.journey._finish_stage(self.stage, self.final_status, self.details)
        return False
