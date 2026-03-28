"""
Internal API views for service-to-service communication (SocraticBots <-> DARE).

These views use X-Internal-Key authentication for trusted backend-to-backend calls
where JWT authentication is not appropriate (e.g., viewing student data on behalf of professors).
"""

import logging

from django.conf import settings
from django.db.models import Count
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from conversations.models import Conversation, Message

logger = logging.getLogger(__name__)


class InternalUserConversationsView(APIView):
    """
    Internal service endpoint to fetch conversations for a specific user.

    Used by SocraticBots backend to display student conversations in Bot Access Management.
    Authenticates via X-Internal-Key header (service-to-service communication).

    Query params:
        user_id (required): DARE user ID to fetch conversations for
        bot_id (optional): Filter by specific bot ID

    Returns:
        List of conversations with id, title, createdAt, messageCount, botId
    """
    permission_classes = [AllowAny]

    def get(self, request):
        # Verify internal key
        internal_key = request.headers.get('X-Internal-Key', '')
        expected_key = getattr(settings, 'DARE_INTERNAL_KEY', '')
        if not internal_key or internal_key != expected_key:
            return Response(
                {'error': 'Unauthorized'},
                status=status.HTTP_403_FORBIDDEN
            )

        # Get required user_id param
        user_id = request.query_params.get('user_id')
        if not user_id:
            return Response(
                {'error': 'user_id query parameter is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            user_id = int(user_id)
        except ValueError:
            return Response(
                {'error': 'user_id must be an integer'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Build queryset for SocraticBots conversations
        queryset = Conversation.active_objects.filter(
            user_id=user_id,
            source='SocraticBots'
        ).select_related('user').order_by('-created_at')

        # Optional bot_id filter
        bot_id = request.query_params.get('bot_id')
        if bot_id:
            try:
                bot_id = int(bot_id)
                queryset = queryset.filter(bot_id=bot_id)
            except ValueError:
                return Response(
                    {'error': 'bot_id must be an integer'},
                    status=status.HTTP_400_BAD_REQUEST
                )

        # Build response data
        conversations = []
        for conv in queryset:
            conversations.append({
                'conversation_id': conv.conversation_id,
                'title': conv.title,
                'created_at': conv.created_at.isoformat(),
                'message_count': Message.active_objects.filter(conversation=conv).count(),
                'bot_id': conv.bot_id,
            })

        return Response({
            'conversations': conversations,
            'total_count': len(conversations),
        })


class InternalConversationMessagesView(APIView):
    """
    Internal API endpoint to fetch messages for a specific conversation.
    Used by SocraticBots backend for professor read-only view of student conversations.

    Authentication: X-Internal-Key header (service-to-service)

    Args:
        conversation_id: DARE conversation ID (path parameter)

    Returns:
        List of messages with role, content, createdAt
    """
    permission_classes = [AllowAny]

    def get(self, request, conversation_id):
        # Verify internal key
        internal_key = request.headers.get('X-Internal-Key', '')
        expected_key = getattr(settings, 'DARE_INTERNAL_KEY', '')
        if not internal_key or internal_key != expected_key:
            return Response(
                {'error': 'Unauthorized'},
                status=status.HTTP_403_FORBIDDEN
            )

        # Get conversation
        try:
            conversation = Conversation.active_objects.get(conversation_id=conversation_id)
        except Conversation.DoesNotExist:
            return Response(
                {'error': 'Conversation not found'},
                status=status.HTTP_404_NOT_FOUND
            )

        # Get messages
        messages = Message.active_objects.filter(
            conversation=conversation
        ).order_by('created_at')

        # SenderType: 1 = PLAYER (user), 2 = AI_ASSISTANT
        message_list = []
        for msg in messages:
            role = 'user' if msg.sender_type == 1 else 'assistant'
            message_list.append({
                'message_id': str(msg.id),
                'role': role,
                'content': msg.message,
                'created_at': msg.created_at.isoformat(),
            })

        return Response({
            'conversation_id': conversation.conversation_id,
            'title': conversation.title,
            'messages': message_list,
            'total_count': len(message_list),
        })
