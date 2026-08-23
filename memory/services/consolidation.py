"""Running the tidy-up sweep, and applying what the person approves.

The sweep is pure (memory/domain/consolidate.py); this is the half that reads
the store, supplies similarity from the stored vectors, and — only when asked
— commits an approved proposal.

Approval is re-validated at apply time. A proposal is a snapshot of a store
that keeps moving: between seeing "merge these two" and clicking it, a turn
may have retired one of them. Applying blind would resurrect a dead row or
retire a fact that had already been replaced.
"""

import logging
from typing import Dict, List, Optional

import numpy as np
from django.db import transaction

from memory.constants import TOKEN_BUDGET, MemoryState, Sensitivity, WriterAction
from memory.domain.consolidate import EVICT, MERGE, PROMOTE, REKEY
from memory.domain.consolidate import sweep as run_sweep
from memory.domain.keys import key_for
from memory.domain.types import MemoryRow
from memory.domain.user_doc import estimate_tokens, merge_pinned
from memory.models import MemoryRecord
from memory.services.ledger import LedgerEvent, record_event
from memory.services.store import read_authored_doc, read_user_doc, row_from_record

logger = logging.getLogger(__name__)


class AppliedResult:
    def __init__(self, ok: bool, reason: str = "", detail: str = ""):
        self.ok = ok
        self.reason = reason
        self.detail = detail


def _vectors(records) -> Dict[str, Optional[np.ndarray]]:
    out: Dict[str, Optional[np.ndarray]] = {}
    for record in records:
        value = getattr(record, "embedding", None)
        try:
            out[str(record.id)] = np.array(value) if value is not None else None
        except (TypeError, ValueError):
            out[str(record.id)] = None
    return out


def propose(user) -> Dict[str, object]:
    """What the store would like to tidy, for this person, right now."""
    records = list(
        MemoryRecord.usable(user)
        .filter(state=MemoryState.ACTIVE)
        .order_by("created_at")
    )
    vectors = _vectors(records)
    rows: List[MemoryRow] = [row_from_record(record) for record in records]

    def similarity(left: MemoryRow, right: MemoryRow) -> float:
        a, b = vectors.get(left.id), vectors.get(right.id)
        if a is None or b is None or a.shape != b.shape:
            # Without vectors there is no evidence of duplication, and guessing
            # from words alone would propose merging things that merely rhyme.
            return 0.0
        return float(np.dot(a, b))

    result = run_sweep(
        rows,
        similarity,
        profile_markdown=read_user_doc(user),
        authored_markdown=read_authored_doc(user),
    )
    return {
        "proposals": [item.as_dict() for item in result.proposals],
        "examined": result.examined,
        "profile_tokens": result.profile_tokens,
        "pinned_tokens": result.pinned_tokens,
    }


def apply(user, proposal: Dict[str, object]) -> AppliedResult:
    """Commit one approved proposal, re-checking the world first."""
    kind = str(proposal.get("kind") or "")
    record = (
        MemoryRecord.usable(user)
        .filter(pk=str(proposal.get("record_id") or ""), state=MemoryState.ACTIVE)
        .first()
    )
    if record is None:
        return AppliedResult(
            ok=False,
            reason="That memory has changed since this was suggested.",
        )

    if kind == MERGE:
        return _merge(user, record, proposal)
    if kind == PROMOTE:
        return _promote(user, record, proposal)
    if kind == REKEY:
        return _rekey(user, record, proposal)
    if kind == EVICT:
        return _evict(user, record)
    return AppliedResult(ok=False, reason=f"Unknown proposal: {kind}")


def _log(user, record, note: str, detail: str) -> None:
    record_event(
        user,
        LedgerEvent(
            action=WriterAction.CONSOLIDATE,
            reason="Tidy-up, approved by the person whose memory this is.",
            note=note,
            applied=True,
            record=record,
            detail=detail,
        ),
    )


def _merge(user, keep, proposal) -> AppliedResult:
    drop = (
        MemoryRecord.usable(user)
        .filter(pk=str(proposal.get("other_id") or ""), state=MemoryState.ACTIVE)
        .first()
    )
    if drop is None:
        return AppliedResult(
            ok=False, reason="The duplicate is already gone — nothing to merge."
        )
    if drop.id == keep.id:
        return AppliedResult(ok=False, reason="A memory cannot merge with itself.")

    with transaction.atomic():
        drop.state = MemoryState.SUPERSEDED
        drop.superseded_by = keep
        drop.save(update_fields=["state", "superseded_by", "updated_at"])
        # Repetition survives the merge: the duplicate is evidence the person
        # said this more than once, which is exactly what promotion reads.
        # The duplicate's tellings carry over, but merging does not invent a
        # new one. Two rows under different keys is a keying failure, not the
        # person insisting — and counting it as insistence made every merge
        # spawn a promotion, so the sweep never settled.
        keep.reinforced = keep.reinforced + drop.reinforced
        keep.save(update_fields=["reinforced", "updated_at"])
        _log(user, keep, f"Merged in “{drop.text}”", keep.text)
    return AppliedResult(ok=True, detail=f"Merged into “{keep.text}”")


def _promote(user, record, proposal) -> AppliedResult:
    if record.pinned_to:
        return AppliedResult(ok=False, reason="That is already in your profile.")
    heading = str(proposal.get("heading") or "") or _heading_for(record)
    tokens = estimate_tokens(
        merge_pinned(read_user_doc(user), [(heading, record.text)])
    )
    if record.sensitivity != Sensitivity.SAFETY and tokens > TOKEN_BUDGET:
        return AppliedResult(
            ok=False,
            reason=(
                f"That would push USER.md to {tokens} tokens, past the "
                f"{TOKEN_BUDGET} ceiling."
            ),
        )
    with transaction.atomic():
        record.pinned_to = heading
        record.save(update_fields=["pinned_to", "updated_at"])
        _log(user, record, f"Pinned under {heading}", record.text)
    return AppliedResult(ok=True, detail=f"Pinned to {heading}")


def _heading_for(record) -> str:
    topic = record.key.split(":")[0]
    if record.sensitivity == Sensitivity.SAFETY:
        return "constraints"
    if topic in {"name"}:
        return "identity"
    if topic in {"style"}:
        return "communication"
    if topic in {"location", "occupation", "industry"}:
        return "identity"
    return "background"


def _rekey(user, record, proposal) -> AppliedResult:
    topic = record.key.split(":")[0]
    fresh = key_for(topic, None, record.text)
    if fresh == record.key:
        return AppliedResult(ok=False, reason="The slot name already matches.")
    clash = (
        MemoryRecord.usable(user)
        .filter(kind=record.kind, key=fresh, state=MemoryState.ACTIVE)
        .exclude(pk=record.pk)
        .first()
    )
    if clash is not None:
        return AppliedResult(
            ok=False,
            reason=f"Another memory already holds that slot: “{clash.text}”.",
        )
    before = record.key
    with transaction.atomic():
        record.key = fresh
        record.save(update_fields=["key", "updated_at"])
        _log(user, record, f"Was filed under {before}", fresh)
    return AppliedResult(ok=True, detail=f"Refiled as {fresh}")


def _evict(user, record) -> AppliedResult:
    if not record.pinned_to:
        return AppliedResult(ok=False, reason="That is not in your profile.")
    if record.sensitivity == Sensitivity.SAFETY:
        return AppliedResult(
            ok=False,
            reason="A safety memory stays in your profile. Forget it outright instead.",
        )
    heading = record.pinned_to
    with transaction.atomic():
        record.pinned_to = ""
        record.save(update_fields=["pinned_to", "updated_at"])
        _log(
            user, record, f"Unpinned from {heading}; still in the archive", record.text
        )
    return AppliedResult(ok=True, detail="Unpinned — still searchable")
