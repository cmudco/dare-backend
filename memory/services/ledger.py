"""Create consistently redacted memory ledger entries."""

import json
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional

from memory.domain.guards import inspect_write
from memory.domain.types import LedgerDraft
from memory.models import MemoryLedgerEntry, MemoryRecord

REDACTED_CREDENTIAL = "[redacted: credential]"


@dataclass(frozen=True)
class LedgerEvent:
    action: str
    reason: str
    applied: bool
    proposed_action: Optional[str] = None
    note: Optional[str] = None
    record: Optional[MemoryRecord] = None
    detail: str = ""
    source_text: str = ""
    source_message: Optional[Any] = None
    proposal: Optional[Dict[str, Any]] = None


def _safe_fields(event: LedgerEvent) -> Dict[str, Any]:
    proposal_text = json.dumps(_proposal_content(event.proposal), default=str)
    payload = "\n".join(
        (event.reason, event.note or "", event.detail, event.source_text, proposal_text)
    )
    if not inspect_write(payload).credential:
        return {
            "reason": event.reason,
            "note": event.note,
            "detail": event.detail,
            "source_text": event.source_text,
            "proposal": event.proposal,
        }
    return {
        "reason": "A credential was excluded from the memory ledger.",
        "note": REDACTED_CREDENTIAL,
        "detail": REDACTED_CREDENTIAL,
        "source_text": REDACTED_CREDENTIAL,
        "proposal": None,
    }


def _proposal_content(value: Any) -> Any:
    """Remove structural IDs before scanning proposal text for credentials."""
    if isinstance(value, dict):
        return {
            key: _proposal_content(item)
            for key, item in value.items()
            if not key.endswith("_id")
        }
    if isinstance(value, list):
        return [_proposal_content(item) for item in value]
    return value


def record_event(user, event: LedgerEvent) -> MemoryLedgerEntry:
    fields = _safe_fields(event)
    return MemoryLedgerEntry.objects.create(
        user=user,
        action=event.action,
        proposed_action=event.proposed_action or event.action,
        applied=event.applied,
        record=event.record,
        source_message=event.source_message,
        **fields,
    )


def model_from_draft(user, draft: LedgerDraft, source_message) -> MemoryLedgerEntry:
    event = LedgerEvent(
        action=draft.action,
        proposed_action=draft.proposed_action,
        reason=draft.reason,
        note=draft.note,
        applied=draft.applied,
        detail=draft.detail,
        source_text=draft.source_text,
        source_message=source_message,
        proposal=draft.proposal,
    )
    fields = _safe_fields(event)
    return MemoryLedgerEntry(
        id=uuid.UUID(draft.id),
        user=user,
        action=draft.action,
        proposed_action=draft.proposed_action,
        applied=draft.applied,
        record_id=uuid.UUID(draft.record_id) if draft.record_id else None,
        source_message=source_message,
        **fields,
    )
