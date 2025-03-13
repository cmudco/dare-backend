from django.db.models import Count
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from conversations.constants import SenderType
from conversations.models import Conversation, Message
from files.models import File
from prompts.models import Prompt


class UserStatsView(APIView):
    def get(self, request, *args, **kwargs):
        user = request.user

        prompt_count = Prompt.active_objects.filter(user=user).count()

        file_count = File.active_objects.filter(user=user).count()

        conversation_count = Conversation.active_objects.filter(user=user).count()

        message_count = Message.active_objects.filter(conversation__user=user).count()

        ai_message_count = Message.active_objects.filter(
            conversation__user=user,
            sender_type=SenderType.AI_ASSISTANT
        ).count()

        tagged_files_count = File.active_objects.filter(user=user, tags__isnull=False).count()

        stats = {
            'prompt_count': prompt_count,
            'file_count': file_count,
            'conversation_count': conversation_count,
            'message_count': message_count,
            'ai_message_count': ai_message_count,
            'tagged_files_count': tagged_files_count,
        }

        return Response(stats, status=status.HTTP_200_OK)