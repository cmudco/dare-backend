from rest_framework import viewsets, generics
from rest_framework.permissions import IsAuthenticated
from conversations.models import Message, Conversation, LLM
from .serializers import MessageSerializer, ConversationSerializer, LLMSerializer



class ConversationViewSet(viewsets.ModelViewSet):
    """Endpoint for listing, retrieving, creating and updating chat conversations."""
    serializer_class = ConversationSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = 'conversation_id'

    def get_queryset(self):
        return Conversation.active_objects.filter(user=self.request.user).order_by('-created_at')

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

class MessageViewSet(viewsets.ModelViewSet):
    """Endpoint for creating/retrieving messages within a conversation."""
    serializer_class = MessageSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Message.active_objects.filter(conversation_user=self.request.user)

    def get_serializer_context(self):
        context = super().get_serializer_context()
        return context

class LLMViewSet(viewsets.ModelViewSet):
    """Endpoint for listing available LLM models."""
    serializer_class = LLMSerializer
    permission_classes = [IsAuthenticated]
    queryset = LLM.objects.all().order_by('name')
