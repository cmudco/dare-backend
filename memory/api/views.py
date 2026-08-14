"""The memory API.

Serves the round-1 frontend contract unchanged (items / search / clear) on top
of the layered store. Scope is always ``request.user`` — no id in any URL ever
selects another person's memory.
"""

import logging

from django.db import transaction
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from memory.constants import TOKEN_BUDGET, TOKEN_WARNING, MemoryState, WriterAction
from memory.domain.user_doc import (
    estimate_tokens,
    normalize_line,
    normalize_user_doc,
    parse_user_doc,
    render_user_doc,
)
from memory.models import MemoryLedgerEntry, MemoryRecord, UserMemoryDocument
from memory.services import consolidation, portability
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
from memory.services.retrieval import retrieve, summarize_recall
from memory.services.session_search import search_sessions_hits
from memory.services.store import read_user_doc, tokenize

from .serializers import (
    ClearResponseSerializer,
    MemoryItemSerializer,
    MemorySearchRequestSerializer,
    MemorySearchResponseSerializer,
)

logger = logging.getLogger(__name__)


class MemoryViewSet(viewsets.ViewSet):
    """list / retrieve / destroy over the flattened item view, plus search and
    clear. The richer v2 surface (document, ledger, hold, recall probe) lives
    beside this in v2 paths."""

    permission_classes = [IsAuthenticated]

    def _document(self):
        return UserMemoryDocument.objects.filter(user=self.request.user).first()

    def list(self, request):
        # The page computes layer counts and filters client-side, so this is
        # the complete unpaginated set: profile lines first, then the archive.
        #
        # ?state=retired swaps it for the archive of retired rows instead of
        # adding them, because the two are read for different reasons: the
        # default list answers "what does it know", and this one answers "what
        # did it used to think".
        if request.query_params.get("state") == "retired":
            records = listed_records(request.user, include_retired=True)
            return Response(
                MemoryItemSerializer(
                    [record_item(record) for record in records], many=True
                ).data
            )

        items = profile_items(self._document(), pinned_records(self.request.user))
        items.extend(record_item(record) for record in listed_records(request.user))
        return Response(MemoryItemSerializer(items, many=True).data)

    def retrieve(self, request, pk=None):
        if pk and pk.startswith(DOC_ID_PREFIX):
            for item in profile_items(
                self._document(), pinned_records(self.request.user)
            ):
                if item["id"] == pk:
                    return Response(MemoryItemSerializer(item).data)
            return Response(status=status.HTTP_404_NOT_FOUND)

        record = MemoryRecord.visible(request.user).filter(pk=pk).first()
        if record is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(MemoryItemSerializer(record_item(record)).data)

    def partial_update(self, request, pk=None):
        """Rewrite one memory by hand.

        A correction, not a supersede: there is no second truth to keep on a
        timeline, so the row is fixed in place — re-embedded so it is findable
        by what it now says, re-keyed if a rule's trigger changed, and logged.
        """
        content = request.data.get("content")
        if content is None:
            return Response(
                {"detail": "A content field is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if pk and pk.startswith(DOC_ID_PREFIX):
            result = edit_doc_line(request.user, pk, str(content))
            if result.not_found:
                return Response(status=status.HTTP_404_NOT_FOUND)
            if not result.ok:
                return Response(
                    {"detail": result.reason}, status=status.HTTP_400_BAD_REQUEST
                )
            for item in profile_items(
                self._document(), pinned_records(self.request.user)
            ):
                if item["content"] == normalize_line(str(content)):
                    return Response(MemoryItemSerializer(item).data)
            return Response(status=status.HTTP_204_NO_CONTENT)

        record = MemoryRecord.visible(request.user).filter(pk=pk).first()
        if record is None:
            return Response(status=status.HTTP_404_NOT_FOUND)

        result = edit_record(request.user, record, str(content))
        if not result.ok:
            return Response(
                {"detail": result.reason}, status=status.HTTP_400_BAD_REQUEST
            )
        record.refresh_from_db()
        return Response(MemoryItemSerializer(record_item(record)).data)

    def destroy(self, request, pk=None):
        """Per-item forget.

        A USER.md line is dropped from the document; an archive row is
        soft-deleted (the supersession chain stays intact, and the row stays
        out of retrieval forever). Both leave a ledger entry — nothing is
        silently lost, including deletions.
        """
        if pk and pk.startswith(DOC_ID_PREFIX):
            return self._forget_doc_line(request, pk)

        record = MemoryRecord.visible(request.user).filter(pk=pk).first()
        if record is None:
            return Response(status=status.HTTP_404_NOT_FOUND)

        with transaction.atomic():
            record.soft_delete()
            MemoryLedgerEntry.objects.create(
                user=request.user,
                action=WriterAction.FORGET,
                proposed_action=WriterAction.FORGET,
                reason="The user asked for this memory to be forgotten.",
                applied=True,
                record=record,
                detail=record.text,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)

    def _forget_doc_line(self, request, pk):
        document = self._document()
        if document is None:
            return Response(status=status.HTTP_404_NOT_FOUND)

        doc = parse_user_doc(document.content)
        for key, lines in doc.items():
            for line in lines:
                if doc_line_id(key, line) == pk:
                    with transaction.atomic():
                        lines.remove(line)
                        document.content = render_user_doc(doc)
                        document.save(update_fields=["content", "updated_at"])
                        MemoryLedgerEntry.objects.create(
                            user=request.user,
                            action=WriterAction.FORGET,
                            proposed_action=WriterAction.FORGET,
                            reason="The user removed a USER.md line.",
                            applied=True,
                            detail=f"[{key}] {line}",
                        )
                    return Response(status=status.HTTP_204_NO_CONTENT)

        # The id no longer matches any line — the document changed since the
        # list was fetched. Refusing beats guessing at which line was meant.
        return Response(status=status.HTTP_404_NOT_FOUND)

    @action(detail=False, methods=["post"])
    def search(self, request):
        serializer = MemorySearchRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        query = serializer.validated_data["query"].strip()

        # The real two-stage funnel, cast wider and floored lower than the
        # injection path (top 10, floor 0.05): a search UI wants the
        # near-misses the prompt would rightly suppress.
        recall = retrieve(request.user, query, top_k=10, floor=0.05)
        items = [row_item(item.record, score=item.score) for item in recall.chosen]

        # USER.md lines never carry embeddings, so they join by token overlap.
        query_terms = set(tokenize(query))
        if query_terms:
            for item in profile_items(
                self._document(), pinned_records(self.request.user)
            ):
                line_terms = set(tokenize(item["content"]))
                overlap = len(query_terms & line_terms)
                if overlap:
                    matched = dict(item)
                    matched["score"] = round(min(1.0, overlap / len(query_terms)), 4)
                    items.append(matched)

        items.sort(key=lambda item: item.get("score", 0.0), reverse=True)
        payload = {"query": query, "items": items, "categories": []}
        return Response(MemorySearchResponseSerializer(payload).data)

    @action(detail=False, methods=["delete"])
    def clear(self, request):
        """Forget everything: archive, ledger, and USER.md.

        Conversations and their messages are NOT touched — the transcript is
        the user's actual chat history, not an extracted layer.
        """
        user = request.user
        with transaction.atomic():
            records, _ = MemoryRecord.objects.filter(user=user).delete()
            MemoryLedgerEntry.objects.filter(user=user).delete()
            UserMemoryDocument.objects.filter(user=user).update(content="")

        logger.info("[memory] user %s cleared their memory (%s rows)", user.id, records)
        payload = {
            "success": True,
            "message": "All memories deleted across every layer.",
        }
        return Response(ClearResponseSerializer(payload).data)

    # ------------------------------------------------------------- v2 ------
    # The layered surface the round-2 frontend consumes. Same scope rules.

    def _budget(self, markdown: str):
        return {
            "tokens": estimate_tokens(markdown),
            "limit": TOKEN_BUDGET,
            "warn_at": TOKEN_WARNING,
        }

    def document(self, request):
        """USER.md as a document: read it, or hand-edit it whole.

        Edits pass through the same normalizer as machine writes and are
        refused past the token budget — the ceiling holds for humans too,
        because the file is charged into every future prompt either way.
        """
        if request.method.lower() == "get":
            document = self._document()
            # The rendered profile, not the authored half: this is what a turn
            # actually carries, so it is what the budget has to be measured
            # against. A PUT still edits only the authored lines — pinned
            # facts are corrected by editing the fact.
            markdown = read_user_doc(request.user)
            updated = document.updated_at.isoformat() if document else None
            return Response(
                {
                    "markdown": markdown,
                    "updated_at": updated,
                    "budget": self._budget(markdown),
                }
            )

        markdown = request.data.get("markdown")
        if markdown is None or not str(markdown).strip():
            return Response(
                {"detail": "A markdown body is required. To erase, use clear/."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        normalized = normalize_user_doc(str(markdown))
        tokens = estimate_tokens(normalized)
        if tokens > TOKEN_BUDGET:
            return Response(
                {
                    "detail": (
                        f"USER.md would reach {tokens} tokens, past the "
                        f"{TOKEN_BUDGET} ceiling. Trim it before saving."
                    ),
                    "budget": self._budget(normalized),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        document, _ = UserMemoryDocument.objects.get_or_create(user=request.user)
        document.content = normalized
        document.save(update_fields=["content", "updated_at"])
        return Response(
            {
                "markdown": normalized,
                "updated_at": document.updated_at.isoformat(),
                "budget": self._budget(normalized),
            }
        )

    def consolidate(self, request):
        """The tidy-up sweep: GET to see what it would change, POST to approve one.

        Nothing is applied by a GET, and a POST applies exactly the proposal it
        was given. A sweep that tidied on its own would be a process quietly
        rewriting someone's memory — every rule in it is a judgement that will
        sometimes be wrong, so the person decides.
        """
        if request.method.lower() == "get":
            return Response(consolidation.propose(request.user))

        proposal = request.data or {}
        if not proposal.get("kind") or not proposal.get("record_id"):
            return Response(
                {"detail": "A proposal needs a kind and a record_id."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        result = consolidation.apply(request.user, proposal)
        if not result.ok:
            return Response(
                {"detail": result.reason}, status=status.HTTP_400_BAD_REQUEST
            )
        return Response({"detail": result.detail})

    def ledger(self, request):
        """The decision audit: every write, applied or refused, with the raw
        proposal alongside. The refusals are the half worth reading."""
        try:
            limit = min(max(int(request.query_params.get("limit", 100)), 1), 500)
        except (TypeError, ValueError):
            limit = 100

        newest_first = MemoryLedgerEntry.objects.filter(user=request.user).order_by(
            "-created_at"
        )[:limit]
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
            for entry in reversed(list(newest_first))
        ]
        return Response({"entries": entries})

    def hold(self, request):
        """Gate or release one memory by hand.

        Only active↔held transitions exist: a superseded row was retired by a
        newer fact, not gated, and "releasing" it would resurrect a
        contradiction."""
        record_id = request.data.get("id")
        held = request.data.get("held")
        if record_id is None or not isinstance(held, bool):
            return Response(
                {"detail": "Body must be {id, held: boolean}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        record = MemoryRecord.visible(request.user).filter(pk=record_id).first()
        if record is None:
            return Response(status=status.HTTP_404_NOT_FOUND)

        if record.state == MemoryState.SUPERSEDED:
            return Response(
                {
                    "detail": (
                        "A superseded memory cannot be held or released — it "
                        "was retired, not gated."
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )

        target_state = MemoryState.HELD if held else MemoryState.ACTIVE
        if record.state != target_state:
            with transaction.atomic():
                record.state = target_state
                record.save(update_fields=["state", "updated_at"])
                MemoryLedgerEntry.objects.create(
                    user=request.user,
                    action=WriterAction.HOLD if held else WriterAction.RELEASE,
                    proposed_action=(
                        WriterAction.HOLD if held else WriterAction.RELEASE
                    ),
                    reason=(
                        "The user gated this memory by hand."
                        if held
                        else "The user released this memory by hand."
                    ),
                    applied=True,
                    record=record,
                    detail=record.text,
                )

        return Response(MemoryItemSerializer(record_item(record)).data)

    def sessions(self, request):
        """The transcript layer, searchable from the Memory page.

        The same search the model reaches through the search_sessions tool,
        returned as clickable hits (conversation, date, exchange) rather than
        a flat transcript block. Words, a date range, or both — all bounds
        only ever narrow, and scope comes from request.user alone.
        """
        query = (request.query_params.get("q") or "").strip()[:2000]
        since = (request.query_params.get("since") or "").strip() or None
        until = (request.query_params.get("until") or "").strip() or None
        if not query and not since and not until:
            return Response(
                {"detail": "Pass ?q=<words>, ?since=YYYY-MM-DD, or both."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        result = search_sessions_hits(
            request.user, query=query, since=since, until=until
        )
        if not result.get("success"):
            # A malformed or reversed date bound is the CALLER's error, and it
            # must never degrade into an unbounded search of the whole
            # history — 400, loudly, with the reason.
            return Response(
                {"detail": result.get("error", "Search failed.")},
                status=(
                    status.HTTP_400_BAD_REQUEST
                    if result.get("bad_request")
                    else status.HTTP_502_BAD_GATEWAY
                ),
            )
        return Response(result)

    def export(self, request):
        """The whole store as one self-contained bundle — the layered
        contract, not a flat list, so an import can reinstate it exactly."""
        return Response(portability.export_bundle(request.user))

    def import_bundle(self, request):
        """Reinstate an exported bundle into an EMPTY store.

        Refuses a non-empty store rather than inventing merge semantics —
        the flow this serves (fresh account, or forget-then-restore) starts
        empty by construction.
        """
        try:
            result = portability.import_bundle(request.user, request.data)
        except portability.ImportError_ as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(result, status=status.HTTP_201_CREATED)

    def import_foreign(self, request):
        """Free-form paste from any other assistant, through the pipeline.

        Not a restore: the text goes through the writer and the gate like
        conversation turns, so it works against a full store — collisions
        supersede, safety pins, health is held, and the ledger records all
        of it. Returns as soon as the turns are queued.
        """
        try:
            result = portability.import_foreign(request.user, request.data.get("text"))
        except portability.ImportError_ as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(result, status=status.HTTP_202_ACCEPTED)

    def recall(self, request):
        """The probe: run the ranking by hand without spending a turn.

        Returns near-misses too, because those tell you whether the floor is
        set right."""
        query = (request.query_params.get("q") or "").strip()[:2000]
        if not query:
            return Response(
                {"detail": "Pass ?q=<query>."}, status=status.HTTP_400_BAD_REQUEST
            )
        summary = summarize_recall(retrieve(request.user, query), considered=12)
        return Response(summary)
