from rest_framework import viewsets, generics, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db import transaction
from conversations.models import Message, Conversation, LLM, Snippet
from .serializers import MessageSerializer, ConversationSerializer, LLMSerializer



class ConversationViewSet(viewsets.ModelViewSet):
    """Endpoint for listing, retrieving, creating and updating chat conversations."""
    serializer_class = ConversationSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = 'conversation_id'

    def get_queryset(self):
        return Conversation.active_objects.filter(user=self.request.user).order_by('sort_order', '-created_at')

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)
        if  self.request.user.default_prompt:
            serializer.instance.prompt = self.request.user.default_prompt
            serializer.instance.save()

    @action(detail=False, methods=['patch'], url_path='update-sort-order')
    def update_sort_order(self, request):
        """
        Update the sort order of multiple conversations.
        Expected payload: [{"conversation_id": "ABC123", "sort_order": 1}, ...]
        """
        try:
            updates = request.data
            if not isinstance(updates, list):
                return Response(
                    {"error": "Expected a list of conversation updates"},
                    status=status.HTTP_400_BAD_REQUEST
                )

            conversation_ids = [update.get('conversation_id') for update in updates]
            conversations = Conversation.active_objects.filter(
                user=request.user,
                conversation_id__in=conversation_ids
            )

            conversation_map = {conv.conversation_id: conv for conv in conversations}

            for update in updates:
                conversation_id = update.get('conversation_id')
                sort_order = update.get('sort_order')

                if conversation_id in conversation_map and sort_order is not None:
                    conversation_map[conversation_id].sort_order = sort_order
                    conversation_map[conversation_id].save(update_fields=['sort_order'])

            return Response(status=status.HTTP_204_NO_CONTENT)

        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )

    @action(detail=False, methods=['post'], url_path='bulk-delete')
    def bulk_delete(self, request):
        """
        Bulk delete multiple conversations using DRF's built-in delete method for each conversation.
        Expected payload: {"conversation_ids": ["ABC123", "DEF456", ...]}
        """
        conversation_ids = request.data.get('conversation_ids', [])

        if not conversation_ids:
            return Response({"error": "No conversation IDs provided."}, status=status.HTTP_400_BAD_REQUEST)

        if not isinstance(conversation_ids, list):
            return Response({"error": "conversation_ids must be a list."}, status=status.HTTP_400_BAD_REQUEST)

        conversations = Conversation.active_objects.filter(
            conversation_id__in=conversation_ids,
            user=request.user
        )

        if not conversations.exists():
            return Response({"error": "No valid conversations found to delete."}, status=status.HTTP_404_NOT_FOUND)

        deleted_conversations = []
        failed_conversations = []

        for conversation in conversations:
            try:
                conversation_data = {"conversation_id": conversation.conversation_id, "title": conversation.title}
                self.perform_destroy(conversation)
                deleted_conversations.append(conversation_data)
            except Exception as e:
                import logging
                logger = logging.getLogger(__name__)
                logger.error(f"Error deleting conversation ID {conversation.conversation_id}: {str(e)}")
                failed_conversations.append({"conversation_id": conversation.conversation_id, "error": str(e)})

        response_data = {
            "status": "Bulk delete completed",
            "deleted_count": len(deleted_conversations),
            "failed_count": len(failed_conversations),
            "requested_count": len(conversation_ids)
        }

        if failed_conversations:
            response_data["failed_conversations"] = failed_conversations

        return Response(response_data, status=status.HTTP_200_OK)

    @action(detail=True, methods=['post'], url_path='clone')
    def clone_conversation(self, request, conversation_id=None):
        """
        Clone a conversation with all its messages, files, tags, and snippets.
        Simply clones everything - no options needed.
        """
        try:
            with transaction.atomic():
                instance = self.get_object()

                # Use the model's built-in clone method
                cloned_conversation = instance.clone()

                # Prepare response data
                serializer = self.get_serializer(cloned_conversation)
                return Response(serializer.data, status=status.HTTP_201_CREATED)

        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Error cloning conversation {conversation_id}: {str(e)}")
            return Response(
                {"error": f"Failed to clone conversation: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class MessageViewSet(viewsets.ModelViewSet):
    """Endpoint for creating/retrieving messages within a conversation."""
    serializer_class = MessageSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Message.active_objects.filter(conversation__user=self.request.user)

    def get_serializer_context(self):
        context = super().get_serializer_context()
        return context

class LLMViewSet(viewsets.ModelViewSet):
    """Endpoint for listing available LLM models."""
    serializer_class = LLMSerializer
    permission_classes = [IsAuthenticated]
    queryset = LLM.objects.all().order_by('name')

    def get_queryset(self):
        """
        Filter LLM models based on user's model group.
        If user has no model group assigned, return all models.
        """
        user = self.request.user

        if not user.model_group:
            return LLM.objects.all().order_by('name')
        if not user.model_group.is_active:
            return LLM.objects.all().order_by('name')

        return user.model_group.allowed_models.all().order_by('name')

    @action(detail=False, methods=['get'])
    def all_models(self, request):
        """
        Return all LLM models without filtering by user's model group.
        This is used for displaying model names in historical conversations.
        """
        queryset = LLM.objects.all().order_by('name')
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

