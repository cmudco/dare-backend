"""Turning proposals into writes.

The model proposes; this module disposes. Keeping them apart is what lets the
application hold rules the model cannot talk its way past, and it is why this
module is pure: no network, no storage, no clock beyond what is passed in.
Give it a state and some decisions, get back the next state and a ledger.

Two rules do the real work here:

1. USER.md is earned, not asserted. A direct patch needs either an explicit
   request or a safety fact behind it. Everything else is sent to the archive,
   because one turn is weak evidence that anything is durable.
2. The budget refuses rather than overflows.

Every refusal lands in the ledger with the rule that caused it. A memory
system you cannot audit is just a text file that grows.
"""

import re
from typing import List, Optional

from memory.constants import (
    ADDRESSING_HEADINGS,
    NEVER_EXPIRES,
    MemoryKind,
    MemoryState,
    Sensitivity,
)
from memory.domain.keys import downgraded_key
from memory.domain.types import (
    ApplyInput,
    ApplyResult,
    LedgerDraft,
    MemoryRow,
    WriterDecision,
)
from memory.domain.user_doc import heading_for, patch_user_doc

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _clamp01(value: Optional[float], fallback: float) -> float:
    if not isinstance(value, (int, float)) or value != value:  # NaN-safe
        return fallback
    return min(1.0, max(0.0, float(value)))


def _iso_date(value: Optional[str]) -> Optional[str]:
    return value if value and _ISO_DATE_RE.match(value) else None


def apply_decisions(input: ApplyInput, decisions: List[WriterDecision]) -> ApplyResult:
    now = input.now
    entries: List[LedgerDraft] = []
    created: List[MemoryRow] = []
    reinforced_ids: List[str] = []

    user_doc = input.user_doc
    user_doc_changed = False
    retired = False

    # Work on copies so a key collision inside a single pass is caught too.
    archive = [MemoryRow(**vars(row)) for row in input.archive]

    # Set at the top of each iteration so every log() call below records the
    # proposal it came from without having to pass it down by hand.
    proposal: Optional[WriterDecision] = None

    def log(
        action: str,
        proposed_action: str,
        reason: str,
        note: Optional[str],
        applied: bool,
        record_id: Optional[str],
        detail: str,
    ) -> None:
        entries.append(
            LedgerDraft(
                id=input.new_id(),
                at=now,
                action=action,
                proposed_action=proposed_action,
                reason=reason,
                note=note,
                applied=applied,
                record_id=record_id,
                detail=detail,
                source_text=input.user_message,
                proposal=proposal.as_dict() if proposal else None,
            )
        )

    def build(decision: WriterDecision, kind: str, text: str, key: str) -> MemoryRow:
        occurred = _iso_date(decision.occurred_at)
        return MemoryRow(
            id=input.new_id(),
            kind=kind,
            key=key,
            text=text,
            source_conversation_id=input.source_conversation_id,
            source_message_id=input.source_message_id,
            created_at=now,
            occurred_at=occurred,
            valid_from=occurred or now[:10],
            # An end date the person actually stated. Without this a temporary
            # fact is believed forever. Refused on the slot topics — a person
            # never stops having a location, they get a new one (NEVER_EXPIRES).
            valid_until=(
                None
                if key.split(":")[0] in NEVER_EXPIRES
                else _iso_date(decision.valid_until)
            ),
            superseded_by=None,
            replaces=None,
            # Held, not active, when it is medical and nobody asked us to keep
            # it. The KEY counts as well as the declared sensitivity: a privacy
            # rule the model can opt out of by being cheerful is not a rule.
            # `safety` is deliberately excluded — an allergy in an approval
            # queue is a system that books the restaurant.
            state=(
                MemoryState.HELD
                if decision.sensitivity != Sensitivity.SAFETY
                and (
                    decision.sensitivity == Sensitivity.HEALTH
                    or key.startswith("health")
                )
                else MemoryState.ACTIVE
            ),
            importance=_clamp01(
                decision.importance,
                # A procedure that exists at all is a rule the person bothered
                # to state, and it is only ever read when its own trigger
                # fires — there is no such thing as an unimportant one worth
                # applying, so the default sits high rather than in the middle.
                0.7 if kind == MemoryKind.PROCEDURE else 0.5,
            ),
            confidence=_clamp01(decision.confidence, 0.9),
            sensitivity=decision.sensitivity or Sensitivity.NONE,
            provenance=input.user_message[:400],
            reinforced=0,
        )

    def file_row(row: MemoryRow) -> None:
        created.append(row)
        archive.append(row)

    def pin_safety(row: MemoryRow, reason: str) -> None:
        """Pin a safety fact into USER.md as well as the archive.

        The archive is only read when a question reaches for it, and the turn
        where an allergy matters is exactly the turn that does not mention it —
        "book me somewhere nice" contains no allergy. So safety does not wait
        to be retrieved, and it does not wait for the budget either: gate for
        privacy, where staying silent is free, never for safety, where silence
        is the hazard.
        """
        nonlocal user_doc, user_doc_changed

        if row.sensitivity != Sensitivity.SAFETY:
            return

        patched = patch_user_doc(user_doc, key="constraints", line=row.text, force=True)

        if not patched.ok:
            # Already there is the usual reason, and it is not a problem.
            log(
                action="patch_user",
                proposed_action="add_fact",
                reason=reason,
                note=f"Not pinned to USER.md: {patched.reason}",
                applied=False,
                record_id=row.id,
                detail=row.text,
            )
            return

        user_doc = patched.markdown
        user_doc_changed = True
        log(
            action="patch_user",
            proposed_action="add_fact",
            reason=reason,
            note=(
                "Pinned to USER.md as well. A safety fact cannot wait to be "
                "retrieved, and it is not held behind the token budget."
            ),
            applied=True,
            record_id=row.id,
            detail=f"[Constraints] {row.text}",
        )

    def retire(target: MemoryRow, replacement: MemoryRow) -> None:
        nonlocal retired
        replacement.replaces = target.id
        target.state = MemoryState.SUPERSEDED
        target.superseded_by = replacement.id
        # The old fact stops being true when the new one starts — except when
        # the replacement is backdated past the day the old one was recorded.
        # An interval that ends before it begins is unanswerable, so it
        # collapses to a point.
        target.valid_until = (
            target.valid_from
            if target.valid_from
            and replacement.valid_from
            and replacement.valid_from < target.valid_from
            else replacement.valid_from
        )
        retired = True

    for decision in decisions:
        proposal = decision
        text = (decision.text or "").strip()
        action = decision.action
        key = decision.key

        # Rule 1. USER.md is injected into every conversation, so a line there
        # costs tokens forever. Consolidation promotes what proves durable; a
        # single turn does not get to.
        #
        # Consent is the right gate for facts ABOUT someone and the wrong one
        # for instructions about how to ANSWER them. "Keep answers short" is
        # not information to be stored pending permission — it is a request,
        # and its whole value is that it applies to the next turn and every
        # turn after. Sent to the archive it only arrives when a question
        # happens to sound like it: measured on a real conversation, an
        # instruction given twice reached 1 turn in 6.
        # Identity earns the same exemption for the same reason: what to call
        # someone is how to address them, not a disclosure about their life,
        # and it is wrong on every turn it fails to reach.
        #
        # But the heading also holds where someone lives, and a profile line
        # cannot be superseded — it has no key to collide on. Pinned there,
        # "lives in Lahore" survives the move to Islamabad and the archive ends
        # up with two live answers to the same question. So identity only
        # qualifies when it is not carrying a life fact: no topic, or the one
        # topic that IS a form of address.
        instruction = decision.key in ADDRESSING_HEADINGS
        if instruction and decision.key == "identity":
            instruction = (decision.topic_key or "").split(":")[0] in ("", "name")
        if (
            action == "patch_user"
            and not input.explicit
            and not instruction
            and decision.sensitivity != Sensitivity.SAFETY
        ):
            action = "add_fact"
            # The key so far is a heading, which is the wrong namespace for a
            # fact. Prefer the topic the writer named — `location` rather than
            # `identity:lives-lahore` — so the fact can still collide with, and
            # be retired by, the same fact stated later. Falling back to the
            # heading only keeps two downgraded lines under one heading apart.
            key = decision.topic_key or downgraded_key(decision.key or "note", text)
            log(
                action=action,
                proposed_action="patch_user",
                reason=decision.reason,
                note=(
                    "Sent to the archive instead. USER.md is read on every "
                    "turn, so a line there needs an explicit request or a "
                    "safety fact behind it."
                ),
                applied=False,
                record_id=None,
                detail=text,
            )

        if action == "ignore":
            # An ignore that names what was repeated is not a no-op. Nothing is
            # written, but the row it points at just earned the only evidence
            # this system ever gets that it still matters, and consolidation
            # promotes on exactly that count.
            repeated = next(
                (
                    row
                    for row in archive
                    if decision.reinforces_id
                    and row.id == decision.reinforces_id
                    and row.state == MemoryState.ACTIVE
                ),
                None,
            )
            if repeated is not None:
                repeated.reinforced += 1
                reinforced_ids.append(repeated.id)
            log(
                action="ignore",
                proposed_action=decision.action,
                reason=decision.reason,
                note=(
                    f'Said again — "{repeated.text}" now stands on '
                    f"{repeated.reinforced + 1} tellings."
                    if repeated is not None
                    else None
                ),
                applied=True,
                record_id=repeated.id if repeated is not None else None,
                detail=text,
            )
            continue

        if action == "patch_user":
            if not key or not text:
                log(
                    action="ignore",
                    proposed_action="patch_user",
                    reason=decision.reason,
                    note="Dropped: a profile line needs both a heading key and a sentence.",
                    applied=False,
                    record_id=None,
                    detail=text,
                )
                continue

            # The key is the heading. Nothing else about a profile line is
            # stored — no id, no date, no validity. A line is either true of
            # the person or it does not belong in a file read on every turn.
            heading = heading_for(key)
            patched = patch_user_doc(
                user_doc, key=key, line=text, replaces_line=decision.replaces_line
            )

            if not patched.ok:
                log(
                    action="patch_user",
                    proposed_action="patch_user",
                    reason=decision.reason,
                    note=patched.reason,
                    applied=False,
                    record_id=None,
                    detail=f"[{heading}] {text}",
                )
                continue

            user_doc = patched.markdown
            user_doc_changed = True
            log(
                action="patch_user",
                proposed_action="patch_user",
                reason=decision.reason,
                note=patched.note,
                applied=True,
                record_id=None,
                detail=f"[{heading}] {text}",
            )
            continue

        if action == "supersede":
            if not text:
                log(
                    action="ignore",
                    proposed_action="supersede",
                    reason=decision.reason,
                    note="Dropped: no replacement statement was given.",
                    applied=False,
                    record_id=None,
                    detail=text,
                )
                continue

            target = next(
                (
                    row
                    for row in archive
                    if row.id == decision.supersedes_id
                    and row.state == MemoryState.ACTIVE
                ),
                None,
            )

            # A supersede is only honoured when the target is about the same
            # thing. Found live: asked about a change of city, the writer
            # handed back the id of an unrelated fact about the person's
            # sister, and a trusting implementation retired it. Two facts can
            # only replace one another if they share a key.
            same_subject = not key or not target or target.key == key

            if target and same_subject:
                row = build(decision, target.kind, text, key or target.key)
                retire(target, row)
                file_row(row)
                log(
                    action="supersede",
                    proposed_action=decision.action,
                    reason=decision.reason,
                    note=(
                        f'Retired "{target.text}" — kept, so questions about '
                        f"the past still reach it."
                    ),
                    applied=True,
                    record_id=row.id,
                    detail=text,
                )
                pin_safety(row, decision.reason)
                continue

            # Either the id is unknown, or it points at something about a
            # different subject. Refuse the destructive half and keep the rest:
            # the statement is still true, and dropping the whole decision
            # loses it. The key-collision check below retires a genuine
            # conflict anyway.
            log(
                action="add_fact",
                proposed_action="supersede",
                reason=decision.reason,
                note=(
                    (
                        f'Nothing was retired: that id points at "{target.text}", '
                        f"which is filed under {target.key or target.kind} rather "
                        f"than {key}. Two facts only replace one another when they "
                        f"are about the same thing. Filed as a new fact instead."
                    )
                    if target
                    else (
                        "Nothing was retired: that memory id is not in the active "
                        "archive. The statement itself is still true, so it is "
                        "filed as a new fact."
                    )
                ),
                applied=False,
                record_id=None,
                detail=text,
            )
            action = "add_fact"

        # add_fact and add_procedure. Anything else reaching here is an action
        # this build does not implement (a stale caller, or a model that
        # invented one), and falling through would file it as a fact without
        # saying so.
        if action not in ("add_fact", "add_procedure"):
            log(
                action="ignore",
                proposed_action=decision.action,
                reason=decision.reason,
                note=f'Dropped: "{decision.action}" is not an action this writer implements.',
                applied=False,
                record_id=None,
                detail=text,
            )
            continue

        kind = MemoryKind.PROCEDURE if action == "add_procedure" else MemoryKind.FACT
        noun = "procedure" if kind == MemoryKind.PROCEDURE else "fact"

        if not text:
            log(
                action="ignore",
                proposed_action=decision.action,
                reason=decision.reason,
                note=f"Dropped: the {noun} was empty.",
                applied=False,
                record_id=None,
                detail=text,
            )
            continue

        fact_key = key or ("when:general" if kind == MemoryKind.PROCEDURE else "note")

        # Kind is part of the collision test, not just the key. `when:` already
        # namespaces procedures away from facts, but relying on a string prefix
        # to keep two layers apart is the kind of thing that holds until
        # someone adds a topic called "when".
        collision = next(
            (
                row
                for row in archive
                if row.kind == kind
                and row.key == fact_key
                and row.state == MemoryState.ACTIVE
            ),
            None,
        )

        if collision:
            # Same key, same words: nothing was written. But the person said it
            # again, and that is the only signal the system ever gets that
            # something is durable rather than a passing remark. Count it —
            # consolidation reads this to decide what has earned a permanent
            # line.
            if collision.text.strip().lower() == text.lower():
                collision.reinforced += 1
                reinforced_ids.append(collision.id)
                said = (
                    "twice"
                    if collision.reinforced == 1
                    else f"{collision.reinforced + 1} times"
                )
                log(
                    action="ignore",
                    proposed_action=decision.action,
                    reason=decision.reason,
                    note=(
                        f"Already stored under {fact_key}. Said {said} now — "
                        f"counted, because repetition is the evidence "
                        f"consolidation promotes on."
                    ),
                    applied=False,
                    record_id=collision.id,
                    detail=text,
                )
                continue

            # Same key, different words. Two facts under one key cannot both be
            # true, which is exactly the rule that makes "she moved" a
            # retirement instead of a second opinion sitting next to the first.
            row = build(decision, kind, text, fact_key)
            # A restated rule is still the same rule being insisted on, so the
            # replacement inherits the history rather than starting from zero.
            row.reinforced = collision.reinforced + 1
            # And it inherits the end date, unless it states its own. An update
            # to a temporary fact is still about something temporary — without
            # this, "the ankle is improving" made the injury permanent again.
            row.valid_until = row.valid_until or collision.valid_until
            retire(collision, row)
            file_row(row)
            log(
                action="supersede",
                proposed_action=decision.action,
                reason=decision.reason,
                note=(
                    f'{fact_key} already held "{collision.text}". Two {noun}s '
                    f"under one key cannot both be true, so the older one was "
                    f"retired."
                ),
                applied=True,
                record_id=row.id,
                detail=text,
            )
            pin_safety(row, decision.reason)
            continue

        row = build(decision, kind, text, fact_key)
        file_row(row)
        log(
            action="add_procedure" if kind == MemoryKind.PROCEDURE else "add_fact",
            proposed_action=decision.action,
            reason=decision.reason,
            note=(
                (
                    "Held, not stored. Medical, and nobody asked for it to be "
                    "remembered — it is written down and visible, but it will "
                    "never be retrieved into an answer until it is released by "
                    "hand."
                )
                if row.state == MemoryState.HELD
                else None
            ),
            applied=True,
            record_id=row.id,
            detail=f"{fact_key} · {text}",
        )
        pin_safety(row, decision.reason)

    return ApplyResult(
        entries=entries,
        user_doc=user_doc,
        user_doc_changed=user_doc_changed,
        archive=archive,
        created=created,
        retired=retired,
        reinforced_ids=reinforced_ids,
    )
