"""Apply writer proposals through deterministic memory rules."""

import re
from typing import List, Optional

from memory.constants import (
    ADDRESSING_HEADINGS,
    NEVER_EXPIRES,
    PINNED_TOPICS,
    TOKEN_BUDGET,
    MemoryKind,
    MemoryState,
    Sensitivity,
)
from memory.domain.guards import demands_override, looks_like_secret
from memory.domain.keys import distinguishing_key, downgraded_key
from memory.domain.types import (
    ApplyInput,
    ApplyResult,
    LedgerDraft,
    MemoryRow,
    WriterDecision,
)
from memory.domain.user_doc import (
    estimate_tokens,
    heading_for,
    merge_pinned,
    without_line,
)

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _clamp01(value: Optional[float], fallback: float) -> float:
    if not isinstance(value, (int, float)) or value != value:  # NaN-safe
        return fallback
    return min(1.0, max(0.0, float(value)))


def _iso_date(value: Optional[str]) -> Optional[str]:
    return value if value and _ISO_DATE_RE.match(value) else None


_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def _dated(text: str, iso_day: str) -> str:
    """Add a date to a measured value unless it already has one."""
    try:
        year, month, _ = iso_day.split("-")
        stamp = f"{_MONTHS[int(month) - 1]} {year}"
    except (ValueError, IndexError):
        return text
    if stamp.lower() in text.lower() or year in text:
        return text
    return f"{text.rstrip('.')} (as of {stamp})."


def apply_decisions(input: ApplyInput, decisions: List[WriterDecision]) -> ApplyResult:
    now = input.now
    turn_has_secret = looks_like_secret(input.user_message or "")
    turn_is_override = demands_override(input.user_message or "")
    entries: List[LedgerDraft] = []
    created: List[MemoryRow] = []
    reinforced_ids: List[str] = []

    user_doc = input.user_doc
    profile_changed = False
    retired = False

    # Decisions in the same turn must see each other's writes.
    archive = [MemoryRow(**vars(row)) for row in input.archive]

    proposal: Optional[WriterDecision] = None

    def log(
        action: str,
        proposed_action: str,
        reason: str,
        note: Optional[str],
        applied: bool,
        record_id: Optional[str],
        detail: str,
        redact: bool = False,
    ) -> None:
        sensitive = redact or turn_has_secret
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
                detail="[redacted: credential]" if sensitive else detail,
                source_text=(
                    "[redacted: credential]" if sensitive else input.user_message
                ),
                proposal=(
                    None if sensitive or proposal is None else proposal.as_dict()
                ),
            )
        )

    def build(
        decision: WriterDecision,
        kind: str,
        text: str,
        key: str,
        pinned_to: str = "",
    ) -> MemoryRow:
        occurred = _iso_date(decision.occurred_at)
        if decision.is_snapshot:
            occurred = occurred or now[:10]
            text = _dated(text, occurred)
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
            valid_until=(
                None
                if key.split(":")[0] in NEVER_EXPIRES
                else _iso_date(decision.valid_until)
            ),
            superseded_by=None,
            replaces=None,
            state=(
                MemoryState.HELD
                if decision.sensitivity != Sensitivity.SAFETY
                and (
                    decision.sensitivity
                    in (Sensitivity.HEALTH, Sensitivity.THIRD_PARTY)
                    or key.startswith("health")
                )
                else MemoryState.ACTIVE
            ),
            importance=_clamp01(
                decision.importance,
                0.7 if kind == MemoryKind.PROCEDURE else 0.5,
            ),
            confidence=_clamp01(decision.confidence, 0.9),
            sensitivity=decision.sensitivity or Sensitivity.NONE,
            provenance=input.user_message[:400],
            reinforced=0,
            pinned_to=pinned_to,
            applies_when=(decision.applies_when or "")[:300],
        )

    def file_row(row: MemoryRow) -> None:
        nonlocal profile_changed
        created.append(row)
        archive.append(row)
        profile_changed = profile_changed or bool(row.pinned_to)

    def pin_safety(row: MemoryRow, reason: str) -> None:
        """Pin a safety fact so it never depends on retrieval."""
        nonlocal profile_changed
        if row.sensitivity != Sensitivity.SAFETY:
            return

        if row.pinned_to:
            return

        row.pinned_to = "constraints"
        profile_changed = True
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
        nonlocal profile_changed, retired
        replacement.replaces = target.id
        target.state = MemoryState.SUPERSEDED
        target.superseded_by = replacement.id
        # Never produce an interval that ends before it begins.
        target.valid_until = (
            target.valid_from
            if target.valid_from
            and replacement.valid_from
            and replacement.valid_from < target.valid_from
            else replacement.valid_from
        )
        profile_changed = profile_changed or bool(target.pinned_to)
        retired = True

    # Budget pins together, not one proposal at a time.
    pins_this_pass: List[tuple] = []

    for decision in decisions:
        proposal = decision
        text = (decision.text or "").strip()
        action = decision.action
        key = decision.key
        pinned_to = ""
        pin_refused = False

        if turn_is_override and action != "ignore":
            log(
                action="ignore",
                proposed_action=action,
                reason=decision.reason,
                note=(
                    "Refused: this message asks for standing instructions to "
                    "be ignored or replaced, and a turn like that is not "
                    "trusted to write memory. Nothing from it was stored."
                ),
                applied=False,
                record_id=None,
                detail=text,
            )
            continue

        if turn_has_secret and action != "ignore":
            log(
                action="ignore",
                proposed_action=action,
                reason=decision.reason,
                note=(
                    "Refused: this turn contains a credential. Nothing from "
                    "it was stored in memory."
                ),
                applied=False,
                record_id=None,
                detail=text,
                redact=True,
            )
            continue

        # Also guard against a writer inventing a secret absent from the turn.
        if action != "ignore" and looks_like_secret(text):
            log(
                action="ignore",
                proposed_action=action,
                reason=decision.reason,
                note=(
                    "Refused: this looks like a credential — a password, key "
                    "or token. Secrets are never stored in memory; keep them "
                    "in a password manager."
                ),
                applied=False,
                record_id=None,
                detail=text,
                redact=True,
            )
            continue

        # Reject prompt injection in a proposed row even when the turn was safe.
        if action != "ignore" and demands_override(text):
            log(
                action="ignore",
                proposed_action=action,
                reason=decision.reason,
                note=(
                    "Refused: a memory row cannot carry instructions to "
                    "ignore, replace or bypass the assistant's rules. How to "
                    "behave is welcome as a procedure; whether the rules "
                    "apply is not up for storage."
                ),
                applied=False,
                record_id=None,
                detail=text,
            )
            continue

        # Pinning is content policy, independent of the writer's chosen action.
        if action == "add_fact" and decision.pin_to_profile:
            action = "patch_user"
            key = decision.profile_key or "identity"

        # Keep the collision key when a profile proposal replaces it with a heading.
        topic_key = decision.topic_key or (
            decision.key if decision.action == "add_fact" else None
        )
        pinned_topic = (topic_key or "").split(":")[0] in PINNED_TOPICS
        if action == "add_fact" and pinned_topic:
            action = "patch_user"
            key = "identity"

        if action == "patch_user" and (not key or not text):
            log(
                action="ignore",
                proposed_action="patch_user",
                reason=decision.reason,
                note="Dropped: a profile line needs a heading and a sentence.",
                applied=False,
                record_id=None,
                detail=text,
            )
            continue

        # Instructions may pin immediately; ordinary life facts require consent.
        instruction = pinned_topic or decision.key in ADDRESSING_HEADINGS
        if instruction and decision.key == "identity" and not pinned_topic:
            # Only an unqualified identity line is a form of address.
            instruction = not (decision.topic_key or "")
        if (
            action == "patch_user"
            and not input.explicit
            and not instruction
            and decision.sensitivity != Sensitivity.SAFETY
        ):
            action = "add_fact"
            # Archive facts keep their topic key so later corrections collide.
            key = topic_key or downgraded_key(decision.key or "note", text)
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
            # A named repetition reinforces the existing row.
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

        # Machine-written profile lines are facts projected into USER.md.
        if action == "patch_user":
            pinned_to = key or "identity"
            key = topic_key or downgraded_key(pinned_to, text)
            action = "add_fact"

            # Budget against the rendered document; a replacement is a swap.
            if decision.sensitivity != Sensitivity.SAFETY:
                outgoing = next(
                    (
                        row
                        for row in archive
                        if row.kind == MemoryKind.FACT
                        and row.key == key
                        and row.state == MemoryState.ACTIVE
                        and row.pinned_to
                    ),
                    None,
                )
                base = without_line(user_doc, outgoing.text) if outgoing else user_doc
                tokens = estimate_tokens(
                    merge_pinned(base, pins_this_pass + [(pinned_to, text)])
                )
                # Allow an over-budget document to become no larger.
                if tokens > TOKEN_BUDGET and not (
                    outgoing and tokens <= estimate_tokens(user_doc)
                ):
                    log(
                        action="add_fact",
                        proposed_action="patch_user",
                        reason=decision.reason,
                        note=(
                            f"Filed in the archive, not the profile: USER.md "
                            f"would reach {tokens} tokens, past the "
                            f"{TOKEN_BUDGET} ceiling. The fact is kept and "
                            f"retrievable — replace or remove a profile line "
                            f"to make room."
                        ),
                        applied=False,
                        record_id=None,
                        detail=f"[{heading_for(pinned_to)}] {text}",
                    )
                    pinned_to = ""
                    pin_refused = True
            if pinned_to:
                pins_this_pass.append((pinned_to, text))

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

            # A supplied id may only retire a row in the same slot.
            same_subject = not key or not target or target.key == key

            if target and same_subject:
                row = build(
                    decision,
                    target.kind,
                    text,
                    key or target.key,
                    pinned_to or ("" if pin_refused else target.pinned_to),
                )
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

            # Refuse the destructive half but keep the new statement.
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

        # Never let an unknown action fall through as a fact.
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

        # Kind and key together define a slot.
        def seek_collision(candidate_key: str) -> Optional[MemoryRow]:
            return next(
                (
                    row
                    for row in archive
                    if row.kind == kind
                    and row.key == candidate_key
                    and row.state == MemoryState.ACTIVE
                ),
                None,
            )

        collision = seek_collision(fact_key)

        # Boundaries coexist unless an explicit supersede names one.
        boundary_note = None
        while (
            collision
            and kind == MemoryKind.FACT
            and fact_key.split(":", 1)[0] in ("boundary", "boundaries")
            and collision.text.strip().lower() != text.lower()
        ):
            fact_key = distinguishing_key(fact_key, text)
            boundary_note = boundary_note or (
                f'Kept alongside "{collision.text}" — two boundaries are '
                f"separate protections, so neither retires the other."
            )
            collision = seek_collision(fact_key)

        if collision:
            # Repetition is durability evidence, not a duplicate write.
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

            # Different text in the same slot replaces the active row.
            row = build(decision, kind, text, fact_key, pinned_to)
            row.reinforced = collision.reinforced + 1
            # Preserve profile placement unless the budget refused it.
            row.pinned_to = row.pinned_to or (
                "" if pin_refused else collision.pinned_to
            )
            # An update to a temporary fact remains temporary.
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

        row = build(decision, kind, text, fact_key, pinned_to)
        file_row(row)
        log(
            action="add_procedure" if kind == MemoryKind.PROCEDURE else "add_fact",
            proposed_action=decision.action,
            reason=decision.reason,
            note=(
                (
                    (
                        "Held, not stored. This is about another person, and "
                        "the one consenting is not the one it concerns — it "
                        "is written down and visible, but it will never be "
                        "retrieved into an answer until it is released by "
                        "hand."
                    )
                    if row.sensitivity == Sensitivity.THIRD_PARTY
                    else (
                        "Held, not stored. Medical, and nobody asked for it "
                        "to be remembered — it is written down and visible, "
                        "but it will never be retrieved into an answer until "
                        "it is released by hand."
                    )
                )
                if row.state == MemoryState.HELD
                else boundary_note
            ),
            applied=True,
            record_id=row.id,
            detail=f"{fact_key} · {text}",
        )
        pin_safety(row, decision.reason)

    return ApplyResult(
        entries=entries,
        profile_changed=profile_changed,
        archive=archive,
        created=created,
        retired=retired,
        reinforced_ids=reinforced_ids,
    )
