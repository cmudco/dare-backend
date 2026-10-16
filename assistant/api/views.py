from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from assistant.api.serializers import (
    AssistantProposalSerializer,
    AssistantThreadSerializer,
    AssistantThreadSummarySerializer,
    ProposalActionIdsSerializer,
)
from assistant.services.proposal_service import (
    ProposalConflict,
    ProposalNotFound,
    apply_actions,
    discard_proposal,
    restore_proposal,
    undo_actions,
)
from assistant.services.thread_service import (
    AssistantThreadNotFound,
    open_thread,
    recent_messages,
    reopen_thread,
    start_new_thread,
    thread_history,
    usage,
)


def _thread_payload(thread, user) -> dict:
    return AssistantThreadSerializer(
        {
            "id": thread.id,
            "messages": recent_messages(thread),
            "usage": usage(user),
        }
    ).data


class AssistantThreadView(APIView):
    """The user's open assistant thread with its recent messages."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(_thread_payload(open_thread(request.user), request.user))


class AssistantNewThreadView(APIView):
    """Close the open thread and start an empty one ("New chat")."""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        thread = start_new_thread(request.user)
        return Response(
            _thread_payload(thread, request.user), status=status.HTTP_201_CREATED
        )


class AssistantThreadHistoryView(APIView):
    """The user's past conversations with the assistant, newest first."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(
            AssistantThreadSummarySerializer(
                thread_history(request.user), many=True
            ).data
        )


class AssistantOpenThreadView(APIView):
    """Switch to a past conversation; it becomes the open thread."""

    permission_classes = [IsAuthenticated]

    def post(self, request, thread_id: int):
        try:
            thread = reopen_thread(request.user, thread_id)
        except AssistantThreadNotFound:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(_thread_payload(thread, request.user))


class ProposalView(APIView):
    """Move one of the user's proposals between applied, pending and discarded."""

    permission_classes = [IsAuthenticated]
    takes_action_ids = False

    def change(self, user, proposal_id: int, action_ids):
        raise NotImplementedError

    def post(self, request, proposal_id: int):
        action_ids = None
        if self.takes_action_ids:
            body = ProposalActionIdsSerializer(data=request.data)
            body.is_valid(raise_exception=True)
            action_ids = body.validated_data.get("action_ids")
        try:
            proposal = self.change(request.user, proposal_id, action_ids)
        except ProposalNotFound:
            return Response(status=status.HTTP_404_NOT_FOUND)
        except ProposalConflict as conflict:
            return Response({"error": str(conflict)}, status=status.HTTP_409_CONFLICT)
        return Response(AssistantProposalSerializer(proposal).data)


class ApplyProposalView(ProposalView):
    takes_action_ids = True

    def change(self, user, proposal_id, action_ids):
        return apply_actions(user, proposal_id, action_ids)


class UndoProposalView(ProposalView):
    takes_action_ids = True

    def change(self, user, proposal_id, action_ids):
        return undo_actions(user, proposal_id, action_ids)


class DiscardProposalView(ProposalView):
    def change(self, user, proposal_id, action_ids):
        return discard_proposal(user, proposal_id)


class RestoreProposalView(ProposalView):
    def change(self, user, proposal_id, action_ids):
        return restore_proposal(user, proposal_id)
