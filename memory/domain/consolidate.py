"""Pure consolidation proposals for duplicates, pins, keys, and crowding."""

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence

from memory.constants import (
    MAX_PER_KIND,
    MAX_PROPOSALS,
    MERGE_DISJOINT_SIMILARITY,
    MERGE_SIMILARITY,
    PROMOTE_AFTER_TELLINGS,
    TOKEN_BUDGET,
    MemoryKind,
    MemoryState,
    Sensitivity,
)
from memory.domain.types import MemoryRow
from memory.domain.user_doc import estimate_tokens, merge_pinned

MERGE = "merge"
PROMOTE = "promote"
REKEY = "rekey"
EVICT = "evict"


@dataclass
class Proposal:
    """One suggested change, with everything needed to judge it."""

    kind: str
    # The row the change happens to.
    record_id: str
    text: str
    reason: str
    # For merge: the row that would be retired. For rekey: the new key.
    other_id: Optional[str] = None
    other_text: Optional[str] = None
    detail: str = ""

    def as_dict(self) -> Dict[str, object]:
        return {
            "kind": self.kind,
            "record_id": self.record_id,
            "text": self.text,
            "reason": self.reason,
            "other_id": self.other_id,
            "other_text": self.other_text,
            "detail": self.detail,
        }


@dataclass
class SweepResult:
    proposals: List[Proposal] = field(default_factory=list)
    # What was looked at, so an empty result reads as "nothing to do" rather
    # than "this did not run".
    examined: int = 0
    # What the profile renders as, and what it would render as uncapped. The
    # second is the one crowding is judged on.
    profile_tokens: int = 0
    pinned_tokens: int = 0


def _qualifier(key: str) -> str:
    return key.split(":", 1)[1] if ":" in key else ""


def _words(text: str) -> set:
    return {word.strip(".,;:").lower() for word in text.split() if len(word) > 2}


def _specifics(text: str) -> set:
    """Extract proper nouns and numbers used to distinguish subjects."""
    tokens = [word.strip(".,;:!?()\"'") for word in text.split()]
    return {
        token.lower()
        for position, token in enumerate(tokens)
        if token
        and (token[0].isupper() and position > 0 or any(c.isdigit() for c in token))
    }


def _qualifiers_compatible(a: str, b: str) -> bool:
    """Return whether two qualifiers can name the same slot."""
    qa, qb = _qualifier(a), _qualifier(b)
    if not qa or not qb:
        return True
    # No length filter here, unlike prose tokens: qualifiers are slugs where
    # every piece is load-bearing — dropping "ok" from album-ok made it a
    # subset of album-random, and two albums merged.
    ta = set(qa.replace("-", " ").split())
    tb = set(qb.replace("-", " ").split())
    if not ta or not tb:
        return True
    a_extra = {t for t in ta if not _mentions(t, tb)}
    b_extra = {t for t in tb if not _mentions(t, ta)}
    return not (a_extra and b_extra)


def _mergeable(row, other, score: float) -> bool:
    """Decide whether two similar rows may be proposed as duplicates."""
    if _qualifiers_compatible(row.key, other.key):
        return score >= MERGE_SIMILARITY
    topic_a, topic_b = row.key.split(":")[0], other.key.split(":")[0]
    if topic_a == "person" or topic_b == "person":
        return False
    if score < MERGE_DISJOINT_SIMILARITY:
        return False
    mine, theirs = _specifics(row.text), _specifics(other.text)
    differs_both_ways = (mine - theirs) and (theirs - mine)
    return not differs_both_ways


def _mentions(word: str, words: set) -> bool:
    """Match a word with simple prefix-based inflection tolerance."""
    return any(
        other == word or other.startswith(word) or word.startswith(other)
        for other in words
    )


def sweep(
    rows: Sequence[MemoryRow],
    similarity: Callable[[MemoryRow, MemoryRow], float],
    profile_markdown: str = "",
    authored_markdown: str = "",
) -> SweepResult:
    """Return reviewable changes without mutating the archive."""
    active = [
        row
        for row in rows
        if row.state == MemoryState.ACTIVE and row.kind == MemoryKind.FACT
    ]
    all_pins = [(row.pinned_to, row.text) for row in active if row.pinned_to]
    pinned_demand = estimate_tokens(merge_pinned(authored_markdown, all_pins))
    result = SweepResult(
        examined=len(active),
        profile_tokens=estimate_tokens(profile_markdown),
        pinned_tokens=pinned_demand,
    )
    proposals: List[Proposal] = []

    # 1. Duplicates. Only across DIFFERENT keys — two rows under one key cannot
    #    both be active, the write gate already saw to that. This is for the
    #    same fact that entered twice under keys that never collided.
    #
    #    A row appears in at most ONE merge proposal. A family of similar rows
    #    otherwise produces a pairwise cascade where approving the first
    #    invalidates the rest, and the person watches ten suggestions fail.
    spoken_for: set = set()
    merges: List[Proposal] = []
    for index, row in enumerate(active):
        for other in active[index + 1 :]:
            if row.key == other.key:
                continue
            if row.id in spoken_for or other.id in spoken_for:
                continue
            if not _mergeable(row, other, similarity(row, other)):
                continue
            # A safety fact is never quietly folded into another row. Two
            # allergies that read alike are still two allergies.
            if Sensitivity.SAFETY in (row.sensitivity, other.sensitivity):
                continue
            spoken_for.update({row.id, other.id})
            # Keep the one that has been said more, then the more important,
            # then the one that says more. Repetition is evidence; length is
            # the last tiebreak because between two ways of saying one thing,
            # the fuller sentence loses less.
            keep, drop = sorted(
                (row, other),
                key=lambda item: (item.reinforced, item.importance, len(item.text)),
                reverse=True,
            )
            merges.append(
                Proposal(
                    kind=MERGE,
                    record_id=keep.id,
                    text=keep.text,
                    other_id=drop.id,
                    other_text=drop.text,
                    reason=(
                        f"Two memories say the same thing under different keys "
                        f"({keep.key} and {drop.key})."
                    ),
                    detail=f"Retire “{drop.text}”",
                )
            )

    # 2. Cold truths worth pinning. Repetition is the only durability signal
    #    the system gets, and a fact repeated is a fact that keeps mattering.
    for row in active:
        if row.pinned_to or row.reinforced < PROMOTE_AFTER_TELLINGS:
            continue
        proposals.append(
            Proposal(
                kind=PROMOTE,
                record_id=row.id,
                text=row.text,
                reason=(
                    f"Said {row.reinforced + 1} times and still looked up rather "
                    f"than carried. Pinning puts it in every conversation."
                ),
                detail="Pin to your profile",
            )
        )

    # 3. Slots named for what they used to hold. A key is an identity, so it
    #    is CORRECT that it does not follow the text — but "note:m1-pro-device"
    #    holding an M4 Max reads as a bug every time someone sees the tag.
    for row in active:
        qualifier = _qualifier(row.key)
        if not qualifier or row.reinforced == 0 and not row.replaces:
            continue
        parts = {part for part in qualifier.replace("-", " ").split() if len(part) > 2}
        if not parts:
            continue
        said = _words(row.text)
        if any(_mentions(part, said) for part in parts):
            continue
        proposals.append(
            Proposal(
                kind=REKEY,
                record_id=row.id,
                text=row.text,
                reason=(
                    f"Filed under “{row.key}”, which no longer describes what it "
                    f"says. The tag reads as stale wherever it is shown."
                ),
                detail="Rename the slot to match",
            )
        )

    # 4. Crowding. Only when the profile is actually over budget, because
    #    unpinning something nobody complained about is a change for its own
    #    sake. Safety is never offered.
    if result.pinned_tokens > TOKEN_BUDGET:
        pinned = [
            row
            for row in active
            if row.pinned_to
            and row.sensitivity != Sensitivity.SAFETY
            and row.reinforced == 0
        ]
        for row in sorted(pinned, key=lambda item: item.importance)[:2]:
            proposals.append(
                Proposal(
                    kind=EVICT,
                    record_id=row.id,
                    text=row.text,
                    reason=(
                        f"Your profile wants {result.pinned_tokens} tokens, past "
                        f"the {TOKEN_BUDGET} ceiling — lines past it are already "
                        f"being left out. This one has never been repeated, and "
                        f"it stays in the archive either way."
                    ),
                    detail="Unpin from your profile",
                )
            )

    # Capped per kind, not overall. A family of near-identical rows can raise
    # a dozen merges on its own, and a global cap let them hide every other
    # kind of proposal behind them.
    by_kind: Dict[str, List[Proposal]] = {}
    for proposal in merges + proposals:
        by_kind.setdefault(proposal.kind, []).append(proposal)

    queues = {
        kind: by_kind.get(kind, [])[:MAX_PER_KIND]
        for kind in (MERGE, PROMOTE, REKEY, EVICT)
    }
    ordered: List[Proposal] = []
    while len(ordered) < MAX_PROPOSALS and any(queues.values()):
        for kind in (MERGE, PROMOTE, REKEY, EVICT):
            if queues[kind] and len(ordered) < MAX_PROPOSALS:
                ordered.append(queues[kind].pop(0))

    result.proposals = ordered
    return result
