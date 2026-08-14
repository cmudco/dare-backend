"""Typed data passed between pure memory modules."""

from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class MemoryRow:
    """One archive row, detached from storage."""

    id: str
    kind: str  # MemoryKind value
    key: str
    text: str
    state: str  # MemoryState value
    created_at: str  # ISO timestamp — when we found out
    occurred_at: Optional[str] = None  # YYYY-MM-DD — when it happened
    valid_from: Optional[str] = None  # when it became true in the world
    valid_until: Optional[str] = None  # when it stopped being true
    superseded_by: Optional[str] = None
    replaces: Optional[str] = None
    importance: float = 0.5
    confidence: float = 0.9
    sensitivity: str = "none"
    provenance: str = ""
    reinforced: int = 0
    # USER.md heading this row renders under, or empty.
    pinned_to: str = ""
    applies_when: str = ""
    source_conversation_id: Optional[Any] = None
    source_message_id: Optional[Any] = None


@dataclass
class WriterDecision:
    """A writer proposal with its storage keys already resolved."""

    action: str
    reason: str = ""
    text: Optional[str] = None
    key: Optional[str] = None
    applies_when: Optional[str] = None
    pinned_to: Optional[str] = None
    importance: Optional[float] = None
    confidence: Optional[float] = None
    sensitivity: Optional[str] = None
    occurred_at: Optional[str] = None
    valid_until: Optional[str] = None
    is_snapshot: bool = False
    supersedes_id: Optional[str] = None
    # Existing row repeated by an ignore decision.
    reinforces_id: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class LedgerDraft:
    """One auditable gate decision, ready to persist."""

    id: str
    at: str
    action: str
    proposed_action: str
    reason: str
    note: Optional[str]
    applied: bool
    record_id: Optional[str]
    detail: str
    source_text: str
    proposal: Optional[Dict[str, Any]] = None


@dataclass
class ApplyInput:
    """Gate inputs with injected time and IDs for deterministic execution."""

    user_doc: str
    archive: List[MemoryRow]
    user_message: str
    explicit: bool
    now: str  # ISO timestamp
    new_id: Callable[[], str]
    source_conversation_id: Optional[Any] = None
    source_message_id: Optional[Any] = None


@dataclass
class ApplyResult:
    entries: List[LedgerDraft]
    profile_changed: bool
    archive: List[MemoryRow]
    created: List[MemoryRow]
    reinforced_ids: List[str] = field(default_factory=list)
    profile_updates: Dict[str, str] = field(default_factory=dict)
