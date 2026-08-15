"""Apply writer proposals through deterministic memory rules."""

import re
from typing import Dict, List, Optional

from memory.constants import (
    NEVER_EXPIRES,
    PINNED_TOPIC_HEADINGS,
    TOKEN_BUDGET,
    MemoryKind,
    MemoryState,
    Sensitivity,
)
from memory.domain.guards import inspect_write
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
_QUESTION_STARTS = frozenset(
    "what when where who why how which do does did is are am was were can could "
    "would should will have has had".split()
)


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


def _words(text: str) -> List[str]:
    return [word.strip(".,;:!?()\"'") for word in text.split() if word]


def _supports_repetition(message: str, stored_text: str) -> bool:
    """Require the user to restate some identifying content from the fact."""
    message_words = _words(message)
    if (
        not message_words
        or "?" in message
        or message_words[0].lower() in _QUESTION_STARTS
    ):
        return False
    stored_words = _words(stored_text)
    anchors = {
        word.lower()
        for position, word in enumerate(stored_words)
        if word
        and (any(char.isdigit() for char in word) or position and word[0].isupper())
    }
    candidates = anchors or {word.lower() for word in stored_words if len(word) > 3}
    return bool(candidates & {word.lower() for word in message_words})


def apply_decisions(input: ApplyInput, decisions: List[WriterDecision]) -> ApplyResult:
    now = input.now
    turn_policy = inspect_write(input.user_message or "")
    entries: List[LedgerDraft] = []
    created: List[MemoryRow] = []
    reinforced_ids: List[str] = []
    profile_updates: Dict[str, str] = {}

    user_doc = input.user_doc
    profile_changed = False

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
        sensitive = redact or turn_policy.credential
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
        sensitivity = decision.sensitivity or Sensitivity.NONE
        if key.split(":")[0] == "person" and sensitivity != Sensitivity.SAFETY:
            sensitivity = Sensitivity.THIRD_PARTY
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
                if sensitivity != Sensitivity.SAFETY
                and (
                    sensitivity in (Sensitivity.HEALTH, Sensitivity.THIRD_PARTY)
                    or key.startswith("health")
                )
                else MemoryState.ACTIVE
            ),
            importance=_clamp01(
                decision.importance,
                0.7 if kind == MemoryKind.PROCEDURE else 0.5,
            ),
            confidence=_clamp01(decision.confidence, 0.9),
            sensitivity=sensitivity,
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

    def retire(target: MemoryRow, replacement: MemoryRow) -> None:
        nonlocal profile_changed
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

    # Budget pins together, not one proposal at a time.
    pins_this_pass: List[tuple] = []

    for decision in decisions:
        proposal = decision
        text = (decision.text or "").strip()
        action = decision.action
        key = decision.key
        pinned_to = ""
        pin_refused = False

        if turn_policy.override and action != "ignore":
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

        if turn_policy.credential and action != "ignore":
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
        proposal_policy = inspect_write(text)
        if action != "ignore" and proposal_policy.credential:
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
        if action != "ignore" and proposal_policy.override:
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
            user_repeated = repeated is not None and _supports_repetition(
                input.user_message, repeated.text
            )
            if user_repeated:
                repeated.reinforced += 1
                reinforced_ids.append(repeated.id)
            log(
                action="ignore",
                proposed_action=decision.action,
                reason=decision.reason,
                note=(
                    None
                    if repeated is None
                    else (
                        (
                            f'Said again — "{repeated.text}" now stands on '
                            f"{repeated.reinforced + 1} tellings."
                        )
                        if user_repeated
                        else "Already stored; a question does not count as another telling."
                    )
                ),
                applied=True,
                record_id=repeated.id if repeated is not None else None,
                detail=text,
            )
            continue

        topic = (key or "").split(":")[0]
        automatic_heading = PINNED_TOPIC_HEADINGS.get(topic, "")
        requested_heading = decision.pinned_to or automatic_heading
        if decision.sensitivity == Sensitivity.SAFETY:
            requested_heading = requested_heading or "constraints"

        if requested_heading and action in ("add_fact", "supersede"):
            key = key or downgraded_key(requested_heading, text)
            instruction = (
                bool(automatic_heading) or requested_heading == "communication"
            )
            if requested_heading == "identity" and key in (None, "name"):
                instruction = True

            if (
                not input.explicit
                and not instruction
                and decision.sensitivity != Sensitivity.SAFETY
            ):
                log(
                    action=action,
                    proposed_action=decision.action,
                    reason=decision.reason,
                    note=(
                        "Filed without a profile pin. USER.md travels with "
                        "every turn, so pinning requires an explicit request, "
                        "an addressing preference, or a safety fact."
                    ),
                    applied=False,
                    record_id=None,
                    detail=text,
                )
            else:
                pinned_to = requested_heading

        if pinned_to:
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
                        proposed_action=decision.action,
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
                user_repeated = _supports_repetition(input.user_message, collision.text)
                if user_repeated:
                    collision.reinforced += 1
                    reinforced_ids.append(collision.id)
                newly_pinned = bool(pinned_to and collision.pinned_to != pinned_to)
                if newly_pinned:
                    collision.pinned_to = pinned_to
                    profile_updates[collision.id] = pinned_to
                    profile_changed = True
                said = (
                    "twice"
                    if collision.reinforced == 1
                    else f"{collision.reinforced + 1} times"
                )
                if newly_pinned and user_repeated:
                    repetition_note = (
                        f"Pinned to {heading_for(pinned_to)} and counted "
                        f"the repetition ({said} total)."
                    )
                elif newly_pinned:
                    repetition_note = f"Pinned to {heading_for(pinned_to)}."
                elif user_repeated:
                    repetition_note = (
                        f"Said {said} now — counted, because repetition is "
                        "the evidence consolidation promotes on."
                    )
                else:
                    repetition_note = "A question does not count as another telling."
                log(
                    action="ignore",
                    proposed_action=decision.action,
                    reason=decision.reason,
                    note=f"Already stored under {fact_key}. {repetition_note}",
                    applied=newly_pinned or user_repeated,
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
    return ApplyResult(
        entries=entries,
        profile_changed=profile_changed,
        archive=archive,
        created=created,
        reinforced_ids=reinforced_ids,
        profile_updates=profile_updates,
    )
