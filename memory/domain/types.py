"""Data shapes shared by the pure memory modules.

``MemoryRow`` mirrors the ``MemoryRecord`` model field-for-field but stays a
plain dataclass so the gate and the ranker can run without a database — apply
mutates rows in place (retire, reinforce) and persistence diffs the result.
Dates travel as ISO strings here (``YYYY-MM-DD``), which compare correctly with
plain ``<`` and keep the pure layer free of timezone decisions.
"""

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
    # USER.md heading this fact renders under, or empty. The document is a
    # view of what is pinned, so a pinned row keeps its key and its timeline.
    pinned_to: str = ""
    source_conversation_id: Optional[Any] = None
    source_message_id: Optional[Any] = None


@dataclass
class WriterDecision:
    """One decision as it leaves the writer, keys already routed.

    ``key`` is a profile heading for ``patch_user`` and a qualified topic key
    otherwise; ``topic_key`` preserves the topic-derived key so a refused
    profile line can still collide with — and later be retired by — the same
    fact stated plainly.
    """

    action: str
    reason: str = ""
    text: Optional[str] = None
    key: Optional[str] = None
    topic_key: Optional[str] = None
    trigger: Optional[str] = None
    importance: Optional[float] = None
    confidence: Optional[float] = None
    sensitivity: Optional[str] = None
    occurred_at: Optional[str] = None
    valid_until: Optional[str] = None
    supersedes_id: Optional[str] = None
    replaces_line: Optional[str] = None
    # For ignore: the row the person just restated. Repetition is the only
    # durability signal the store ever gets, so an ignore that names its cause
    # is worth more than one that does not.
    reinforces_id: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class LedgerDraft:
    """One ledger line, ready to persist.

    ``action`` is what the application actually did; ``proposed_action`` is
    what the model asked for. The pairs where they differ are the audit trail's
    entire value.
    """

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
    """Everything the disposal gate needs, with the clock and ids injected so
    the result is deterministic and the tests can assert on it."""

    user_doc: str
    archive: List[MemoryRow]
    user_message: str
    # True when the person said "remember that..." — consent, in their own words.
    explicit: bool
    now: str  # ISO timestamp
    new_id: Callable[[], str]
    source_conversation_id: Optional[Any] = None
    source_message_id: Optional[Any] = None


@dataclass
class ApplyResult:
    entries: List[LedgerDraft]
    user_doc: str
    user_doc_changed: bool
    # The full archive after the pass, including rows this pass retired.
    archive: List[MemoryRow]
    # Only the rows created this pass.
    created: List[MemoryRow]
    # True when an existing row changed state, so storage must update it.
    retired: bool
    # Rows the person restated word for word this turn. Nothing was written for
    # these, but repetition is the only durability signal the system ever gets,
    # and consolidation promotes on it.
    reinforced_ids: List[str] = field(default_factory=list)
