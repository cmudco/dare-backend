"""Editing a memory by hand.

The writer proposes and the gate disposes — but the person whose memory this
is gets the last word, and that word goes through the same machinery: a
rewritten statement is re-embedded (an edit that kept its old vector would be
findable only by its old wording), a rewritten rule is re-keyed if its trigger
changed, and every edit lands in the ledger like any other decision.

What an edit deliberately does NOT do is supersede. A supersede means "this
was true, now something else is" and keeps both halves on a timeline. An edit
means "this was never quite right" — there is no second truth to keep, so the
row is corrected in place and the ledger carries the before and after.
"""

import logging
from dataclasses import dataclass
from typing import Optional

from django.db import connection, transaction

from memory.constants import (TOKEN_BUDGET, MemoryKind, Sensitivity,
                              WriterAction)
from memory.domain.keys import procedure_key
from memory.domain.user_doc import (estimate_tokens, normalize_line,
                                    parse_user_doc, render_user_doc)
from memory.models import MemoryLedgerEntry, MemoryRecord, UserMemoryDocument
from memory.services.embeddings import embed_texts
from memory.services.items import doc_line_id, parse_behavior_content

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

    before = record.text
    key = record.key

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
    # what it now says rather than by what it used to.
    vector = None
    if connection.vendor == "postgresql":
        vector = embed_texts([f"{key} {text}"])[0]

    with transaction.atomic():
        record.text = text
        record.key = key
        fields = ["text", "key", "updated_at"]
        if vector is not None:
            record.embedding = vector
            fields.append("embedding")
        record.save(update_fields=fields)

        MemoryLedgerEntry.objects.create(
            user=user,
            action=WriterAction.EDIT,
            proposed_action=WriterAction.EDIT,
            reason="The user rewrote this memory.",
            note=f'Was: "{before}"',
            applied=True,
            record=record,
            detail=f"{key} · {text}",
        )
    return EditResult(ok=True)


def _safety_pin_for(user, line: str) -> Optional[MemoryRecord]:
    """The active safety record this USER.md line was pinned from, if any.

    The gate writes a safety fact's text into Constraints verbatim, so an exact
    match is the link — USER.md itself carries no per-line metadata.
    """
    return (
        MemoryRecord.usable(user)
        .filter(kind=MemoryKind.FACT, sensitivity=Sensitivity.SAFETY, text=line)
        .first()
    )


def _still_covers(record: MemoryRecord, text: str) -> bool:
    """Whether a rewritten line still names what the safety fact is about.

    The subject comes from the qualified key (``diet_avoid:peanut`` → peanut),
    which is the one token the rewrite must keep. Rewording is fine; dropping
    the subject is not.
    """
    qualifier = record.key.split(":")[-1] if ":" in record.key else ""
    subjects = [word for word in qualifier.replace("-", " ").split() if len(word) > 2]
    if not subjects:
        # Nothing reliable to check for, so fall back to the strict reading:
        # only an edit that keeps the original wording is safe.
        return record.text.lower() in text.lower()
    lowered = text.lower()
    return any(subject in lowered for subject in subjects)


def edit_doc_line(user, item_id: str, content: str) -> EditResult:
    """Rewrite one USER.md bullet, keeping it under the budget.

    The line is found by the same content hash the list handed out, so an
    edit against a stale view fails to match rather than overwriting whatever
    now sits in that position.
    """
    text = (content or "").strip()
    if not text:
        return EditResult(ok=False, reason="A profile line needs some text.")

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

            # Rewriting a safety line into something unrelated is the one edit
            # that loses information silently: the fact stays in the archive,
            # but it stops being carried into every turn, and the turn where it
            # matters is the one that never mentions it.
            pinned = _safety_pin_for(user, existing)
            if pinned is not None and not _still_covers(pinned, line):
                return EditResult(
                    ok=False,
                    reason=(
                        f'This line is pinned here because "{pinned.text}" is '
                        f"marked as a safety fact, so it travels with every "
                        f"message. Reword it however you like as long as it "
                        f"still says what to avoid — or delete the underlying "
                        f"memory first if it is no longer true."
                    ),
                )

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
                MemoryLedgerEntry.objects.create(
                    user=user,
                    action=WriterAction.EDIT,
                    proposed_action=WriterAction.EDIT,
                    reason="The user rewrote a USER.md line.",
                    note=f'Was: "{existing}"',
                    applied=True,
                    detail=f"[{key}] {line}",
                )
            return EditResult(ok=True)

    return EditResult(ok=False, not_found=True)
