"""Application services used by the memory API."""

import logging
from typing import Any, Dict, List, Optional

from django.db import transaction

from conversations.models import Message
from memory.constants import (
    ACTIVE_BACKFILL_STATUSES,
    TOKEN_BUDGET,
    TOKEN_WARNING,
    MemoryState,
    WriterAction,
)
from memory.domain.guards import inspect_write
from memory.domain.user_doc import (
    estimate_tokens,
    normalize_line,
    normalize_user_doc,
    parse_user_doc,
    render_user_doc,
)
from memory.models import (
    MemoryBackfillRun,
    MemoryLedgerEntry,
    MemoryRecord,
    UserMemoryDocument,
)
from memory.services import consolidation
from memory.services.edit import edit_doc_line, edit_record
from memory.services.items import (
    DOC_ID_PREFIX,
    doc_line_id,
    listed_records,
    pinned_records,
    profile_items,
    record_item,
    row_item,
)
from memory.services.ledger import LedgerEvent, record_event
from memory.services.retrieval import retrieve, summarize_recall
from memory.services.session_search import search_sessions_hits
from memory.services.store import read_user_doc, tokenize

logger = logging.getLogger(__name__)


class MemoryServiceError(Exception):
    """Base error returned to the API layer."""

    def __init__(self, detail: str = "", *, payload: Optional[Dict[str, Any]] = None):
        super().__init__(detail)
        self.payload = payload or {}


class MemoryNotFound(MemoryServiceError):
    pass


class MemoryInvalid(MemoryServiceError):
    pass


class MemoryConflict(MemoryServiceError):
    pass


class MemoryUnavailable(MemoryServiceError):
    pass


def _document(user) -> Optional[UserMemoryDocument]:
    return UserMemoryDocument.objects.filter(user=user).first()


def _profile(user) -> List[Dict[str, Any]]:
    return profile_items(_document(user), pinned_records(user))


def list_items(user, *, retired: bool = False) -> List[Dict[str, Any]]:
    if retired:
        return [
            record_item(record) for record in listed_records(user, include_retired=True)
        ]

    items = _profile(user)
    items.extend(record_item(record) for record in listed_records(user))
    return items


def get_item(user, item_id: str) -> Dict[str, Any]:
    if item_id.startswith(DOC_ID_PREFIX):
        item = next((item for item in _profile(user) if item["id"] == item_id), None)
        if item is None:
            raise MemoryNotFound
        return item

    record = MemoryRecord.visible(user).filter(pk=item_id).first()
    if record is None:
        raise MemoryNotFound
    return record_item(record)


def update_item(user, item_id: str, content: str) -> Optional[Dict[str, Any]]:
    if item_id.startswith(DOC_ID_PREFIX):
        result = edit_doc_line(user, item_id, content)
        if result.not_found:
            raise MemoryNotFound
        if not result.ok:
            raise MemoryInvalid(result.reason)

        normalized = normalize_line(content)
        return next(
            (item for item in _profile(user) if item["content"] == normalized), None
        )

    record = MemoryRecord.visible(user).filter(pk=item_id).first()
    if record is None:
        raise MemoryNotFound

    result = edit_record(user, record, content)
    if not result.ok:
        raise MemoryInvalid(result.reason)
    record.refresh_from_db()
    return record_item(record)


def forget_item(user, item_id: str) -> None:
    if item_id.startswith(DOC_ID_PREFIX):
        _forget_doc_line(user, item_id)
        return

    record = MemoryRecord.visible(user).filter(pk=item_id).first()
    if record is None:
        raise MemoryNotFound

    with transaction.atomic():
        record.soft_delete()
        record_event(
            user,
            LedgerEvent(
                action=WriterAction.FORGET,
                reason="The user asked for this memory to be forgotten.",
                applied=True,
                record=record,
                detail=record.text,
            ),
        )


def _forget_doc_line(user, item_id: str) -> None:
    document = _document(user)
    if document is None:
        raise MemoryNotFound

    parsed = parse_user_doc(document.content)
    for key, lines in parsed.items():
        for line in lines:
            if doc_line_id(key, line) != item_id:
                continue
            with transaction.atomic():
                lines.remove(line)
                document.content = render_user_doc(parsed)
                document.save(update_fields=["content", "updated_at"])
                record_event(
                    user,
                    LedgerEvent(
                        action=WriterAction.FORGET,
                        reason="The user removed a USER.md line.",
                        applied=True,
                        detail=f"[{key}] {line}",
                    ),
                )
            return

    raise MemoryNotFound


def search_items(user, query: str) -> Dict[str, Any]:
    recall = retrieve(user, query, top_k=10, floor=0.05)
    items = [row_item(item.record, score=item.score) for item in recall.chosen]

    query_terms = set(tokenize(query))
    for item in _profile(user):
        line_terms = set(tokenize(item["content"]))
        overlap = len(query_terms & line_terms)
        if not overlap:
            continue
        matched = dict(item)
        matched["score"] = round(min(1.0, overlap / len(query_terms)), 4)
        items.append(matched)

    items.sort(key=lambda item: item.get("score", 0.0), reverse=True)
    return {"query": query, "items": items, "categories": []}


def clear_store(user) -> Dict[str, Any]:
    if MemoryBackfillRun.active_objects.filter(
        user=user,
        status__in=ACTIVE_BACKFILL_STATUSES,
    ).exists():
        raise MemoryConflict("Wait for memory building to finish before clearing it.")

    with transaction.atomic():
        records, _ = MemoryRecord.objects.filter(user=user).delete()
        MemoryLedgerEntry.objects.filter(user=user).delete()
        UserMemoryDocument.objects.filter(user=user).update(content="")
        Message.active_objects.filter(conversation__user=user).exclude(
            memory_write_data__isnull=True
        ).update(memory_write_data=None)

    logger.info("[memory] user %s cleared memory (%s rows)", user.id, records)
    return {
        "success": True,
        "message": "All memories deleted across every layer.",
    }


def _budget(markdown: str) -> Dict[str, int]:
    return {
        "tokens": estimate_tokens(markdown),
        "limit": TOKEN_BUDGET,
        "warn_at": TOKEN_WARNING,
    }


def get_document(user) -> Dict[str, Any]:
    document = _document(user)
    markdown = read_user_doc(user)
    return {
        "markdown": markdown,
        "updated_at": document.updated_at.isoformat() if document else None,
        "budget": _budget(markdown),
    }


def save_document(user, markdown: Any) -> Dict[str, Any]:
    if markdown is None or not str(markdown).strip():
        raise MemoryInvalid("A markdown body is required. To erase, use clear/.")

    normalized = normalize_user_doc(str(markdown))
    policy = inspect_write(normalized)
    if policy.credential:
        raise MemoryInvalid("Credentials cannot be stored in USER.md.")
    if policy.override:
        raise MemoryInvalid("USER.md cannot override the assistant's instructions.")
    tokens = estimate_tokens(normalized)
    if tokens > TOKEN_BUDGET:
        raise MemoryInvalid(
            (
                f"USER.md would reach {tokens} tokens, past the "
                f"{TOKEN_BUDGET} ceiling. Trim it before saving."
            ),
            payload={"budget": _budget(normalized)},
        )

    document, _ = UserMemoryDocument.objects.get_or_create(user=user)
    document.content = normalized
    document.save(update_fields=["content", "updated_at"])
    return {
        "markdown": normalized,
        "updated_at": document.updated_at.isoformat(),
        "budget": _budget(normalized),
    }


def get_consolidation(user) -> Dict[str, Any]:
    return consolidation.propose(user)


def apply_consolidation(user, proposal: Dict[str, Any]) -> Dict[str, str]:
    if not proposal.get("kind") or not proposal.get("record_id"):
        raise MemoryInvalid("A proposal needs a kind and a record_id.")

    result = consolidation.apply(user, proposal)
    if not result.ok:
        raise MemoryInvalid(result.reason)
    return {"detail": result.detail}


def get_ledger(user, raw_limit: Any = 100) -> Dict[str, List[Dict[str, Any]]]:
    try:
        limit = min(max(int(raw_limit), 1), 500)
    except (TypeError, ValueError):
        limit = 100

    newest = MemoryLedgerEntry.objects.filter(user=user).order_by("-created_at")[:limit]
    entries = [
        {
            "id": str(entry.id),
            "at": entry.created_at.isoformat(),
            "action": entry.action,
            "proposed_action": entry.proposed_action,
            "reason": entry.reason,
            "note": entry.note,
            "applied": entry.applied,
            "record_id": str(entry.record_id) if entry.record_id else None,
            "detail": entry.detail,
            "source_text": entry.source_text,
            "proposal": entry.proposal,
        }
        for entry in reversed(list(newest))
    ]
    return {"entries": entries}


def set_hold(user, record_id: Any, held: Any) -> Dict[str, Any]:
    if record_id is None or not isinstance(held, bool):
        raise MemoryInvalid("Body must be {id, held: boolean}.")

    record = MemoryRecord.visible(user).filter(pk=record_id).first()
    if record is None:
        raise MemoryNotFound
    if record.state == MemoryState.SUPERSEDED:
        raise MemoryConflict(
            "A superseded memory cannot be held or released — it was retired, "
            "not gated."
        )

    target = MemoryState.HELD if held else MemoryState.ACTIVE
    if record.state != target:
        with transaction.atomic():
            record.state = target
            record.save(update_fields=["state", "updated_at"])
            action = WriterAction.HOLD if held else WriterAction.RELEASE
            record_event(
                user,
                LedgerEvent(
                    action=action,
                    reason=(
                        "The user gated this memory by hand."
                        if held
                        else "The user released this memory by hand."
                    ),
                    applied=True,
                    record=record,
                    detail=record.text,
                ),
            )

    return record_item(record)


def get_sessions(
    user, *, query: str = "", since: Optional[str] = None, until: Optional[str] = None
) -> Dict[str, Any]:
    if not query and not since and not until:
        raise MemoryInvalid("Pass ?q=<words>, ?since=YYYY-MM-DD, or both.")

    result = search_sessions_hits(user, query=query, since=since, until=until)
    if result.get("success"):
        return result
    error = result.get("error", "Search failed.")
    if result.get("bad_request"):
        raise MemoryInvalid(error)
    raise MemoryUnavailable(error)


def get_recall(user, query: str) -> Dict[str, Any]:
    if not query:
        raise MemoryInvalid("Pass ?q=<query>.")
    return summarize_recall(retrieve(user, query), considered=12)
