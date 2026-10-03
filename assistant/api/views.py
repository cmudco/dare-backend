from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from assistant.api.serializers import (
    AssistantThreadSerializer,
    FileOrganizationProposalSerializer,
)
from assistant.services.proposal_service import (
    ProposalAlreadyDecided,
    ProposalNotFound,
    apply_proposal,
    discard_proposal,
)
from assistant.services.thread_service import (
    open_thread,
    recent_messages,
    start_new_thread,
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


class ProposalDecisionView(APIView):
    """Apply or discard one of the user's file-organisation proposals."""

    permission_classes = [IsAuthenticated]
    decide = None

    def post(self, request, proposal_id: int):
        try:
            proposal = type(self).decide(request.user, proposal_id)
        except ProposalNotFound:
            return Response(status=status.HTTP_404_NOT_FOUND)
        except ProposalAlreadyDecided:
            return Response(
                {"error": "This proposal was already applied or discarded."},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(FileOrganizationProposalSerializer(proposal).data)


class ApplyProposalView(ProposalDecisionView):
    decide = staticmethod(apply_proposal)


class DiscardProposalView(ProposalDecisionView):
    decide = staticmethod(discard_proposal)
