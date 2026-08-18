"""Correct memory records and authored profile lines in place."""

import logging
from dataclasses import dataclass
from typing import Optional

from django.db import connection, transaction

from memory.constants import TOKEN_BUDGET, MemoryKind, Sensitivity, WriterAction
from memory.domain.guards import inspect_write
from memory.domain.keys import procedure_key
from memory.domain.user_doc import (
    estimate_tokens,
    normalize_line,
    parse_user_doc,
    render_user_doc,
)
from memory.models import MemoryRecord, UserMemoryDocument
from memory.services.embeddings import embed_texts
from memory.services.items import doc_line_id, parse_behavior_content
from memory.services.ledger import LedgerEvent, record_event

logger = logging.getLogger(__name__)


@dataclass
class EditResult:
    ok: bool
    reason: Optional[str] = None
    # 404 when the target is gone, 400 when the new content is refused.
    not_found: bool = False


def edit_record(user, record: MemoryRecord, content: str) -> EditResult:
    """Correct a fact or a rule in place."""
    text = (content or "").strip()
    if not text:
        return EditResult(ok=False, reason="A memory needs some text.")

    policy = inspect_write(text)
    if policy.credential:
        return EditResult(ok=False, reason="Credentials cannot be stored in memory.")
    if policy.override:
        return EditResult(
            ok=False,
            reason="A memory cannot override the assistant's instructions.",
        )

    before = record.text
    key = record.key
    applies_when = record.applies_when

    # Rewriting a safety fact into something unrelated is the one edit that
    # loses information silently: it stays in the archive, but it stops being
    # carried into every turn, and the turn where it matters is the one that
    # never mentions it.
    if record.sensitivity == Sensitivity.SAFETY and not _still_covers(record, text):
        return EditResult(
            ok=False,
            reason=(
                f'"{record.text}" is marked as a safety fact, so it travels '
                f"with every message. Reword it however you like as long as "
                f"it still says what to avoid — or forget it outright if it "
                f"is no longer true."
            ),
        )

    if record.kind == MemoryKind.PROCEDURE:
        trigger, rule = parse_behavior_content(text)
        if not rule:
            return EditResult(ok=False, reason="A rule needs something to follow.")
        text = rule
        if trigger:
            # The trigger is the rule's identity, so changing it moves the row
            # to a new key — and that key must be free, or the edit would
            # silently retire someone else's rule.
            key = procedure_key(trigger, rule)
            applies_when = trigger
            clash = (
                MemoryRecord.usable(user)
                .filter(kind=MemoryKind.PROCEDURE, key=key)
                .exclude(pk=record.pk)
                .first()
            )
            if clash is not None:
                return EditResult(
                    ok=False,
                    reason=(
                        f"Another rule already covers that situation: "
                        f'"{clash.text}". Edit that one, or give this a '
                        f"different trigger."
                    ),
                )

    if text == before and key == record.key:
        return EditResult(ok=True)

    # An edited statement means something new, so it has to be findable by
    # what it now says rather than by what it used to. A rule is still found
    # by the situations it fires in, so editing one must not quietly drop it
    # back to the terse form that loses to unrelated rules.
    vector = None
    if connection.vendor == "postgresql":
        if record.kind == MemoryKind.PROCEDURE and applies_when:
            embed_source = f"{applies_when} {text}"
        else:
            embed_source = f"{key} {text}"
        vector = embed_texts([embed_source])[0]

    with transaction.atomic():
        record.text = text
        record.key = key
        record.applies_when = applies_when
        fields = ["text", "key", "applies_when", "updated_at"]
        if vector is not None:
            record.embedding = vector
            fields.append("embedding")
        record.save(update_fields=fields)

        record_event(
            user,
            LedgerEvent(
                action=WriterAction.EDIT,
                reason="The user rewrote this memory.",
                note=f'Was: "{before}"',
                applied=True,
                record=record,
                detail=f"{key} · {text}",
            ),
        )
    return EditResult(ok=True)


def _still_covers(record: MemoryRecord, text: str) -> bool:
    """Require a safety edit to retain the key's subject."""
    qualifier = record.key.split(":")[-1] if ":" in record.key else ""
    subjects = [word for word in qualifier.replace("-", " ").split() if len(word) > 2]
    if not subjects:
        # Nothing reliable to check for, so fall back to the strict reading:
        # only an edit that keeps the original wording is safe.
        return record.text.lower() in text.lower()
    lowered = text.lower()
    return any(subject in lowered for subject in subjects)


def edit_doc_line(user, item_id: str, content: str) -> EditResult:
    """Rewrite one authored USER.md line within the budget."""
    text = (content or "").strip()
    if not text:
        return EditResult(ok=False, reason="A profile line needs some text.")

    policy = inspect_write(text)
    if policy.credential:
        return EditResult(ok=False, reason="Credentials cannot be stored in USER.md.")
    if policy.override:
        return EditResult(
            ok=False,
            reason="USER.md cannot override the assistant's instructions.",
        )

    document = UserMemoryDocument.objects.filter(user=user).first()
    if document is None:
        return EditResult(ok=False, not_found=True)

    doc = parse_user_doc(document.content)
    line = normalize_line(text)
    if not line:
        return EditResult(ok=False, reason="A profile line needs some text.")

    for key, lines in doc.items():
        for index, existing in enumerate(lines):
            if doc_line_id(key, existing) != item_id:
                continue

            if line != existing and any(
                other.lower() == line.lower()
                for other_lines in doc.values()
                for other in other_lines
            ):
                return EditResult(ok=False, reason="USER.md already says this.")

            lines[index] = line
            rendered = render_user_doc(doc)
            tokens = estimate_tokens(rendered)
            if tokens > TOKEN_BUDGET and tokens > estimate_tokens(document.content):
                return EditResult(
                    ok=False,
                    reason=(
                        f"That would push USER.md to {tokens} tokens, past the "
                        f"{TOKEN_BUDGET} ceiling. Try something shorter."
                    ),
                )

            with transaction.atomic():
                document.content = rendered
                document.save(update_fields=["content", "updated_at"])
                record_event(
                    user,
                    LedgerEvent(
                        action=WriterAction.EDIT,
                        reason="The user rewrote a USER.md line.",
                        note=f'Was: "{existing}"',
                        applied=True,
                        detail=f"[{key}] {line}",
                    ),
                )
            return EditResult(ok=True)

    return EditResult(ok=False, not_found=True)
