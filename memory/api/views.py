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

from memory.constants import WriterAction
from memory.domain.user_doc import parse_user_doc, render_user_doc
from memory.models import MemoryLedgerEntry, MemoryRecord, UserMemoryDocument
from memory.services.items import (
    DOC_ID_PREFIX,
    doc_line_id,
    listed_records,
    profile_items,
    record_item,
)

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
        items = profile_items(self._document())
        items.extend(record_item(record) for record in listed_records(request.user))
        return Response(MemoryItemSerializer(items, many=True).data)

    def retrieve(self, request, pk=None):
        if pk and pk.startswith(DOC_ID_PREFIX):
            for item in profile_items(self._document()):
                if item["id"] == pk:
                    return Response(MemoryItemSerializer(item).data)
            return Response(status=status.HTTP_404_NOT_FOUND)

        record = MemoryRecord.visible(request.user).filter(pk=pk).first()
        if record is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
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

        # Wired to the real two-stage retriever in the read-path phase; until
        # then semantic search reports no matches rather than erroring.
        payload = {"query": query, "items": [], "categories": []}
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
